using System.Text;
using System.Text.Json;
using System.Security.Cryptography;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse_Conversion.Dto;
using CUE4Parse_Conversion.Options;
using CUE4Parse_Conversion.Textures;
using SkiaSharp;

namespace Palx;

public static class WaterExtract
{
    const int MAXTEX = 512;

    // Engine / "no texture plugged in here" placeholders. Picking one of these
    // would paint a piece flat black or flat white and look like a real result.
    static readonly string[] Fallbacks = {
        "DefaultBaseTexture", "DefaultNormalMap", "DefaultMaskMap", "DefaultTangent",
        "Engine/Content", "T_Black", "T_White", "T_DefaultMaskTexture", "Black.Black",
        "DefaultDiffuse", "DefaultTexture", "T_Gray",
    };
    static bool IsFallback(string path) =>
        path == null || Fallbacks.Any(f => path.Contains(f, StringComparison.OrdinalIgnoreCase));

    public class TexRec { public string param; public string path; public string png; public int w, h; public string fmt; }
    public class MatRec { public int slot; public string slotName; public string material; public string materialPath; public string blend; public TexRec baseColor; public string note; }
    public class Rec
    {
        public string name, meshPath, kind, err, glb;
        public bool ok;
        public int verts, tris, uvSets, prims;
        public bool nanite, hasUv;
        public float[] bbox_cm, min_cm, max_cm;
        public List<MatRec> mats = new();
    }

    // texture asset path -> emitted png filename (dedup across every mesh)
    static readonly Dictionary<string, string> TexCache = new(StringComparer.OrdinalIgnoreCase);

    public static int Run(string[] args)
    {
        var outDir = args.Length > 1 ? args[1] : Path.Combine(Program.BaseDir, "meshes_uv");
        var texDir = Path.Combine(outDir, "tex");
        Directory.CreateDirectory(texDir);

        var provider = Level.MakeProvider(Environment.GetEnvironmentVariable("PALX_ROOT")
            ?? Path.Combine(Program.BaseDir, "pakroot"));
        Console.Error.WriteLine($"mounted files={provider.Files.Count}");

        // Which manifest to walk. Same schema for architecture (mesh_manifest)
        // and Pals (pal_manifest), so one extractor serves both.
        var manifestFile = args.Length > 2 ? args[2] : Path.Combine(Program.BaseDir, "mesh_manifest.json");
        var manifest = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(
            File.ReadAllText(manifestFile));
        Console.Error.WriteLine($"manifest={manifestFile}");
        // name -> (game path, explicit base-colour parameter).
        //
        // WHY THE EXPLICIT PARAMETER: the shared extractor picks a base colour by
        // ASSET-NAME convention (T_*_B / _BC / _D ...). Palworld's water shaders
        // have no such map at all - a waterfall's surface is a scrolling noise
        // texture fed through an unlit translucent graph - so name-matching
        // rightly finds nothing and the mesh comes out untextured. `texParam`
        // names the sampler the material itself declares (e.g. Texture02 ->
        // T_Water04), so the colour still comes from the material's own
        // parameter list, not from a guess. `noTexture` means "this surface
        // genuinely has no colour map"; its colour comes from the material's
        // own vector parameters instead (see build_water.py).
        var targets = new Dictionary<string, (string path, string texParam, bool noTex, float? aMin, float? aMax)>();
        foreach (var (key, e) in manifest)
        {
            if (!e.TryGetProperty("meshPath", out var mp)) continue;
            var gp = mp.GetString();
            if (string.IsNullOrEmpty(gp)) continue;
            var tp = e.TryGetProperty("texParam", out var t) ? t.GetString() : null;
            var nt = e.TryGetProperty("noTexture", out var n) && n.GetBoolean();
            float? amin = e.TryGetProperty("alphaMin", out var a0) ? a0.GetSingle() : null;
            float? amax = e.TryGetProperty("alphaMax", out var a1) ? a1.GetSingle() : null;
            targets[key] = (gp, tp, nt, amin, amax);
        }
        Console.Error.WriteLine($"targets={targets.Count}");

        var results = new List<Rec>();
        int i = 0;
        foreach (var (name, tg) in targets.OrderBy(k => k.Key))
        {
            if (++i % 25 == 0) Console.Error.WriteLine($"  {i}/{targets.Count}");
            var r = new Rec { name = name, meshPath = tg.path };
            ForceParam = tg.texParam; NoTexture = tg.noTex; AlphaMin = tg.aMin; AlphaMax = tg.aMax;
            try { One(provider, r, outDir, texDir); }
            catch (Exception ex) { r.err = ex.GetType().Name + ": " + ex.Message; }
            results.Add(r);
            Console.WriteLine(r.ok
                ? $"OK   {r.name,-46} {r.verts,7}v {r.tris,7}t uv={r.hasUv,-5} prims={r.prims} tex={r.mats.Count(m => m.baseColor != null)}/{r.mats.Count}"
                : $"FAIL {r.name,-46} {r.err}");
            if (r.ok) foreach (var m in r.mats.Where(m => m.baseColor == null)) Console.WriteLine($"       slot{m.slot} mat={m.material} :: {m.note}");
        }

        File.WriteAllText(Path.Combine(Program.BaseDir, Path.GetFileNameWithoutExtension(manifestFile) + "_uv_report.json"),
            JsonSerializer.Serialize(results, new JsonSerializerOptions { WriteIndented = true, IncludeFields = true }));
        Console.WriteLine($"done ok={results.Count(x => x.ok)}/{results.Count} distinct-textures={TexCache.Values.Distinct().Count()}");
        return 0;
    }

    /// Set per target by Run(); read by the material pass below.
    static string ForceParam;
    static bool NoTexture;
    /// The waterfall shader derives its OPACITY from the same greyscale map it
    /// derives its colour from: saturate((L - RGBMaskMin) / (RGBMaskMax -
    /// RGBMaskMin)). Those two scalars ship on the MaterialInstanceConstant, so
    /// baking exactly that curve into the emitted texture's alpha channel is
    /// the material's own opacity, not a hand-tuned one. Null = leave alone.
    static float? AlphaMin, AlphaMax;

    static void One(CUE4Parse.FileProvider.DefaultFileProvider provider, Rec r, string outDir, string texDir)
    {
        var leaf = "/" + r.meshPath[(r.meshPath.LastIndexOf('/') + 1)..] + ".uasset";
        var vpath = "Pal/Content/" + r.meshPath.Substring("/Game/".Length) + ".uasset";
        if (!provider.Files.ContainsKey(vpath))
            vpath = provider.Files.Keys.FirstOrDefault(k => k.EndsWith(leaf, StringComparison.OrdinalIgnoreCase));
        if (vpath == null) { r.err = "asset not found"; return; }

        var pkg = provider.LoadPackage(vpath);
        var sk = pkg.GetExports().OfType<USkeletalMesh>().FirstOrDefault();
        var sm = pkg.GetExports().OfType<UStaticMesh>().FirstOrDefault();
        r.kind = sk != null ? "SK" : sm != null ? "SM" : null;
        if (r.kind == null) { r.err = "no mesh export"; return; }

        float[] pos, uv; uint[] idx; MeshSectionDto[] sections;
        // Material slots read from the mesh export itself, in slot order.
        var mats = sk != null
            ? sk.SkeletalMaterials.Select(m => m.Material).ToArray()
            : sm.StaticMaterials.Select(m => m.MaterialInterface).ToArray();

        if (r.kind == "SK")
        {
            using var dto = new SkeletalMeshDto(sk, EMeshQuality.All, ENaniteMeshFormat.NoNanite, false);
            if (dto.LODs.Count == 0) { r.err = "no LODs"; return; }
            var lod = dto.LODs[0];
            r.nanite = lod.IsNanite;
            pos = Flat3(lod.Vertices.Select(v => v.Position).ToArray());
            uv = Flat2(lod.Vertices.Select(v => v.Uv).ToArray());
            // COPY out of the dto: its buffers are pooled and Dispose() reclaims
            // them at the end of this block.
            idx = lod.Indices.ToArray(); sections = lod.Sections.ToArray();
        }
        else
        {
            using var dto = new StaticMeshDto(sm, EMeshQuality.All, ENaniteMeshFormat.NaniteLast);
            if (dto.LODs.Count == 0) { r.err = "no LODs"; return; }
            var lod = dto.LODs[0];
            r.nanite = lod.IsNanite;
            pos = Flat3(lod.Vertices.Select(v => v.Position).ToArray());
            uv = Flat2(lod.Vertices.Select(v => v.Uv).ToArray());
            idx = lod.Indices.ToArray(); sections = lod.Sections.ToArray();
        }
        if (pos.Length == 0 || idx.Length == 0) { r.err = "empty geometry"; return; }

        r.verts = pos.Length / 3; r.tris = idx.Length / 3;
        r.hasUv = uv.Any(v => v != 0f);
        r.bbox_cm = BBox(pos); r.min_cm = MinV(pos); r.max_cm = MaxV(pos);

        // --- materials -> base-colour texture ---
        for (int s = 0; s < mats.Length; s++)
        {
            var mr = new MatRec { slot = s };
            var mi = mats[s];
            mr.material = mi?.Name;
            mr.materialPath = mi?.ResolvedObject?.GetPathName();
            try
            {
                if (mi != null && mi.TryLoad(out var mo) && mo is UMaterialInterface umat)
                {
                    var p = new CMaterialParams2();
                    umat.GetParams(p, EMaterialDepth.AllLayersNoRef);
                    mr.blend = p.BlendMode.ToString();
                    var pick = PickBaseColor(p);
                    // Explicit sampler wins: it is the material's OWN declared
                    // parameter, read out of the cooked instance.
                    if (NoTexture) pick = (null, null);
                    else if (ForceParam != null && p.Textures.TryGetValue(ForceParam, out var ft)
                             && ft is UTexture fut)
                        pick = (fut, ForceParam);
                    // Strictly additive fallback: a handful of Palworld materials
                    // (MI_egg00 is the one that matters here) expose ZERO texture
                    // parameters because the base material samples its maps
                    // directly rather than through named parameters. Those
                    // samplers still survive cooking in the material's referenced
                    // texture list, so use it when — and only when — the
                    // parameter path found nothing.
                    if (!NoTexture && pick.tex == null && p.Textures.Count == 0)
                        pick = PickFromReferenced(umat);
                    if (pick.tex != null)
                    {
                        var png = EmitTexture(pick.tex, texDir);
                        if (png.Item1 != null)
                            mr.baseColor = new TexRec { param = pick.param, path = pick.tex.GetPathName(), png = png.Item1, w = png.Item2, h = png.Item3, fmt = png.Item4 };
                        else mr.note = "decode failed";
                    }
                    else mr.note = "no non-fallback base-colour texture among " + p.Textures.Count + " params";
                }
                else mr.note = "material did not load";
            }
            catch (Exception ex) { mr.note = "mat error: " + ex.Message; }
            r.mats.Add(mr);
        }

        Directory.CreateDirectory(outDir);
        var glbPath = Path.Combine(outDir, r.name + ".glb");
        r.prims = WriteGlb(glbPath, pos, uv, idx, sections, r.mats, r.name);
        r.glb = Path.GetRelativePath(Program.BaseDir, glbPath);
        r.ok = true;
    }

    // Suffixes that mean "this is not the colour map": normal, mask/metal,
    // roughness, ambient occlusion, emissive, height/displacement.
    static readonly System.Text.RegularExpressions.Regex NotBaseColor = new(
        @"(_N|_NRM|_Normal|_M|_MSK|_Mask|_R|_Rough|_ORM|_AO|_E|_Emissive|_H|_Height|_Disp)$",
        System.Text.RegularExpressions.RegexOptions.IgnoreCase);

    /// Last-resort base colour: the material's own referenced-texture list.
    /// Only ever consulted when the material declared no texture parameters at
    /// all, so it cannot change what a parameterised material resolves to.
    static (UTexture tex, string param) PickFromReferenced(UMaterialInterface umat)
    {
        var refs = new List<UUnrealMaterial>();
        try { umat.AppendReferencedTextures(refs, true); } catch { return (null, null); }
        // Same folder as the material only. A material's own colour map ships
        // beside it (Others/egg/MI_egg00 -> Others/egg/T_EggA00); the shared
        // effect maps a material also samples (caustics, noise, gradients) live
        // elsewhere and are not what the surface looks like.
        var dir = umat.GetPathName();
        dir = dir[..(dir.LastIndexOf('/') + 1)];
        var cands = refs.OfType<UTexture>()
            .Where(t => !IsFallback(t.GetPathName()))
            .Where(t => t.GetPathName().StartsWith(dir, StringComparison.OrdinalIgnoreCase))
            .Where(t => !NotBaseColor.IsMatch(t.Name))
            .ToList();
        if (cands.Count == 0) return (null, null);
        // prefer an explicitly colour-named map, else the first one the material
        // actually renders with (UE lists them in sampler order)
        var named = cands.FirstOrDefault(t =>
            t.Name.Contains("Color", StringComparison.OrdinalIgnoreCase) ||
            t.Name.Contains("Albedo", StringComparison.OrdinalIgnoreCase) ||
            t.Name.Contains("BaseColor", StringComparison.OrdinalIgnoreCase));
        return (named ?? cands[0], "ReferencedTextures");
    }

    static (UTexture tex, string param) PickBaseColor(CMaterialParams2 p)
    {
        // 1. the material's own declared diffuse parameter names
        if (p.TryGetTexture2d(out var t, CMaterialParams2.Diffuse[0]) && !IsFallback(t.GetPathName()))
            return (t, "Diffuse[0]");
        // 2. anything whose ASSET name reads like a base-colour map. Palworld's
        //    architecture set names them T_<Thing>_B; _BC/_D/_C/Albedo cover the rest.
        var re = new System.Text.RegularExpressions.Regex(
            @"(_B|_BC|_D|_C|_BaseColor|BaseColor|_Albedo|Albedo|_Diffuse|Diffuse)$",
            System.Text.RegularExpressions.RegexOptions.IgnoreCase);
        foreach (var kv in p.Textures)
        {
            if (kv.Value is not UTexture ut) continue;
            var path = ut.GetPathName();
            if (IsFallback(path)) continue;
            if (re.IsMatch(ut.Name)) return (ut, kv.Key);
        }
        return (null, null);
    }

    /// PNG for one texture, deduped by asset path. -> (filename, w, h, srcFormat)
    static (string, int, int, string) EmitTexture(UTexture tex, string texDir)
    {
        var path = tex.GetPathName() + (AlphaMin.HasValue ? $"|a{AlphaMin}-{AlphaMax}" : "");
        if (TexCache.TryGetValue(path, out var cached))
        {
            var fi = new FileInfo(Path.Combine(texDir, cached));
            using var bm0 = SKBitmap.Decode(fi.FullName);
            return (cached, bm0?.Width ?? 0, bm0?.Height ?? 0, "cached");
        }
        var dec = tex.Decode(MAXTEX);
        if (dec == null) return default;
        using var bmp = dec.ToSkBitmap();
        if (bmp == null) return default;

        SKBitmap use = bmp;
        SKBitmap scaled = null;
        int max = Math.Max(bmp.Width, bmp.Height);
        if (max > MAXTEX)
        {
            var f = (float)MAXTEX / max;
            scaled = bmp.Resize(new SKImageInfo(Math.Max(1, (int)(bmp.Width * f)), Math.Max(1, (int)(bmp.Height * f))), SKFilterQuality.High);
            if (scaled != null) use = scaled;
        }
        // Alpha decides the container. Foliage//fence cutouts genuinely need the
        // alpha channel, so those stay PNG; every opaque architecture map goes
        // out as JPEG, which is ~8x smaller and is a first-class glTF image type
        // (no extension needed, unlike WebP). Straight PNG for everything cost
        // ~230 KB per texture, which does not fit a browser payload.
        // Bake the material's own opacity curve into the alpha channel.
        if (AlphaMin.HasValue && AlphaMax.HasValue)
        {
            var baked = new SKBitmap(new SKImageInfo(use.Width, use.Height,
                SKColorType.Rgba8888, SKAlphaType.Unpremul));
            var src = use.Pixels;
            var dst = new SKColor[src.Length];
            float lo = AlphaMin.Value, hi = AlphaMax.Value, span = MathF.Max(1e-6f, hi - lo);
            for (int k = 0; k < src.Length; k++)
            {
                var c = src[k];
                float lum = (0.2126f * c.Red + 0.7152f * c.Green + 0.0722f * c.Blue) / 255f;
                float a = Math.Clamp((lum - lo) / span, 0f, 1f);
                dst[k] = new SKColor(c.Red, c.Green, c.Blue, (byte)MathF.Round(a * 255f));
            }
            baked.Pixels = dst;
            scaled?.Dispose();
            use = baked; scaled = baked;
        }
        bool hasAlpha = AlphaMin.HasValue;
        if (!hasAlpha && !use.Info.IsOpaque)
        {
            var px = use.Pixels;
            for (int k = 0; k < px.Length; k += Math.Max(1, px.Length / 4096))
                if (px[k].Alpha < 250) { hasAlpha = true; break; }
        }
        var hash = Convert.ToHexString(MD5.HashData(Encoding.UTF8.GetBytes(path)))[..8].ToLowerInvariant();
        var fname = $"{tex.Name}_{hash}" + (hasAlpha ? ".png" : ".jpg");
        using (var data = hasAlpha
                   ? use.Encode(SKEncodedImageFormat.Png, 90)
                   : use.Encode(SKEncodedImageFormat.Jpeg, 82))
        using (var fs = File.Create(Path.Combine(texDir, fname)))
            data.SaveTo(fs);
        int w = use.Width, h = use.Height;
        scaled?.Dispose();
        TexCache[path] = fname;
        return (fname, w, h, dec.PixelFormat.ToString());
    }

    // ---------- GLB ----------
    static int WriteGlb(string path, float[] pos, float[] uv, uint[] idx,
                        MeshSectionDto[] sections, List<MatRec> mats, string name)
    {
        var nrm = Normals(pos, idx);
        int nv = pos.Length / 3;
        bool u32 = nv > 65535;

        var pb = new byte[pos.Length * 4]; Buffer.BlockCopy(pos, 0, pb, 0, pb.Length);
        var nb = new byte[nrm.Length * 4]; Buffer.BlockCopy(nrm, 0, nb, 0, nb.Length);
        var tb = new byte[uv.Length * 4]; Buffer.BlockCopy(uv, 0, tb, 0, tb.Length);
        byte[] ib;
        if (u32) { ib = new byte[idx.Length * 4]; Buffer.BlockCopy(idx, 0, ib, 0, ib.Length); }
        else
        {
            var sh = new ushort[idx.Length];
            for (int i = 0; i < idx.Length; i++) sh[i] = (ushort)idx[i];
            ib = new byte[sh.Length * 2]; Buffer.BlockCopy(sh, 0, ib, 0, ib.Length);
        }
        int ibPad = ib.Length % 4 == 0 ? 0 : 4 - ib.Length % 4;

        var lo = MinV(pos); var hi = MaxV(pos);
        int oP = 0, oN = pb.Length, oT = oN + nb.Length, oI = oT + tb.Length;
        var bin = new byte[oI + ib.Length + ibPad];
        Buffer.BlockCopy(pb, 0, bin, oP, pb.Length);
        Buffer.BlockCopy(nb, 0, bin, oN, nb.Length);
        Buffer.BlockCopy(tb, 0, bin, oT, tb.Length);
        Buffer.BlockCopy(ib, 0, bin, oI, ib.Length);

        string F(float v) => v.ToString("R", System.Globalization.CultureInfo.InvariantCulture);
        string J(string s) => s.Replace("\\", "").Replace("\"", "");

        // Valid sections only; fall back to one whole-mesh primitive.
        var secs = sections?.Where(s => s.NumFaces > 0 && s.FirstIndex >= 0 &&
                                        s.FirstIndex + s.NumFaces * 3 <= idx.Length).ToList()
                   ?? new List<MeshSectionDto>();
        if (secs.Count == 0) secs = new List<MeshSectionDto> { new MeshSectionDto(0, 0, idx.Length / 3, true) };

        // one glTF image/texture/material per distinct PNG actually used
        var pngs = new List<string>();
        var matIdxOf = new Dictionary<int, int>();      // section material slot -> glTF material index
        var accessors = new StringBuilder();
        var prims = new StringBuilder();
        var bufViews = new StringBuilder();

        for (int s = 0; s < mats.Count; s++)
        {
            var png = mats[s].baseColor?.png;
            if (png == null) continue;
            int ti = pngs.IndexOf(png);
            if (ti < 0) { pngs.Add(png); ti = pngs.Count - 1; }
            matIdxOf[s] = ti;
        }

        bufViews.Append($"{{\"buffer\":0,\"byteOffset\":{oP},\"byteLength\":{pb.Length},\"target\":34962}},");
        bufViews.Append($"{{\"buffer\":0,\"byteOffset\":{oN},\"byteLength\":{nb.Length},\"target\":34962}},");
        bufViews.Append($"{{\"buffer\":0,\"byteOffset\":{oT},\"byteLength\":{tb.Length},\"target\":34962}},");
        bufViews.Append($"{{\"buffer\":0,\"byteOffset\":{oI},\"byteLength\":{ib.Length},\"target\":34963}}");

        accessors.Append($"{{\"bufferView\":0,\"componentType\":5126,\"count\":{nv},\"type\":\"VEC3\",\"min\":[{F(lo[0])},{F(lo[1])},{F(lo[2])}],\"max\":[{F(hi[0])},{F(hi[1])},{F(hi[2])}]}},");
        accessors.Append($"{{\"bufferView\":1,\"componentType\":5126,\"count\":{nv},\"type\":\"VEC3\"}},");
        accessors.Append($"{{\"bufferView\":2,\"componentType\":5126,\"count\":{nv},\"type\":\"VEC2\"}}");

        int acc = 3;
        int compSize = u32 ? 4 : 2;
        foreach (var s in secs)
        {
            accessors.Append($",{{\"bufferView\":3,\"byteOffset\":{s.FirstIndex * compSize},\"componentType\":{(u32 ? 5125 : 5123)},\"count\":{s.NumFaces * 3},\"type\":\"SCALAR\"}}");
            var attrs = "\"POSITION\":0,\"NORMAL\":1,\"TEXCOORD_0\":2";
            if (prims.Length > 0) prims.Append(',');
            prims.Append($"{{\"attributes\":{{{attrs}}},\"indices\":{acc}");
            if (matIdxOf.TryGetValue(s.MaterialIndex, out var gm)) prims.Append($",\"material\":{gm}");
            prims.Append('}');
            acc++;
        }

        var sb = new StringBuilder();
        sb.Append("{\"asset\":{\"version\":\"2.0\",\"generator\":\"mappal-palxtex\"},\"scene\":0,\"scenes\":[{\"nodes\":[0]}],");
        sb.Append($"\"nodes\":[{{\"mesh\":0,\"name\":\"{J(name)}\"}}],");
        sb.Append($"\"meshes\":[{{\"name\":\"{J(name)}\",\"primitives\":[{prims}]}}],");
        if (pngs.Count > 0)
        {
            sb.Append("\"images\":[");
            for (int k = 0; k < pngs.Count; k++) sb.Append((k > 0 ? "," : "") + $"{{\"uri\":\"tex/{J(pngs[k])}\"}}");
            sb.Append("],\"samplers\":[{\"wrapS\":10497,\"wrapT\":10497,\"magFilter\":9729,\"minFilter\":9987}],\"textures\":[");
            for (int k = 0; k < pngs.Count; k++) sb.Append((k > 0 ? "," : "") + $"{{\"source\":{k},\"sampler\":0}}");
            sb.Append("],\"materials\":[");
            for (int k = 0; k < pngs.Count; k++)
                sb.Append((k > 0 ? "," : "") + $"{{\"name\":\"m{k}\",\"doubleSided\":true,\"pbrMetallicRoughness\":{{\"baseColorTexture\":{{\"index\":{k}}},\"metallicFactor\":0,\"roughnessFactor\":1}}}}");
            sb.Append("],");
        }
        sb.Append($"\"buffers\":[{{\"byteLength\":{bin.Length}}}],");
        sb.Append($"\"bufferViews\":[{bufViews}],\"accessors\":[{accessors}]}}");

        var js = Encoding.UTF8.GetBytes(sb.ToString());
        if (js.Length % 4 != 0)
        {
            var pad = 4 - js.Length % 4; var t2 = new byte[js.Length + pad];
            js.CopyTo(t2, 0); for (int k = js.Length; k < t2.Length; k++) t2[k] = 0x20; js = t2;
        }
        using var fs2 = File.Create(path);
        using var w2 = new BinaryWriter(fs2);
        w2.Write(Encoding.ASCII.GetBytes("glTF")); w2.Write(2u);
        w2.Write((uint)(12 + 8 + js.Length + 8 + bin.Length));
        w2.Write((uint)js.Length); w2.Write(0x4E4F534Au); w2.Write(js);
        w2.Write((uint)bin.Length); w2.Write(0x004E4942u); w2.Write(bin);
        return secs.Count;
    }

    static float[] Flat3(CUE4Parse.UE4.Objects.Core.Math.FVector[] v)
    {
        var o = new float[v.Length * 3];
        for (int i = 0; i < v.Length; i++) { o[i * 3] = v[i].X; o[i * 3 + 1] = v[i].Y; o[i * 3 + 2] = v[i].Z; }
        return o;
    }
    static float[] Flat2(CUE4Parse.UE4.Objects.Meshes.FMeshUVFloat[] v)
    {
        var o = new float[v.Length * 2];
        for (int i = 0; i < v.Length; i++) { o[i * 2] = v[i].U; o[i * 2 + 1] = v[i].V; }
        return o;
    }
    static float[] MinV(float[] p)
    { var m = new[] { float.MaxValue, float.MaxValue, float.MaxValue };
      for (int i = 0; i < p.Length; i += 3) for (int k = 0; k < 3; k++) if (p[i + k] < m[k]) m[k] = p[i + k]; return m; }
    static float[] MaxV(float[] p)
    { var m = new[] { float.MinValue, float.MinValue, float.MinValue };
      for (int i = 0; i < p.Length; i += 3) for (int k = 0; k < 3; k++) if (p[i + k] > m[k]) m[k] = p[i + k]; return m; }
    static float[] BBox(float[] p)
    { var lo = MinV(p); var hi = MaxV(p); return new[] { hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2] }; }

    static float[] Normals(float[] p, uint[] idx)
    {
        var n = new float[p.Length];
        for (int i = 0; i + 2 < idx.Length; i += 3)
        {
            int a = (int)idx[i] * 3, b = (int)idx[i + 1] * 3, c = (int)idx[i + 2] * 3;
            if (a + 2 >= p.Length || b + 2 >= p.Length || c + 2 >= p.Length) continue;
            float ux = p[b] - p[a], uy = p[b + 1] - p[a + 1], uz = p[b + 2] - p[a + 2];
            float vx = p[c] - p[a], vy = p[c + 1] - p[a + 1], vz = p[c + 2] - p[a + 2];
            float nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
            foreach (var k in new[] { a, b, c }) { n[k] += nx; n[k + 1] += ny; n[k + 2] += nz; }
        }
        for (int i = 0; i < n.Length; i += 3)
        {
            var l = MathF.Sqrt(n[i] * n[i] + n[i + 1] * n[i + 1] + n[i + 2] * n[i + 2]);
            if (l <= 0) { n[i] = 0; n[i + 1] = 0; n[i + 2] = 1; } else { n[i] /= l; n[i + 1] /= l; n[i + 2] /= l; }
        }
        return n;
    }
}
