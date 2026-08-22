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

namespace Palx;

public static class Level
{
    public static DefaultFileProvider MakeProvider(string root)
    {
        var provider = new DefaultFileProvider(new DirectoryInfo(root), SearchOption.AllDirectories,
            new VersionContainer(EGame.GAME_UE5_1), StringComparer.OrdinalIgnoreCase);
        provider.Initialize();
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

    public static int Run(string[] args)
    {
        var mode = args[0];
        var provider = MakeProvider(Path.Combine(Program.BaseDir, "rawassets"));
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
                    try
                    {
                        var dto = new CUE4Parse_Conversion.Dto.LandscapeMeshDto(proxy,
                            CUE4Parse_Conversion.Options.ELandscapeFlags.Mesh);
                        var lod = dto.LODs[0];
                        var pos = new float[lod.Vertices.Length * 3];
                        for (int i = 0; i < lod.Vertices.Length; i++)
                        {
                            var v = lod.Vertices[i].Position;
                            pos[i * 3] = v.X; pos[i * 3 + 1] = v.Y; pos[i * 3 + 2] = v.Z;
                        }
                        var outDir = Path.Combine(Program.BaseDir, "terrain_meshes");
                        Directory.CreateDirectory(outDir);
                        var nm = $"{cell[..cell.LastIndexOf('.')]}__{proxy.Name}";
                        Program.WriteGlbPublic(Path.Combine(outDir, nm + ".glb"), pos, lod.Indices, nm);
                        recs.Add(new { cell, actor = proxy.Name, glb = "terrain_meshes/" + nm + ".glb",
                            verts = lod.Vertices.Length, tris = lod.Indices.Length / 3,
                            loc = new[] { loc.X, loc.Y, loc.Z }, scale = new[] { scl.X, scl.Y, scl.Z },
                            componentSizeQuads = proxy.ComponentSizeQuads,
                            components = proxy.LandscapeComponents.Length });
                        Console.WriteLine($"OK  {nm}  {lod.Vertices.Length} v  loc={loc}  scale={scl}");
                    }
                    catch (Exception ex)
                    {
                        Console.WriteLine($"FAIL {cell} {proxy.Name}: {ex.GetType().Name}: {ex.Message}");
                    }
                }
            }
            File.WriteAllText(Path.Combine(Program.BaseDir, "terrain_index.json"),
                JsonSerializer.Serialize(recs, new JsonSerializerOptions { WriteIndented = true }));
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
                    outActors.Add(new
                    {
                        cell,
                        cls,
                        name = e.Name,
                        mesh = meshName,
                        meshPath = mp,
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
        var outPath = Path.Combine(Program.BaseDir, "cellactors.json");
        File.WriteAllText(outPath, JsonSerializer.Serialize(outActors,
            new JsonSerializerOptions { WriteIndented = false }));
        Console.Error.WriteLine($"wrote {outActors.Count} records -> {outPath}");
        return 0;
    }
}
