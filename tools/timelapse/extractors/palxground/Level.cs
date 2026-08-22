// World Partition streaming-cell reader.
//
// Cooked Palworld ships each World Partition cell as its own .umap under
// PL_MainWorld5/_Generated_/<Grid>_L<lod>_X<x>_Y<y>_DL<datalayer>.umap, with the
// cell's actors BAKED IN (there is no __ExternalActors__ directory in the pak —
// cooking folds them into the cell package). So every actor's real authored
// transform is recoverable straight from the package exports.
//
// Mode 1 (--survey): print the export class histogram of one or more cells.
// Mode 2 (--level):  dump every placed StaticMesh / foliage instance transform
//                    as JSON.
using System.Text.Json;
using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports.Component.StaticMesh;
using CUE4Parse_Conversion.Textures;

namespace Palx;

public static class Level
{
    static string OutDir => Environment.GetEnvironmentVariable("PALX_OUT")
        ?? Program.BaseDir;

    /// Where the SERVED terrain meshes and their textures live. The landscape
    /// base-colour bakes go beside the GLBs that reference them, not into the
    /// per-run PALX_OUT scratch dir.
    public static string OutDirPublic => Environment.GetEnvironmentVariable("PALX_PUBLIC")
        ?? Path.Combine(Program.BaseDir, "mappal", "public", "terrain_meshes");

    public static DefaultFileProvider MakeProvider(string root)
    {
        var provider = new DefaultFileProvider(new DirectoryInfo(root), SearchOption.AllDirectories,
            new VersionContainer(EGame.GAME_UE5_1), StringComparer.OrdinalIgnoreCase);
        provider.Initialize();
        provider.Mount();   // unencrypted pak: required, else Files is empty
        provider.MappingsContainer = new FileUsmapTypeMappingsProvider(
            Path.Combine(Program.BaseDir, "Mappings.usmap"));
        provider.ReadNaniteData = true;
        // Palworld subclasses UHierarchicalInstancedStaticMeshComponent for its
        // foliage. Without this the export falls back to plain UObject and the
        // PerInstanceSMData bulk array (which is serialized, not a UProperty) is
        // never read — every foliage component comes back with 0 instances.
        foreach (var n in new[] { "PalFoliageISMComponent", "PalFoliageISMComponentBase",
                                  "PalHierarchicalInstancedStaticMeshComponent" })
            ObjectTypeRegistry.RegisterClass(n, typeof(UHierarchicalInstancedStaticMeshComponent));
        return provider;
    }

    /// Every parameter of a material (and, for a MaterialInstanceConstant, of the
    /// parents it inherits from). Probe output only.
    static void DumpMaterial(FPackageIndex mi)
    {
        if (mi == null || mi.IsNull) { Console.WriteLine("  material: <none>"); return; }
        if (!mi.TryLoad(out var mo) || mo is not CUE4Parse.UE4.Assets.Exports.Material.UMaterialInterface umat)
        { Console.WriteLine($"  material {mi.Name}: did not load"); return; }
        Console.WriteLine($"  --- material {umat.Name} [{umat.ExportType}] {umat.GetPathName()}");
        var p = new CUE4Parse.UE4.Assets.Exports.Material.CMaterialParams2();
        umat.GetParams(p, CUE4Parse.UE4.Assets.Exports.Material.EMaterialDepth.AllLayersNoRef);
        Console.WriteLine($"      blend={p.BlendMode} textures={p.Textures.Count} scalars={p.Scalars.Count} vectors={p.Colors.Count} switches={p.Switches.Count}");
        foreach (var kv in p.Textures.OrderBy(k => k.Key))
            Console.WriteLine($"      TEX  {kv.Key} = {(kv.Value as CUE4Parse.UE4.Assets.Exports.Texture.UTexture)?.GetPathName() ?? kv.Value?.Name}");
        foreach (var kv in p.Scalars.OrderBy(k => k.Key))
            Console.WriteLine($"      SCL  {kv.Key} = {kv.Value}");
        foreach (var kv in p.Colors.OrderBy(k => k.Key))
            Console.WriteLine($"      VEC  {kv.Key} = {kv.Value}");
        foreach (var kv in p.Switches.OrderBy(k => k.Key))
            Console.WriteLine($"      SW   {kv.Key} = {kv.Value}");
        var refs = new List<CUE4Parse.UE4.Assets.Exports.Material.UUnrealMaterial>();
        try { umat.AppendReferencedTextures(refs, false); } catch { }
        foreach (var t in refs.OfType<CUE4Parse.UE4.Assets.Exports.Texture.UTexture>())
            Console.WriteLine($"      REF  {t.GetPathName()}");
    }

    public static int Run(string[] args)
    {
        var mode = args[0];
        var provider = MakeProvider(Environment.GetEnvironmentVariable("PALX_ROOT")
            ?? Path.Combine(Program.BaseDir, "rawassets"));
        Console.Error.WriteLine($"provider files: {provider.Files.Count}");

        var wanted = args.Skip(1).ToArray();
        var vpaths = new List<string>();
        foreach (var k in provider.Files.Keys)
        {
            if (!k.EndsWith(".umap", StringComparison.OrdinalIgnoreCase)) continue;
            var leaf = k[(k.LastIndexOf('/') + 1)..];
            if (wanted.Length == 0 || wanted.Any(w => leaf.Contains(w, StringComparison.OrdinalIgnoreCase)))
                vpaths.Add(k);
        }
        vpaths.Sort();
        Console.Error.WriteLine($"matched {vpaths.Count} umaps");

        if (mode == "--charxform")
        {
            // For every character blueprint, emit the SkeletalMeshComponent's own
            // RelativeLocation/Rotation/Scale3D (plus the collision capsule's half
            // height, which is what that offset is derived from in the first place).
            // This is the character equivalent of meshXform.json: a save records the
            // ACTOR transform, and the mesh hangs off it by this amount.
            var assets = provider.Files.Keys
                .Where(k => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)
                            && args.Skip(1).Any(w => k.Contains(w, StringComparison.OrdinalIgnoreCase)))
                .OrderBy(k => k).ToList();
            Console.Error.WriteLine($"matched {assets.Count} blueprint assets");
            var outx = new Dictionary<string, object>();
            int hits = 0;
            foreach (var vp in assets)
            {
                try
                {
                    var pkg = provider.LoadPackage(vp);
                    float? capsule = null;
                    object rec = null;
                    foreach (var ex in pkg.GetExports())
                    {
                        var cls = ex.ExportType ?? "";
                        if (cls.Contains("Capsule", StringComparison.OrdinalIgnoreCase)
                            && ex.Properties.Any(pr => pr.Name.Text == "CapsuleHalfHeight"))
                            capsule ??= ex.GetOrDefault<float>("CapsuleHalfHeight");
                        if (!cls.Contains("SkeletalMeshComponent", StringComparison.OrdinalIgnoreCase)) continue;
                        if (!ex.Name.Contains("CharacterMesh", StringComparison.OrdinalIgnoreCase)) continue;
                        var loc = ex.GetOrDefault("RelativeLocation", FVector.ZeroVector);
                        var rot = ex.GetOrDefault("RelativeRotation", FRotator.ZeroRotator);
                        var scl = ex.GetOrDefault("RelativeScale3D", FVector.OneVector);
                        var skm = ex.GetOrDefault<FPackageIndex>("SkeletalMesh", null);
                        rec = new {
                            loc = new[] { loc.X, loc.Y, loc.Z },
                            rot = new[] { rot.Pitch, rot.Yaw, rot.Roll },
                            scale = new[] { scl.X, scl.Y, scl.Z },
                            mesh = skm?.Name,
                        };
                    }
                    if (rec != null)
                    {
                        var name = vp[(vp.LastIndexOf('/') + 1)..^".uasset".Length];
                        outx[name] = new { xform = rec, capsuleHalfHeight = capsule };
                        hits++;
                    }
                }
                catch { }
            }
            File.WriteAllText(Path.Combine(Program.BaseDir, "charxform_raw.json"),
                JsonSerializer.Serialize(outx, new JsonSerializerOptions { WriteIndented = false }));
            Console.Error.WriteLine($"blueprints with a CharacterMesh component: {hits} -> charxform_raw.json");
            return 0;
        }

        if (mode == "--assetprops")
        {
            // Dump every export's properties for arbitrary assets (used to read
            // the SkeletalMeshComponent's own RelativeLocation/Rotation/Scale3D
            // out of a Pal / player character blueprint, rather than eyeballing
            // an offset).
            var assets = provider.Files.Keys
                .Where(k => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)
                            && args.Skip(1).Any(w => k.Contains(w, StringComparison.OrdinalIgnoreCase)))
                .OrderBy(k => k).ToList();
            Console.Error.WriteLine($"matched {assets.Count} assets");
            foreach (var vp in assets)
            {
                try
                {
                    var pkg = provider.LoadPackage(vp);
                    Console.WriteLine($"\n### {vp}");
                    foreach (var ex in pkg.GetExports())
                    {
                        var interesting = ex.Properties.Where(pr =>
                            pr.Name.Text is "RelativeLocation" or "RelativeRotation"
                            or "RelativeScale3D" or "SkeletalMesh" or "CapsuleHalfHeight"
                            or "MeshComponentRelativeTransform").ToList();
                        if (interesting.Count == 0) continue;
                        Console.WriteLine($"  [{ex.ExportType}] {ex.Name}");
                        foreach (var pr in interesting)
                            Console.WriteLine($"      {pr.Name.Text} = {pr.Tag?.GenericValue}");
                    }
                }
                catch (Exception ex) { Console.WriteLine($"FAIL {vp}: {ex.Message}"); }
            }
            return 0;
        }

        if (mode == "--datatable")
        {
            // Dump UDataTable rows as JSON. Used to resolve the equipment/skin
            // ITEM ids the player save records (e.g. "CopperArmorCold") to the
            // actual SK_Player_*_Outfit_* mesh the game uses for them, rather
            // than guessing a mapping from the names.
            var tables = provider.Files.Keys
                .Where(k => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)
                            && args.Skip(1).Any(w => k.Contains(w, StringComparison.OrdinalIgnoreCase)))
                .OrderBy(k => k).ToList();
            Console.Error.WriteLine($"matched {tables.Count} assets");
            var dump = new Dictionary<string, object>();
            foreach (var vp in tables)
            {
                try
                {
                    var pkg = provider.LoadPackage(vp);
                    foreach (var ex in pkg.GetExports())
                    {
                        if (ex is not CUE4Parse.UE4.Assets.Exports.Engine.UDataTable dt) continue;
                        var rows = new Dictionary<string, Dictionary<string, string>>();
                        foreach (var kv in dt.RowMap)
                        {
                            var cols = new Dictionary<string, string>();
                            foreach (var prop in kv.Value.Properties)
                            {
                                var g = prop.Tag?.GenericValue;
                                if (g is CUE4Parse.UE4.Assets.Objects.UScriptMap map)
                                {
                                    // e.g. SkeletalMeshMap is keyed by gender enum —
                                    // flatten so the real per-gender mesh path is visible.
                                    var parts = new List<string>();
                                    foreach (var e2 in map.Properties)
                                        parts.Add($"{e2.Key?.GenericValue}={e2.Value?.GenericValue}");
                                    cols[prop.Name.Text] = string.Join(" | ", parts);
                                }
                                else cols[prop.Name.Text] = g?.ToString() ?? "";
                            }
                            rows[kv.Key.Text] = cols;
                        }
                        dump[vp[(vp.LastIndexOf('/') + 1)..]] = rows;
                        Console.WriteLine($"{vp[(vp.LastIndexOf('/') + 1)..]}: {rows.Count} rows");
                    }
                }
                catch (Exception ex) { Console.WriteLine($"FAIL {vp}: {ex.Message}"); }
            }
            File.WriteAllText(Path.Combine(Program.BaseDir, "datatables.json"),
                JsonSerializer.Serialize(dump, new JsonSerializerOptions { WriteIndented = false }));
            return 0;
        }

        if (mode == "--landbake")
            return LandBake.Run(provider, vpaths, OutDirPublic);

        if (mode == "--landcmp")
        {
            // Ground truth for the composite: dump the game's OWN cooked
            // <cell>T_<proxy>_BaseColor out of the HLOD0_252m package, so the
            // composite can be compared against it pixel for pixel.
            var outDir = Path.Combine(OutDir, "landcmp");
            Directory.CreateDirectory(outDir);
            int n = 0;
            foreach (var vp in vpaths)
            {
                var pkg = provider.LoadPackage(vp);
                foreach (var e in pkg.GetExports())
                {
                    if (e is not CUE4Parse.UE4.Assets.Exports.Texture.UTexture2D t) continue;
                    if (!t.Name.EndsWith("_BaseColor", StringComparison.OrdinalIgnoreCase)) continue;
                    var dec = t.Decode(4096);
                    var bm = dec?.ToSkBitmap();
                    if (bm == null) continue;
                    using (var d = bm.Encode(SkiaSharp.SKEncodedImageFormat.Png, 95))
                    using (var fs = File.Create(Path.Combine(outDir, t.Name + ".png")))
                        d.SaveTo(fs);
                    Console.WriteLine($"{t.Name}  {bm.Width}x{bm.Height}");
                    n++;
                }
            }
            Console.Error.WriteLine($"{n} cooked BaseColor textures -> {outDir}");
            return 0;
        }

        if (mode == "--landmat")
        {
            // PROBE: how is this landscape's surface actually authored?
            // A UE landscape is layer-blended: the proxy names ONE LandscapeMaterial,
            // each ULandscapeComponent carries WeightmapTextures + a
            // WeightmapLayerAllocations table saying which (texture, channel) holds
            // which ULandscapeLayerInfoObject's weight, and the material exposes one
            // set of parameters per layer. Nothing about that is guessable, so dump it.
            foreach (var vp in vpaths)
            {
                var pkg = provider.LoadPackage(vp);
                var cell = vp[(vp.LastIndexOf('/') + 1)..];
                foreach (var e in pkg.GetExports())
                {
                    if (e is not CUE4Parse.UE4.Assets.Exports.Actor.ALandscapeProxy proxy) continue;
                    Console.WriteLine($"\n### {cell} :: {proxy.Name}");
                    Console.WriteLine($"  LandscapeMaterial = {proxy.LandscapeMaterial?.Name} :: {proxy.LandscapeMaterial?.ResolvedObject?.GetPathName()}");
                    foreach (var pn in new[] { "LandscapeHoleMaterial", "LandscapeMaterialsOverride", "NaniteComponents", "LandscapeMaterialInstances" })
                        if (proxy.Properties.Any(p => p.Name.Text == pn))
                            Console.WriteLine($"  {pn} = {proxy.GetOrDefault<object>(pn)}");
                    Console.WriteLine($"  proxy props: {string.Join(", ", proxy.Properties.Select(p => p.Name.Text))}");
                    int ci = 0;
                    foreach (var cref in proxy.LandscapeComponents)
                    {
                        var comp = cref.Load<CUE4Parse.UE4.Assets.Exports.Component.Landscape.ULandscapeComponent>();
                        if (comp == null) continue;
                        Console.WriteLine($"  [comp {ci}] {comp.Name} base=({comp.SectionBaseX},{comp.SectionBaseY}) quads={comp.ComponentSizeQuads} subs={comp.NumSubsections}x{comp.SubsectionSizeQuads}");
                        Console.WriteLine($"      OverrideMaterial={comp.OverrideMaterial?.Name} heightmap={comp.GetHeightmap()?.Name}");
                        Console.WriteLine($"      WeightmapScaleBias={comp.WeightmapScaleBias} subOff={comp.WeightmapSubsectionOffset}");
                        var wts = comp.GetWeightmapTextures();
                        for (int k = 0; k < wts.Length; k++)
                            Console.WriteLine($"      weightmap[{k}] = {wts[k]?.Name} {wts[k]?.PlatformData?.SizeX}x{wts[k]?.PlatformData?.SizeY} {wts[k]?.Format}");
                        foreach (var a in comp.GetWeightmapLayerAllocations())
                            Console.WriteLine($"      layer '{a.GetLayerName()}' -> tex{a.WeightmapTextureIndex} ch{a.WeightmapTextureChannel}  ({a.LayerInfo?.ResolvedObject?.GetPathName()})");
                        Console.WriteLine($"      comp props: {string.Join(", ", comp.Properties.Select(p => p.Name.Text))}");
                        if (++ci >= 2) break;   // components of one proxy are identical in shape
                    }
                    // The material itself: every texture / scalar / vector parameter,
                    // which is where the per-layer diffuse maps and their tiling live.
                    var mobj = proxy.LandscapeComponents.Length > 0
                        ? (proxy.LandscapeComponents[0].Load<CUE4Parse.UE4.Assets.Exports.Component.Landscape.ULandscapeComponent>()?.OverrideMaterial ?? proxy.LandscapeMaterial)
                        : proxy.LandscapeMaterial;
                    DumpMaterial(mobj);
                    break;   // one proxy per cell is enough for a probe
                }
            }
            return 0;
        }

        if (mode == "--landscape")
        {
            // Real terrain: every World Partition cell that covers land carries an
            // ALandscapeStreamingProxy whose ULandscapeComponents hold the authored
            // heightmap. CUE4Parse rebuilds the exact editor heightfield mesh from
            // them (LandscapeMeshDto), so this is the game's own terrain geometry,
            // not a reconstruction.
            var recs = new List<object>();
            foreach (var vp in vpaths)
            {
                var pkg = provider.LoadPackage(vp);
                var cell = vp[(vp.LastIndexOf('/') + 1)..];
                foreach (var e in pkg.GetExports())
                {
                    if (e is not CUE4Parse.UE4.Assets.Exports.Actor.ALandscapeProxy proxy) continue;
                    var rootIdx = proxy.GetOrDefault<FPackageIndex>("RootComponent", null);
                    var root = rootIdx?.ResolvedObject?.Load();
                    var loc = root?.GetOrDefault("RelativeLocation", FVector.ZeroVector) ?? FVector.ZeroVector;
                    var scl = root?.GetOrDefault("RelativeScale3D", FVector.OneVector) ?? FVector.OneVector;
                    var rot = root?.GetOrDefault("RelativeRotation", FRotator.ZeroRotator) ?? FRotator.ZeroRotator;
                    try
                    {
                        var dto = new CUE4Parse_Conversion.Dto.LandscapeMeshDto(proxy,
                            CUE4Parse_Conversion.Options.ELandscapeFlags.Mesh);
                        // LAND_LOD picks the heightfield's OWN LOD. LOD0 of this
                        // landscape is 1 quad per metre, so a 600 m disc of it is
                        // ~2.3 M triangles - more than the whole rest of a base
                        // scene. A coarser LOD is the game's own decimation of the
                        // same surface, not an invented simplification.
                        var want = int.TryParse(Environment.GetEnvironmentVariable("LAND_LOD"), out var wl) ? wl : 0;
                        var li = Math.Min(want, dto.LODs.Count - 1);
                        var lod = dto.LODs[li];
                        Console.Error.WriteLine($"   lods={dto.LODs.Count} using={li}");
                        var pos = new float[lod.Vertices.Length * 3];
                        for (int i = 0; i < lod.Vertices.Length; i++)
                        {
                            var v = lod.Vertices[i].Position;
                            pos[i * 3] = v.X; pos[i * 3 + 1] = v.Y; pos[i * 3 + 2] = v.Z;
                        }
                        var outDir = Path.Combine(OutDir, "terrain_meshes");
                        Directory.CreateDirectory(outDir);
                        var nm = $"{cell[..cell.LastIndexOf('.')]}__{proxy.Name}";
                        // LAND_NOGLB=1: transforms/extents only. FarMountain_L0 holds
                        // 939 proxies; writing every GLB just to find out WHERE they
                        // are is minutes of disk for a question answered by the index.
                        if (Environment.GetEnvironmentVariable("LAND_NOGLB") != "1")
                            Program.WriteGlbPublic(Path.Combine(outDir, nm + ".glb"), pos, lod.Indices, nm);
                        float mnx=float.MaxValue,mny=float.MaxValue,mnz=float.MaxValue,mxx=float.MinValue,mxy=float.MinValue,mxz=float.MinValue;
                        for (int i = 0; i < pos.Length; i += 3) {
                            if (pos[i]<mnx) mnx=pos[i]; if (pos[i]>mxx) mxx=pos[i];
                            if (pos[i+1]<mny) mny=pos[i+1]; if (pos[i+1]>mxy) mxy=pos[i+1];
                            if (pos[i+2]<mnz) mnz=pos[i+2]; if (pos[i+2]>mxz) mxz=pos[i+2];
                        }
                        recs.Add(new { cell, actor = proxy.Name, glb = "terrain_meshes/" + nm + ".glb",
                            verts = lod.Vertices.Length, tris = lod.Indices.Length / 3,
                            vmin = new[]{mnx,mny,mnz}, vmax = new[]{mxx,mxy,mxz},
                            loc = new[] { loc.X, loc.Y, loc.Z }, scale = new[] { scl.X, scl.Y, scl.Z },
                            rot = new[] { rot.Pitch, rot.Yaw, rot.Roll },
                            componentSizeQuads = proxy.ComponentSizeQuads,
                            components = proxy.LandscapeComponents.Length });
                        Console.WriteLine($"OK  {nm}  {lod.Vertices.Length} v  loc={loc}  scale={scl}  rot={rot}");
                    }
                    catch (Exception ex)
                    {
                        Console.WriteLine($"FAIL {cell} {proxy.Name}: {ex.GetType().Name}: {ex.Message}");
                    }
                }
            }
            File.WriteAllText(Path.Combine(OutDir, "terrain_index.json"),
                JsonSerializer.Serialize(recs, new JsonSerializerOptions { WriteIndented = true }));
            return 0;
        }

        if (mode == "--dump")
        {
            // Copy the raw cooked bytes of every file whose virtual path matches one
            // of the patterns into rawassets/, so later passes can run off loose
            // files without holding the 40 GB pak open (it is contended).
            var outRoot = Environment.GetEnvironmentVariable("PALX_DUMP")
                ?? Path.Combine(Program.BaseDir, "rawassets");
            var pats = args.Skip(1).ToArray();
            long tot = 0; int n = 0, fail = 0;
            foreach (var k in provider.Files.Keys.ToList())
            {
                if (!pats.Any(w => k.Contains(w, StringComparison.OrdinalIgnoreCase))) continue;
                var dst = Path.Combine(outRoot, k.Replace('/', Path.DirectorySeparatorChar));
                if (File.Exists(dst)) { n++; continue; }
                try
                {
                    var bytes = provider.SaveAsset(k);
                    Directory.CreateDirectory(Path.GetDirectoryName(dst)!);
                    File.WriteAllBytes(dst, bytes);
                    tot += bytes.Length; n++;
                }
                catch (Exception ex) { fail++; Console.Error.WriteLine($"  SKIP {k}: {ex.Message}"); }
            }
            Console.Error.WriteLine($"dumped {n} files ({tot:N0} bytes), {fail} failed -> {outRoot}");
            return 0;
        }

        if (mode == "--hlodland")
        {
            // Palworld DOES cook a baked landscape: the HLOD0_252m_* runtime grids
            // hold 517 LandscapeMeshProxyComponents, each a StaticMesh of one
            // 252 m landscape block with its own MaterialInstanceConstant and a
            // BAKED base-colour Texture2D. That bake is the layer blend already
            // resolved by the cooker, so it beats compositing weightmaps by hand.
            // What has to be established before using it: the world box each
            // proxy covers, and the world XY -> UV map of its mesh.
            var recs = new List<object>();
            foreach (var vp in vpaths)
            {
                var pkg = provider.LoadPackage(vp);
                var cell = vp[(vp.LastIndexOf('/') + 1)..];
                foreach (var e in pkg.GetExports())
                {
                    if (e.ExportType != "LandscapeMeshProxyComponent") continue;
                    var smRef = e.GetOrDefault<FPackageIndex>("StaticMesh", null);
                    var sm = smRef?.Load<CUE4Parse.UE4.Assets.Exports.StaticMesh.UStaticMesh>();
                    if (sm == null) continue;
                    var loc = e.GetOrDefault("RelativeLocation", FVector.ZeroVector);
                    var scl = e.GetOrDefault("RelativeScale3D", FVector.OneVector);
                    using var dto = new CUE4Parse_Conversion.Dto.StaticMeshDto(sm,
                        CUE4Parse_Conversion.Options.EMeshQuality.All,
                        CUE4Parse_Conversion.Options.ENaniteMeshFormat.NaniteLast);
                    if (dto.LODs.Count == 0) continue;
                    var lod = dto.LODs[0];
                    float mnx=float.MaxValue,mny=float.MaxValue,mxx=float.MinValue,mxy=float.MinValue;
                    float mnu=float.MaxValue,mnv=float.MaxValue,mxu=float.MinValue,mxv=float.MinValue;
                    foreach (var v in lod.Vertices)
                    {
                        var p = v.Position; var t = v.Uv;
                        if (p.X<mnx) mnx=p.X; if (p.X>mxx) mxx=p.X;
                        if (p.Y<mny) mny=p.Y; if (p.Y>mxy) mxy=p.Y;
                        if (t.U<mnu) mnu=t.U; if (t.U>mxu) mxu=t.U;
                        if (t.V<mnv) mnv=t.V; if (t.V>mxv) mxv=t.V;
                    }
                    var mats = sm.StaticMaterials.Select(m => m.MaterialInterface).ToArray();
                    var texes = new List<string>();
                    foreach (var m in mats)
                    {
                        if (m == null || !m.TryLoad(out var mo) || mo is not CUE4Parse.UE4.Assets.Exports.Material.UMaterialInterface um) continue;
                        var pp = new CUE4Parse.UE4.Assets.Exports.Material.CMaterialParams2();
                        um.GetParams(pp, CUE4Parse.UE4.Assets.Exports.Material.EMaterialDepth.AllLayersNoRef);
                        foreach (var kv in pp.Textures)
                            if (kv.Value is CUE4Parse.UE4.Assets.Exports.Texture.UTexture2D t2)
                                texes.Add($"{kv.Key}={t2.Name}[{t2.PlatformData?.SizeX}x{t2.PlatformData?.SizeY}]");
                        texes.Add($"MAT={um.Name}");
                    }
                    recs.Add(new { cell, comp = e.Name, mesh = sm.Name,
                        verts = lod.Vertices.Length, tris = lod.Indices.Length / 3,
                        loc = new[]{loc.X,loc.Y,loc.Z}, scale = new[]{scl.X,scl.Y,scl.Z},
                        xy = new[]{mnx,mny,mxx,mxy}, uv = new[]{mnu,mnv,mxu,mxv},
                        tex = texes });
                    Console.WriteLine($"{cell} {sm.Name} v={lod.Vertices.Length} xy=[{mnx:F0},{mny:F0}]..[{mxx:F0},{mxy:F0}] uv=[{mnu:F3},{mnv:F3}]..[{mxu:F3},{mxv:F3}] {string.Join(" ", texes)}");
                }
            }
            File.WriteAllText(Path.Combine(OutDir, Environment.GetEnvironmentVariable("HLODLAND_OUT") ?? "hlodland_index.json"),
                JsonSerializer.Serialize(recs, new JsonSerializerOptions { WriteIndented = false }));
            Console.Error.WriteLine($"{recs.Count} landscape HLOD proxies");
            return 0;
        }

        if (mode == "--find")
        {
            // Any virtual path matching every pattern. Used to ask whether the pak
            // ships a COOKED landscape base-colour (some projects bake one for
            // HLOD/low-end) before deciding to composite one from the weightmaps.
            var pats = args.Skip(1).ToArray();
            int n = 0;
            foreach (var k in provider.Files.Keys.OrderBy(x => x))
                if (pats.All(w => k.Contains(w, StringComparison.OrdinalIgnoreCase)))
                { Console.WriteLine(k); n++; }
            Console.Error.WriteLine($"{n} matches");
            return 0;
        }

        if (mode == "--ls")
        {
            foreach (var vp in vpaths) Console.WriteLine(vp);
            return 0;
        }

        if (mode == "--allexp")
        {
            // EVERY export of the matched cells, with no class filter at all.
            // The --level dump only keeps *StaticMeshComponent* / *ISMComponent* /
            // SceneComponent / *Landscape*, so anything else that carries geometry
            // (SplineMeshComponent, a Blueprint actor whose meshes live in its own
            // class package, ...) is invisible in cellactors.json. This mode is the
            // ground truth to compare it against.
            var recs = new List<object>();
            foreach (var vp in vpaths)
            {
                var pkg = provider.LoadPackage(vp);
                var cell = vp[(vp.LastIndexOf('/') + 1)..];
                foreach (var e in pkg.GetExports())
                {
                    var loc = e.GetOrDefault("RelativeLocation", FVector.ZeroVector);
                    var rot = e.GetOrDefault("RelativeRotation", FRotator.ZeroRotator);
                    var scl = e.GetOrDefault("RelativeScale3D", FVector.OneVector);
                    var meshRef = e.GetOrDefault<FPackageIndex>("StaticMesh", null);
                    var rootRef = e.GetOrDefault<FPackageIndex>("RootComponent", null);
                    // the actor's own class: for a BP instance the export's class is
                    // the generated class, reachable through the export's template.
                    string ovr = null;
                    var om = e.GetOrDefault<FPackageIndex[]>("OverrideMaterials", null);
                    if (om != null) ovr = string.Join(",", om.Select(x => x?.Name ?? "null"));
                    recs.Add(new
                    {
                        cell,
                        cls = e.ExportType,
                        name = e.Name,
                        outer = e.Outer?.Name,
                        mesh = meshRef?.Name,
                        meshPath = meshRef?.ResolvedObject?.GetPathName(),
                        root = rootRef?.Name,
                        ovr,
                        loc = new[] { loc.X, loc.Y, loc.Z },
                        rot = new[] { rot.Pitch, rot.Yaw, rot.Roll },
                        scale = new[] { scl.X, scl.Y, scl.Z },
                        props = e.Properties.Select(p => p.Name.Text).ToArray(),
                    });
                }
                Console.Error.WriteLine($"{cell}: {pkg.GetExports().Count()} exports");
            }
            var op = Path.Combine(OutDir, Environment.GetEnvironmentVariable("ALLEXP_OUT") ?? "allexp.json");
            File.WriteAllText(op, JsonSerializer.Serialize(recs,
                new JsonSerializerOptions { WriteIndented = false }));
            Console.Error.WriteLine($"wrote {recs.Count} exports -> {op}");
            return 0;
        }

        if (mode == "--survey")
        {
            foreach (var vp in vpaths)
            {
                var pkg = provider.LoadPackage(vp);
                var hist = new SortedDictionary<string, int>();
                foreach (var e in pkg.GetExports())
                {
                    var c = e.ExportType ?? e.GetType().Name;
                    hist[c] = hist.GetValueOrDefault(c) + 1;
                }
                Console.WriteLine($"\n### {vp[(vp.LastIndexOf('/') + 1)..]}  exports={pkg.GetExports().Count()}");
                foreach (var kv in hist.OrderByDescending(k => k.Value))
                    Console.WriteLine($"   {kv.Value,6}  {kv.Key}");
            }
            return 0;
        }

        var outActors = new List<object>();
        foreach (var vp in vpaths)
        {
            var pkg = provider.LoadPackage(vp);
            var cell = vp[(vp.LastIndexOf('/') + 1)..];
            foreach (var e in pkg.GetExports())
            {
                var cls = e.ExportType ?? "";
                // --- plain placed static meshes -------------------------------
                if (cls.Contains("StaticMeshComponent", StringComparison.OrdinalIgnoreCase)
                    || cls.Contains("ISMComponent", StringComparison.OrdinalIgnoreCase))
                {
                    var meshRef = e.GetOrDefault<FPackageIndex>("StaticMesh", null);
                    var meshName = meshRef?.Name ?? meshRef?.ResolvedObject?.Name.Text ?? null;
                    var mp = meshRef?.ResolvedObject?.GetPathName();
                    var loc = e.GetOrDefault("RelativeLocation", FVector.ZeroVector);
                    var rot = e.GetOrDefault("RelativeRotation", FRotator.ZeroRotator);
                    var scl = e.GetOrDefault("RelativeScale3D", FVector.OneVector);

                    // Instanced (foliage / grouped props): PerInstanceSMData holds
                    // every instance's own 4x4 transform, relative to the component.
                    var insts = new List<float[]>();
                    if (e is UInstancedStaticMeshComponent ism && ism.PerInstanceSMData != null)
                    {
                        foreach (var pi in ism.PerInstanceSMData)
                        {
                            var t = pi.TransformData;
                            insts.Add(new[]{ (float)t.Translation.X, (float)t.Translation.Y, (float)t.Translation.Z,
                                             (float)t.Rotation.X, (float)t.Rotation.Y, (float)t.Rotation.Z, (float)t.Rotation.W,
                                             (float)t.Scale3D.X, (float)t.Scale3D.Y, (float)t.Scale3D.Z });
                        }
                    }
                    // OverrideMaterials is a COMPONENT-level property, so the mesh's
                    // own material list does not mention it. Palworld uses it both for
                    // water (the waterfalls) and to turn an /Engine/BasicShapes/Cube
                    // into a LOCAL FOG VOLUME - geometry that must never be drawn as
                    // an opaque surface. Recording it here is what lets the terrain
                    // build tell those apart from real ground.
                    var om = e.GetOrDefault<FPackageIndex[]>("OverrideMaterials", null);
                    var ovr = om == null ? null
                        : string.Join(",", om.Select(x => x?.Name ?? ""));
                    // A mesh component that hangs off something other than the actor
                    // root carries a RelativeLocation in PARENT space, not world space.
                    var parent = e.GetOrDefault<FPackageIndex>("AttachParent", null);
                    outActors.Add(new
                    {
                        cell,
                        cls,
                        name = e.Name,
                        mesh = meshName,
                        meshPath = mp,
                        ovr,
                        attachParent = parent?.Name,
                        loc = new[] { loc.X, loc.Y, loc.Z },
                        rot = new[] { rot.Pitch, rot.Yaw, rot.Roll },
                        scale = new[] { scl.X, scl.Y, scl.Z },
                        instanceCount = insts.Count,
                        instances = insts,
                    });
                }
                else if (cls == "SceneComponent" || cls == "InstancedFoliageActor")
                {
                    var loc = e.GetOrDefault("RelativeLocation", FVector.ZeroVector);
                    var rot = e.GetOrDefault("RelativeRotation", FRotator.ZeroRotator);
                    var scl = e.GetOrDefault("RelativeScale3D", FVector.OneVector);
                    var root = e.GetOrDefault<FPackageIndex>("RootComponent", null);
                    outActors.Add(new
                    {
                        cell, cls, name = e.Name,
                        mesh = (string?)null, meshPath = root?.Name,
                        loc = new[] { loc.X, loc.Y, loc.Z },
                        rot = new[] { rot.Pitch, rot.Yaw, rot.Roll },
                        scale = new[] { scl.X, scl.Y, scl.Z },
                        instanceCount = 0,
                        instances = new List<float[]>(),
                    });
                }
                else if (cls.Contains("Landscape", StringComparison.OrdinalIgnoreCase))
                {
                    var loc = e.GetOrDefault("RelativeLocation", FVector.ZeroVector);
                    var scl = e.GetOrDefault("RelativeScale3D", FVector.OneVector);
                    var sectionBase = e.GetOrDefault("SectionBaseX", 0);
                    outActors.Add(new
                    {
                        cell, cls, name = e.Name,
                        mesh = (string?)null, meshPath = (string?)null,
                        loc = new[] { loc.X, loc.Y, loc.Z },
                        rot = new[] { 0f, 0f, 0f },
                        scale = new[] { scl.X, scl.Y, scl.Z },
                        instanceCount = 0,
                        instances = new List<float[]>(),
                        sectionBaseX = sectionBase,
                        props = e.Properties.Select(p => p.Name.Text).ToArray(),
                    });
                }
            }
        }
        var outPath = Path.Combine(OutDir, "cellactors.json");
        File.WriteAllText(outPath, JsonSerializer.Serialize(outActors,
            new JsonSerializerOptions { WriteIndented = false }));
        Console.Error.WriteLine($"wrote {outActors.Count} records -> {outPath}");
        return 0;
    }
}
