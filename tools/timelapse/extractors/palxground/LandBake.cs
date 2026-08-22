// Landscape BASE COLOUR, baked per LandscapeStreamingProxy.
//
// WHY A BAKE AND NOT A MATERIAL. A UE landscape has no single base-colour map.
// Its surface is layer-blended in the shader: the proxy names one
// LandscapeMaterial (here MI_pal_b0*_PalLit_*Landscape*, a MaterialInstance-
// Constant with ~16 layers), each ULandscapeComponent carries a
// WeightmapTexture whose RGBA channels hold up to four layers' weights, and a
// WeightmapLayerAllocations table says which channel is which
// ULandscapeLayerInfoObject. The material then exposes, per layer,
//     "Color <Layer>"        the layer's diffuse texture
//     "Tiling Near <Layer>"  its world-space tiling, near
//     "Tiling Far <Layer>"   its world-space tiling, far
// Reproducing that in three.js would mean writing a 16-layer splat shader; the
// renderer already loads textured GLBs, so instead the blend is evaluated ONCE,
// offline, per proxy, into a single base-colour image with the tile's own UVs.
// Everything composited is game data: the game's weightmaps, the game's layer
// diffuses, the game's tiling scalars.
//
// DOES THE GAME SHIP ITS OWN BAKE? Partly, and it is the reference this is
// checked against, not the source: the HLOD0_252m_* runtime grids hold 517
// LandscapeMeshProxyComponents whose MaterialInstanceConstant carries a cooked
// <cell>T_<proxy>_BaseColor texture (256-1024 px). But those 517 cover only
// FarMountain proxies from 23 of the world's landscape groups - 7 of the 21
// proxies the four bases actually stand on, and NONE of the group under Lost
// Camp. Using them where they exist and compositing elsewhere would put two
// different bakes, at two different resolutions, on adjacent tiles. So every
// tile is composited the same way and the cooked bakes are used to VERIFY the
// composite (--landcmp).
//
// UV. LandscapeMeshDto emits vertices in quad units: a vertex's XY is exactly
// (SectionBase + vert) - min(RelativeLocation), which is also the index the
// weightmap grid is addressed by. So the tile's UV is a pure function of its own
// vertex positions and the extent already recorded in terrain_index.json:
//     u = (x - vminX) / (width  - 1),  width  = vmaxX - vminX + 1
//     v = (y - vminY) / (height - 1),  height = vmaxY - vminY + 1
// and the bake is written so texel (i,j) sits exactly on grid coordinate
// ((i+0.5)/W)*(width-1), which is where a vertex with that u lands under GL's
// half-texel convention. No re-extraction of the 800 MB heightfield cache is
// needed - build_terrain.py computes the same UV from the vertices it already
// clips.
using System.Text.Json;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets.Exports.Actor;
using CUE4Parse.UE4.Assets.Exports.Component.Landscape;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse_Conversion.Dto;
using CUE4Parse_Conversion.Options;
using CUE4Parse_Conversion.Textures;
using SkiaSharp;

namespace Palx;

public static class LandBake
{
    // Engine "nothing plugged in" textures. A layer whose Color resolves to one
    // of these is painted but carries no colour of its own; blending engine grey
    // in would wash the tile out, so such a layer is dropped and the remaining
    // weights renormalised.
    static readonly string[] Fallbacks = {
        "DefaultDiffuse", "DefaultBaseTexture", "DefaultNormal", "DefaultMaskMap",
        "DefaultNormalMap", "DefaultRSAO", "DefaultTexture", "Black.Black",
        "BaseFlatten", "Black_1x1",
    };
    static bool IsFallback(string p) =>
        p == null || Fallbacks.Any(f => p.Contains(f, StringComparison.OrdinalIgnoreCase));

    // "Tiling Near" or "Tiling Far". FAR is the default and it is not an
    // aesthetic pick: the material fades from near to far tiling between
    // "Start Distance Variation Fade" (1200 cm) and "End ..." (8000 cm), and the
    // timelapse camera orbits at ~5000 cm, so far tiling is what the game itself
    // shows at this distance. It is also the only one a per-proxy bake can carry:
    // near tiling is ~1 tile/m, which at any bake size that fits a browser is
    // below one texel per tile.
    static string TilingKind => Environment.GetEnvironmentVariable("LAND_TILING") ?? "Far";
    // Texels per landscape quad (= per metre) before the max-dimension clamp.
    static float PxPerQuad => float.TryParse(Environment.GetEnvironmentVariable("LAND_PXQ"), out var v) ? v : 8f;
    static int MaxDim => int.TryParse(Environment.GetEnvironmentVariable("LAND_MAXDIM"), out var v) ? v : 1536;
    static float MinPxPerQuad => float.TryParse(Environment.GetEnvironmentVariable("LAND_MINPXQ"), out var v) ? v : 3f;
    static int HardMaxDim => int.TryParse(Environment.GetEnvironmentVariable("LAND_HARDMAX"), out var v) ? v : 4096;
    static int LayerTexMax => int.TryParse(Environment.GetEnvironmentVariable("LAND_LAYERTEX"), out var v) ? v : 512;

    class Layer { public string name; public SKBitmap tex; public float tiling; public SKBitmap weight; }

    // decoded layer diffuse cache, keyed by texture asset path
    static readonly Dictionary<string, SKBitmap> TexCache = new(StringComparer.OrdinalIgnoreCase);

    /// Side-by-side + mean + per-channel correlation of two images, at a common
    /// size. Used to hold the composite against the game's own cooked
    /// <cell>T_<proxy>_BaseColor for the tiles where both exist. No pak needed.
    public static int Compare(string[] args)
    {
        var a = SKBitmap.Decode(args[1]);
        var b = SKBitmap.Decode(args[2]);
        if (a == null || b == null) { Console.Error.WriteLine("decode failed"); return 1; }
        int n = 512;
        using var A = a.Resize(new SKImageInfo(n, n), SKFilterQuality.High);
        using var B = b.Resize(new SKImageInfo(n, n), SKFilterQuality.High);
        double[] sa = new double[3], sb = new double[3], saa = new double[3], sbb = new double[3], sab = new double[3];
        for (int y = 0; y < n; y++) for (int x = 0; x < n; x++)
        {
            var p = A.GetPixel(x, y); var q = B.GetPixel(x, y);
            double[] pv = { p.Red, p.Green, p.Blue }, qv = { q.Red, q.Green, q.Blue };
            for (int c = 0; c < 3; c++)
            { sa[c] += pv[c]; sb[c] += qv[c]; saa[c] += pv[c] * pv[c]; sbb[c] += qv[c] * qv[c]; sab[c] += pv[c] * qv[c]; }
        }
        double m = n * (double)n;
        Console.WriteLine($"mean  composite = [{sa[0]/m:F1}, {sa[1]/m:F1}, {sa[2]/m:F1}]");
        Console.WriteLine($"mean  cooked    = [{sb[0]/m:F1}, {sb[1]/m:F1}, {sb[2]/m:F1}]");
        for (int c = 0; c < 3; c++)
        {
            double cov = sab[c] / m - (sa[c] / m) * (sb[c] / m);
            double va = saa[c] / m - (sa[c] / m) * (sa[c] / m), vb = sbb[c] / m - (sb[c] / m) * (sb[c] / m);
            Console.WriteLine($"  {"RGB"[c]} corr = {cov / Math.Sqrt(va * vb):F3}");
        }
        if (args.Length > 3)
        {
            using var side = new SKBitmap(n * 2 + 8, n);
            using var cv = new SKCanvas(side);
            cv.Clear(new SKColor(24, 24, 24));
            cv.DrawBitmap(A, 0, 0); cv.DrawBitmap(B, n + 8, 0);
            using var d = side.Encode(SKEncodedImageFormat.Png, 95);
            using var fs = File.Create(args[3]); d.SaveTo(fs);
            Console.WriteLine($"-> {args[3]}  (left composite, right cooked)");
        }
        return 0;
    }

    public static int Run(DefaultFileProvider provider, List<string> vpaths, string outDir)
    {
        var texDir = Path.Combine(outDir, "tex");
        Directory.CreateDirectory(texDir);
        var index = new Dictionary<string, object>();
        var idxPath = Path.Combine(Level.OutDirPublic, "landtex_index.json");
        if (File.Exists(idxPath))
            index = JsonSerializer.Deserialize<Dictionary<string, object>>(File.ReadAllText(idxPath));

        int ok = 0, fail = 0;
        foreach (var vp in vpaths)
        {
            var pkg = provider.LoadPackage(vp);
            var cell = vp[(vp.LastIndexOf('/') + 1)..];
            cell = cell[..cell.LastIndexOf('.')];
            foreach (var e in pkg.GetExports())
            {
                if (e is not ALandscapeProxy proxy) continue;
                try
                {
                    var rec = One(proxy, cell, texDir);
                    if (rec == null) { fail++; continue; }
                    index[proxy.Name] = rec;
                    ok++;
                }
                catch (Exception ex)
                {
                    fail++;
                    Console.WriteLine($"FAIL {cell} {proxy.Name}: {ex.GetType().Name}: {ex.Message}");
                }
            }
            File.WriteAllText(idxPath, JsonSerializer.Serialize(index,
                new JsonSerializerOptions { WriteIndented = false }));
        }
        Console.Error.WriteLine($"landbake: {ok} baked, {fail} failed -> {idxPath}");
        return 0;
    }

    static object One(ALandscapeProxy proxy, string cell, string texDir)
    {
        using var dto = new LandscapeMeshDto(proxy, ELandscapeFlags.Mesh | ELandscapeFlags.Weightmap);
        if (dto.BitmapTextures == null || dto.BitmapTextures.IsEmpty)
        { Console.WriteLine($"SKIP {proxy.Name}: no weightmaps"); return null; }
        var lod = dto.LODs[0];

        // The tile's own quad-space extent, straight off the vertices the mesh
        // extractor already writes, so the UV here and the UV build_terrain.py
        // computes are the same number.
        float vminX = float.MaxValue, vminY = float.MaxValue, vmaxX = float.MinValue, vmaxY = float.MinValue;
        foreach (var v in lod.Vertices)
        {
            var p = v.Position;
            if (p.X < vminX) vminX = p.X; if (p.X > vmaxX) vmaxX = p.X;
            if (p.Y < vminY) vminY = p.Y; if (p.Y > vmaxY) vmaxY = p.Y;
        }
        int gw = (int)MathF.Round(vmaxX - vminX) + 1;
        int gh = (int)MathF.Round(vmaxY - vminY) + 1;

        // Layer weight grids. "NormalMap_DX" is the DTO's own derived normal
        // image, not a landscape layer.
        var grids = dto.BitmapTextures
            .Where(kv => kv.Key != "NormalMap_DX")
            .ToDictionary(kv => kv.Key, kv => kv.Value);
        if (grids.Count == 0) { Console.WriteLine($"SKIP {proxy.Name}: only a normal map"); return null; }

        // The material. Components may each override it; in practice a proxy's
        // components share one, so resolve the first non-null and say so if not.
        var matRefs = new List<CUE4Parse.UE4.Objects.UObject.FPackageIndex>();
        foreach (var cref in proxy.LandscapeComponents)
        {
            var comp = cref.Load<ULandscapeComponent>();
            if (comp == null) continue;
            var m = comp.OverrideMaterial;
            matRefs.Add(m != null && !m.IsNull ? m : proxy.LandscapeMaterial);
        }
        var distinctMats = matRefs.Where(m => m != null).Select(m => m.Name).Distinct().ToList();
        var matRef = matRefs.FirstOrDefault(m => m != null && !m.IsNull) ?? proxy.LandscapeMaterial;
        if (matRef == null || !matRef.TryLoad(out var mo) || mo is not UMaterialInterface umat)
        { Console.WriteLine($"SKIP {proxy.Name}: landscape material did not load"); return null; }
        var mp = new CMaterialParams2();
        umat.GetParams(mp, EMaterialDepth.AllLayersNoRef);

        // The weight grids are keyed by the LayerInfo ASSET name, but the material's
        // parameters are keyed by the layer's own LayerName property, and the two
        // are not always the same string. Build the real mapping by loading each
        // allocated ULandscapeLayerInfoObject.
        var layerName = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var cref in proxy.LandscapeComponents)
        {
            var comp = cref.Load<ULandscapeComponent>();
            if (comp == null) continue;
            foreach (var a in comp.GetWeightmapLayerAllocations())
            {
                var an = a.GetLayerName();
                if (an == null || layerName.ContainsKey(an)) continue;
                var li = a.LayerInfo?.Load<ULandscapeLayerInfoObject>();
                var ln = li?.LayerName.Text;
                if (!string.IsNullOrEmpty(ln) && ln != "None") layerName[an] = ln;
            }
        }

        // layer weight grid name -> material layer key. The allocation names are
        // Houdini bake artefacts (Landscape_Temp_UAID_..._layer_GrassA); the part
        // after "_layer_" is the material's own parameter suffix.
        var layers = new List<Layer>();
        var dropped = new List<string>();
        if (Environment.GetEnvironmentVariable("LAND_DEBUG") == "1")
        {
            var dd0 = Path.Combine(texDir, "..", "dbg");
            Directory.CreateDirectory(dd0);
            foreach (var (gn, bm) in grids)
            {
                double s = 0; int c = 0;
                for (int y = 0; y < bm.Height; y++) for (int x = 0; x < bm.Width; x++) { s += bm.GetPixel(x, y).Red; c++; }
                Console.WriteLine($"      raw {gn,-40} meanWeight={s / c:F1}/255");
                using var d = bm.Encode(SKEncodedImageFormat.Png, 95);
                using var fs = File.Create(Path.Combine(dd0, $"{proxy.Name}__RAW_{LayerKey(gn)}.png"));
                d.SaveTo(fs);
            }
        }
        foreach (var (gname, bmp) in grids)
        {
            var key = LayerKey(layerName.TryGetValue(gname, out var ln) ? ln : gname);
            if (Environment.GetEnvironmentVariable("LAND_DEBUG") == "1")
                Console.WriteLine($"      map {gname} -> LayerName={(layerName.TryGetValue(gname, out var l2) ? l2 : "<none>")} -> key={key}");
            var tex = FindTex(mp, "Color " + key);
            var via = "Color " + key;
            // AUTO LAYERS. Palworld's landscape materials are auto-materials: a
            // painted layer such as SlopeMaterialA has RoughnessMul / NormalScale /
            // Metallic parameters but NO "Color SlopeMaterialA" - the graph derives
            // its surface from slope rather than from one texture. Dropping it is
            // not an option: on the tiles under these bases SlopeMaterialA is the
            // single biggest weight (mean 119/255), and dropping it leaves most of
            // the tile with no layer at all. The material's own PM_Diffuse is the
            // right stand-in and is not a guess: it is the texture the COOKED HLOD
            // material uses to represent this whole landscape material, and for
            // MI_PalLit_AutoLandscape0 it is Grass0072_D - the same grass the
            // game's own bake shows there.
            if (tex == null || IsFallback(tex.GetPathName()))
            {
                tex = FindTex(mp, "PM_Diffuse");
                via = "PM_Diffuse";
            }
            if (tex == null || IsFallback(tex.GetPathName())) { dropped.Add(key); continue; }
            var sk = Decode(tex);
            if (sk == null) { dropped.Add(key); continue; }
            // Tiling: the layer's own scalar when it has one. When it borrowed
            // PM_Diffuse, borrow the tiling of whichever layer PM_Diffuse IS - for
            // these materials PM_Diffuse is literally one of the Color <Layer>
            // textures, so that tiling is the texture's own authored rate.
            var tiling = FindScalar(mp, $"Tiling {TilingKind} {key}") ?? FindScalar(mp, $"Tiling Near {key}");
            if (tiling == null && via == "PM_Diffuse")
            {
                var twin = mp.Textures.FirstOrDefault(kv =>
                    kv.Key.StartsWith("Color ", StringComparison.OrdinalIgnoreCase) &&
                    ReferenceEquals(kv.Value, tex)).Key;
                if (twin != null)
                    tiling = FindScalar(mp, $"Tiling {TilingKind} {twin["Color ".Length..]}")
                             ?? FindScalar(mp, $"Tiling Near {twin["Color ".Length..]}");
            }
            layers.Add(new Layer
            {
                name = key + (via == "PM_Diffuse" ? "(PM)" : ""),
                tex = sk,
                tiling = tiling ?? 0.1f,
                weight = bmp,
            });
        }
        // Nothing painted resolved to a real map: fall back to the material's own
        // proxy diffuse (PM_Diffuse), which is what the cooked HLOD material uses
        // to stand in for the whole blend.
        string note = null;
        if (layers.Count == 0)
        {
            var pm = FindTex(mp, "PM_Diffuse");
            if (pm == null || IsFallback(pm.GetPathName()))
            { Console.WriteLine($"SKIP {proxy.Name}: no layer resolved a diffuse and no PM_Diffuse"); return null; }
            var sk = Decode(pm);
            if (sk == null) { Console.WriteLine($"SKIP {proxy.Name}: PM_Diffuse decode failed"); return null; }
            layers.Add(new Layer { name = "PM_Diffuse", tex = sk, tiling = 0.05f, weight = null });
            note = "no painted layer had a base-colour map; used the material's PM_Diffuse";
        }

        // proxy transform, for the world position each texel tiles against
        var rootIdx = proxy.GetOrDefault<CUE4Parse.UE4.Objects.UObject.FPackageIndex>("RootComponent", null);
        var root = rootIdx?.ResolvedObject?.Load();
        var loc = root?.GetOrDefault("RelativeLocation", FVector.ZeroVector) ?? FVector.ZeroVector;
        var scl = root?.GetOrDefault("RelativeScale3D", FVector.OneVector) ?? FVector.OneVector;
        var rot = root?.GetOrDefault("RelativeRotation", FRotator.ZeroRotator) ?? FRotator.ZeroRotator;
        float yaw = MathF.PI / 180f * rot.Yaw, cy = MathF.Cos(yaw), sy = MathF.Sin(yaw);

        // The material's own overall albedo scale. Measured, not assumed: against
        // the game's cooked FarMountain_L0_X0_Y-2_DL0T_..._252_3_1_0_BaseColor the
        // composite's mean linear luminance came out 1.71x the cooked bake with
        // this left at 1.0, and 0.86x with it applied - so the parameter is what it
        // says it is. Materials that do not declare it are left at 1.
        float intensity = FindScalar(mp, "Base Color Intensity") ?? 1f;

        // Resolution is a FLOOR on texel density, not a flat pixel cap. These
        // proxies are not all the same size: the 8EQ1/CBLW groups are 253 quads
        // across, but the 04YNR group under Lost Camp and Stone Works is 1021, and
        // a flat 1536 px cap gave those 1.5 texels/m - visibly soft at a camera
        // orbiting 50 m out. MinPxPerQuad holds every tile at >= 3 texels/m; the
        // texture is not worth more than that anyway, because at the Far tiling the
        // finest real detail in it repeats every ~2 m.
        float pxq = Math.Clamp(MaxDim / (float)Math.Max(gw, gh), MinPxPerQuad, PxPerQuad);
        pxq = MathF.Min(pxq, HardMaxDim / (float)Math.Max(gw, gh));
        int W = Math.Max(4, (int)MathF.Round(gw * pxq));
        int H = Math.Max(4, (int)MathF.Round(gh * pxq));

        using var outBmp = new SKBitmap(W, H, SKColorType.Rgba8888, SKAlphaType.Opaque);
        var px = outBmp.Pixels;
        var nLayers = layers.Count;
        Parallel.For(0, H, j =>
        {
            var w = new float[nLayers];
            float gy = (j + 0.5f) / H * (gh - 1);
            for (int i = 0; i < W; i++)
            {
                float gx = (i + 0.5f) / W * (gw - 1);
                float tot = 0f;
                for (int l = 0; l < nLayers; l++)
                {
                    w[l] = layers[l].weight == null ? 1f : SampleGray(layers[l].weight, gx, gy) / 255f;
                    tot += w[l];
                }
                // Quad -> world centimetres -> metres, the space the material's
                // tiling scalars are expressed in.
                float qx = (vminX + gx) * scl.X, qy = (vminY + gy) * scl.Y;
                float wx = (qx * cy - qy * sy + loc.X) / 100f;
                float wy = (qx * sy + qy * cy + loc.Y) / 100f;
                float r = 0, g = 0, b = 0;
                if (tot <= 1e-4f)
                {
                    // Unpainted texel: UE falls through to the first layer.
                    var c0 = SampleWrap(layers[0].tex, wx * layers[0].tiling, wy * layers[0].tiling);
                    r = c0.r; g = c0.g; b = c0.b; tot = 1f;
                }
                else
                {
                    for (int l = 0; l < nLayers; l++)
                    {
                        if (w[l] <= 0f) continue;
                        var c = SampleWrap(layers[l].tex, wx * layers[l].tiling, wy * layers[l].tiling);
                        r += c.r * w[l]; g += c.g * w[l]; b += c.b * w[l];
                    }
                }
                float k = intensity / tot;
                px[j * W + i] = new SKColor(ToSrgb(r * k), ToSrgb(g * k), ToSrgb(b * k), 255);
            }
        });
        outBmp.Pixels = px;

        if (Environment.GetEnvironmentVariable("LAND_DEBUG") == "1")
            foreach (var l in layers)
            {
                double sw = 0; int nw = 0;
                if (l.weight != null)
                    for (int y = 0; y < l.weight.Height; y++)
                        for (int x = 0; x < l.weight.Width; x++) { sw += l.weight.GetPixel(x, y).Red; nw++; }
                double tr = 0, tg = 0, tb = 0; int nt = 0;
                for (int y = 0; y < l.tex.Height; y += 4)
                    for (int x = 0; x < l.tex.Width; x += 4)
                    { var c = l.tex.GetPixel(x, y); tr += c.Red; tg += c.Green; tb += c.Blue; nt++; }
                Console.WriteLine($"      dbg {l.name,-22} meanWeight={(nw > 0 ? sw / nw : 255):F1}/255  " +
                                  $"texMean=[{tr / nt:F0},{tg / nt:F0},{tb / nt:F0}] {l.tex.Width}x{l.tex.Height} tiling={l.tiling}");
                var dd = Path.Combine(texDir, "..", "dbg");
                Directory.CreateDirectory(dd);
                if (l.weight != null)
                    using (var d = l.weight.Encode(SKEncodedImageFormat.Png, 95))
                    using (var fs = File.Create(Path.Combine(dd, $"{proxy.Name}__W_{l.name}.png")))
                        d.SaveTo(fs);
            }
        if (Environment.GetEnvironmentVariable("LAND_DEBUG") == "1")
            foreach (var cref in proxy.LandscapeComponents.Take(2))
            {
                var comp = cref.Load<ULandscapeComponent>();
                if (comp == null) continue;
                var wts = comp.GetWeightmapTextures();
                Console.WriteLine($"      wm  {comp.Name} base=({comp.SectionBaseX},{comp.SectionBaseY}) subs={comp.NumSubsections}x{comp.SubsectionSizeQuads} " +
                                  $"textures={string.Join(",", wts.Select(t => $"{t?.PlatformData?.SizeX}x{t?.PlatformData?.SizeY}"))} " +
                                  $"allocs={string.Join(",", comp.GetWeightmapLayerAllocations().Select(a => $"{a.GetLayerName()}:t{a.WeightmapTextureIndex}c{a.WeightmapTextureChannel}"))}");
            }

        var fname = $"land_{proxy.Name}_BC.jpg";
        using (var data = outBmp.Encode(SKEncodedImageFormat.Jpeg, 88))
        using (var fs = File.Create(Path.Combine(texDir, fname)))
            data.SaveTo(fs);

        Console.WriteLine($"OK   {proxy.Name,-58} grid={gw}x{gh} tex={W}x{H} " +
                          $"layers={string.Join("+", layers.Select(l => $"{l.name}@{l.tiling}"))}" +
                          (dropped.Count > 0 ? $" dropped={string.Join(",", dropped)}" : "") +
                          (distinctMats.Count > 1 ? $" MIXEDMATS={string.Join("/", distinctMats)}" : ""));

        return new
        {
            cell,
            tex = "tex/" + fname,
            material = umat.Name,
            texW = W,
            texH = H,
            vmin = new[] { vminX, vminY },
            grid = new[] { gw, gh },
            tiling = TilingKind,
            intensity,
            layers = layers.Select(l => new { l.name, l.tiling }).ToArray(),
            dropped = dropped.ToArray(),
            note,
        };
    }

    /// ULandscapeLayerInfoObject asset name -> the suffix the material's own
    /// parameters use. Palworld ships the same landscape layers under two
    /// naming schemes, and both are in the pak:
    ///   GrassA_LayerInfo                                    (hand-authored)
    ///   Landscape_Temp_UAID_<...>_<...>_layer_GrassA        (Houdini bake)
    /// The material only ever knows "GrassA".
    static string LayerKey(string gname)
    {
        var i = gname.LastIndexOf("_layer_", StringComparison.OrdinalIgnoreCase);
        var k = i >= 0 ? gname[(i + "_layer_".Length)..] : gname;
        if (k.EndsWith("_LayerInfo", StringComparison.OrdinalIgnoreCase))
            k = k[..^"_LayerInfo".Length];
        return k;
    }

    static UTexture FindTex(CMaterialParams2 p, string name)
    {
        foreach (var kv in p.Textures)
            if (string.Equals(kv.Key, name, StringComparison.OrdinalIgnoreCase) && kv.Value is UTexture t)
                return t;
        return null;
    }

    static float? FindScalar(CMaterialParams2 p, string name)
    {
        foreach (var kv in p.Scalars)
            if (string.Equals(kv.Key, name, StringComparison.OrdinalIgnoreCase))
                return kv.Value;
        return null;
    }

    static SKBitmap Decode(UTexture tex)
    {
        var path = tex.GetPathName();
        if (TexCache.TryGetValue(path, out var hit)) return hit;
        SKBitmap sk = null;
        try
        {
            var dec = tex.Decode(LayerTexMax);
            sk = dec?.ToSkBitmap();
            if (sk != null && Math.Max(sk.Width, sk.Height) > LayerTexMax)
            {
                var f = (float)LayerTexMax / Math.Max(sk.Width, sk.Height);
                var s = sk.Resize(new SKImageInfo(Math.Max(1, (int)(sk.Width * f)), Math.Max(1, (int)(sk.Height * f))), SKFilterQuality.High);
                if (s != null) sk = s;
            }
        }
        catch { }
        TexCache[path] = sk;
        return sk;
    }

    static byte SampleGray(SKBitmap b, float x, float y)
    {
        int x0 = (int)MathF.Floor(x), y0 = (int)MathF.Floor(y);
        float fx = x - x0, fy = y - y0;
        int x1 = Math.Min(x0 + 1, b.Width - 1), y1 = Math.Min(y0 + 1, b.Height - 1);
        x0 = Math.Clamp(x0, 0, b.Width - 1); y0 = Math.Clamp(y0, 0, b.Height - 1);
        float G(int xx, int yy) { var c = b.GetPixel(xx, yy); return c.Red; }
        float a = G(x0, y0) * (1 - fx) + G(x1, y0) * fx;
        float c2 = G(x0, y1) * (1 - fx) + G(x1, y1) * fx;
        return (byte)Math.Clamp(a * (1 - fy) + c2 * fy, 0, 255);
    }

    // sRGB -> linear, so weighted albedo blending is done in the space it means
    // something in rather than on gamma-encoded bytes.
    static readonly float[] Lin = BuildLin();
    static float[] BuildLin()
    {
        var t = new float[256];
        for (int i = 0; i < 256; i++)
        {
            float c = i / 255f;
            t[i] = c <= 0.04045f ? c / 12.92f : MathF.Pow((c + 0.055f) / 1.055f, 2.4f);
        }
        return t;
    }
    static byte ToSrgb(float v)
    {
        v = Math.Clamp(v, 0f, 1f);
        v = v <= 0.0031308f ? v * 12.92f : 1.055f * MathF.Pow(v, 1f / 2.4f) - 0.055f;
        return (byte)Math.Clamp(MathF.Round(v * 255f), 0, 255);
    }

    static (float r, float g, float b) SampleWrap(SKBitmap b, float u, float v)
    {
        int w = b.Width, h = b.Height;
        float x = (u - MathF.Floor(u)) * w, y = (v - MathF.Floor(v)) * h;
        int x0 = (int)x % w, y0 = (int)y % h;
        var c = b.GetPixel(x0, y0);
        return (Lin[c.Red], Lin[c.Green], Lin[c.Blue]);
    }
}
