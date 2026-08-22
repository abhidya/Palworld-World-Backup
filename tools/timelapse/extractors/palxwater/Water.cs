// WATER sweep over the cooked pak.
//
// Everything the earlier passes learned about water came through a
// StaticMeshComponent-only filter, so anything authored as a Blueprint actor,
// a volume, or a material-only surface was invisible to them. These modes are
// deliberately UNFILTERED.
//
//   --files   <pat...>            list virtual paths matching any pattern
//   --exports <umap-substr...>    every export of the matched umaps, with class,
//                                 outer, transform, mesh ref, override materials
//                                 and the full property-name list
//   --matparams <asset-pat...>    every scalar / vector / texture parameter of a
//                                 material (instance), plus blend mode + parent
using System.Text.Json;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;

namespace Palx;

public static class Water
{
    static string OutDir => Environment.GetEnvironmentVariable("PALX_OUT") ?? Program.BaseDir;

    public static int Run(string[] args)
    {
        var mode = args[0];
        var provider = Level.MakeProvider(Environment.GetEnvironmentVariable("PALX_ROOT")
            ?? Path.Combine(Program.BaseDir, "rawassets"));
        Console.Error.WriteLine($"provider files: {provider.Files.Count}");
        // PROVE the mappings took: without them every export comes back with
        // zero properties, which is exactly how a silent empty sweep happens.
        Console.Error.WriteLine($"mappings: {(provider.MappingsContainer?.MappingsForGame != null ? "LOADED" : "NULL")}");

        var pats = args.Skip(1).ToArray();

        if (mode == "--files")
        {
            int n = 0;
            foreach (var k in provider.Files.Keys.OrderBy(x => x))
                if (pats.Length == 0 || pats.Any(w => k.Contains(w, StringComparison.OrdinalIgnoreCase)))
                { Console.WriteLine(k); n++; }
            Console.Error.WriteLine($"{n} matches");
            return 0;
        }

        if (mode == "--matparams")
        {
            var outp = new List<object>();
            var assets = provider.Files.Keys
                .Where(k => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)
                            && pats.Any(w => k.Contains(w, StringComparison.OrdinalIgnoreCase)))
                .OrderBy(k => k).ToList();
            Console.Error.WriteLine($"matched {assets.Count} assets");
            foreach (var vp in assets)
            {
                try
                {
                    var pkg = provider.LoadPackage(vp);
                    foreach (var ex in pkg.GetExports())
                    {
                        if (ex is not UMaterialInterface umat) continue;
                        var rec = new Dictionary<string, object>
                        {
                            ["asset"] = vp,
                            ["name"] = ex.Name,
                            ["class"] = ex.ExportType,
                        };
                        // Raw MaterialInstance parameter arrays: the literal
                        // authored overrides, straight off the cooked asset.
                        if (ex is UMaterialInstanceConstant mic)
                        {
                            rec["parent"] = mic.Parent?.GetPathName();
                            rec["scalars"] = mic.ScalarParameterValues?.Select(p =>
                                new { name = p.ParameterInfo?.Name.Text, value = p.ParameterValue }).ToArray();
                            rec["vectors"] = mic.VectorParameterValues?.Select(p =>
                                new { name = p.ParameterInfo?.Name.Text,
                                      rgba = p.ParameterValue is { } c
                                          ? new[] { c.R, c.G, c.B, c.A } : null }).ToArray();
                            rec["textures"] = mic.TextureParameterValues?.Select(p =>
                                new { name = p.ParameterInfo?.Name.Text,
                                      tex = p.ParameterValue?.ResolvedObject?.GetPathName()
                                            ?? p.ParameterValue?.Name }).ToArray();
                        }
                        // Resolved parameters, i.e. what the surface actually
                        // renders with once the parent chain is folded in.
                        try
                        {
                            var p2 = new CMaterialParams2();
                            umat.GetParams(p2, EMaterialDepth.AllLayersNoRef);
                            rec["blend"] = p2.BlendMode.ToString();
                            rec["shadingModel"] = p2.ShadingModel.ToString();
                            rec["resolvedScalars"] = p2.Scalars.ToDictionary(k => k.Key, v => v.Value);
                            rec["resolvedVectors"] = p2.Colors.ToDictionary(k => k.Key,
                                v => new[] { v.Value.R, v.Value.G, v.Value.B, v.Value.A });
                            rec["resolvedTextures"] = p2.Textures.ToDictionary(k => k.Key,
                                v => v.Value?.GetPathName());
                        }
                        catch (Exception ex2) { rec["paramErr"] = ex2.Message; }
                        // Everything the material samples, parameterised or not.
                        try
                        {
                            var refs = new List<UUnrealMaterial>();
                            umat.AppendReferencedTextures(refs, true);
                            rec["referencedTextures"] = refs.Select(t => t.GetPathName()).ToArray();
                        }
                        catch { }
                        outp.Add(rec);
                        Console.WriteLine($"### {ex.Name}  ({ex.ExportType})  {vp}");
                    }
                }
                catch (Exception e) { Console.Error.WriteLine($"  SKIP {vp}: {e.Message}"); }
            }
            var f = Path.Combine(OutDir, "water_matparams.json");
            File.WriteAllText(f, JsonSerializer.Serialize(outp,
                new JsonSerializerOptions { WriteIndented = true }));
            Console.Error.WriteLine($"wrote {outp.Count} materials -> {f}");
            return 0;
        }

        if (mode == "--spline")
        {
            // BP_UltimateRiverTool_C emits its rivers as SplineMeshComponents:
            // ONE source mesh (SM_River_Plane) bent along a per-segment Hermite
            // spline. The class name does not contain "StaticMeshComponent", so
            // every StaticMesh-filtered sweep of these cells dropped all 472 of
            // them — which is why "no river" kept coming back. Dump each
            // segment's FSplineMeshParams verbatim so the deformation can be
            // replayed exactly (build_terrain.py).
            var maps = provider.Files.Keys
                .Where(k => k.EndsWith(".umap", StringComparison.OrdinalIgnoreCase)
                            && (pats.Length == 0 || pats.Any(w =>
                                k[(k.LastIndexOf('/') + 1)..].Contains(w, StringComparison.OrdinalIgnoreCase))))
                .OrderBy(k => k).ToList();
            Console.Error.WriteLine($"matched {maps.Count} umaps");
            var outp = new List<object>();
            int done = 0;
            foreach (var vp in maps)
            {
                try
                {
                    var pkg = provider.LoadPackage(vp);
                    var cell = vp[(vp.LastIndexOf('/') + 1)..];
                    foreach (var ex in pkg.GetExports())
                    {
                        if (!(ex.ExportType ?? "").Contains("SplineMeshComponent",
                                StringComparison.OrdinalIgnoreCase)) continue;
                        var sp = ex.GetOrDefault<FStructFallback>("SplineParams", null);
                        float[] V2(string n)
                        {
                            var v = sp?.GetOrDefault<FVector2D>(n, new FVector2D(0, 0));
                            return v == null ? new float[] { 0, 0 } : new[] { (float)v.Value.X, (float)v.Value.Y };
                        }
                        float[] V3(string n)
                        {
                            var v = sp?.GetOrDefault("" + n, FVector.ZeroVector) ?? FVector.ZeroVector;
                            return new[] { (float)v.X, (float)v.Y, (float)v.Z };
                        }
                        var loc = ex.GetOrDefault("RelativeLocation", FVector.ZeroVector);
                        var rot = ex.GetOrDefault("RelativeRotation", FRotator.ZeroRotator);
                        var scl = ex.GetOrDefault("RelativeScale3D", FVector.OneVector);
                        var up = ex.GetOrDefault("SplineUpDir", new FVector(0, 0, 1));
                        var sm = ex.GetOrDefault<FPackageIndex>("StaticMesh", null);
                        var om = ex.GetOrDefault<FPackageIndex[]>("OverrideMaterials", null);
                        outp.Add(new
                        {
                            cell,
                            cls = ex.ExportType,
                            name = ex.Name,
                            outer = ex.Outer?.Name.Text,
                            mesh = sm?.Name,
                            meshPath = sm?.ResolvedObject?.GetPathName(),
                            overrideMaterials = om?.Select(m => m?.ResolvedObject?.GetPathName() ?? m?.Name).ToArray(),
                            // The river tool assigns a MaterialInstanceDynamic
                            // built in the construction script, so the shipped
                            // colour lives on its PARENT asset, not on the MID.
                            matParent = om != null && om.Length > 0
                                && om[0]?.ResolvedObject?.Object?.Value is UMaterialInstance mid
                                ? mid.Parent?.GetPathName() : null,
                            loc = new[] { loc.X, loc.Y, loc.Z },
                            rot = new[] { rot.Pitch, rot.Yaw, rot.Roll },
                            scale = new[] { scl.X, scl.Y, scl.Z },
                            splineUpDir = new[] { up.X, up.Y, up.Z },
                            forwardAxis = ex.GetOrDefault<string>("ForwardAxis", null)
                                          ?? ex.GetOrDefault<int?>("ForwardAxis", null)?.ToString(),
                            boundaryMin = ex.GetOrDefault<float?>("SplineBoundaryMin", null),
                            boundaryMax = ex.GetOrDefault<float?>("SplineBoundaryMax", null),
                            smoothInterpRollScale = ex.GetOrDefault<bool?>("bSmoothInterpRollScale", null),
                            startPos = V3("StartPos"), startTangent = V3("StartTangent"),
                            endPos = V3("EndPos"), endTangent = V3("EndTangent"),
                            startScale = V2("StartScale"), endScale = V2("EndScale"),
                            startOffset = V2("StartOffset"), endOffset = V2("EndOffset"),
                            startRoll = sp?.GetOrDefault<float>("StartRoll", 0f) ?? 0f,
                            endRoll = sp?.GetOrDefault<float>("EndRoll", 0f) ?? 0f,
                            hasParams = sp != null,
                            props = ex.Properties.Select(p => p.Name.Text).ToArray(),
                        });
                    }
                }
                catch (Exception e) { Console.Error.WriteLine($"  SKIP {vp}: {e.Message}"); }
                if (++done % 50 == 0) Console.Error.WriteLine($"  {done}/{maps.Count}");
            }
            var fs = Path.Combine(OutDir,
                (Environment.GetEnvironmentVariable("PALX_NAME") ?? "water_splines") + ".json");
            File.WriteAllText(fs, JsonSerializer.Serialize(outp,
                new JsonSerializerOptions { WriteIndented = false }));
            Console.Error.WriteLine($"wrote {outp.Count} spline meshes -> {fs}");
            return 0;
        }

        if (mode == "--exports")
        {
            var maps = provider.Files.Keys
                .Where(k => k.EndsWith(".umap", StringComparison.OrdinalIgnoreCase)
                            && (pats.Length == 0 || pats.Any(w =>
                                k[(k.LastIndexOf('/') + 1)..].Contains(w, StringComparison.OrdinalIgnoreCase))))
                .OrderBy(k => k).ToList();
            Console.Error.WriteLine($"matched {maps.Count} umaps");
            var outp = new List<object>();
            int done = 0;
            foreach (var vp in maps)
            {
                try
                {
                    var pkg = provider.LoadPackage(vp);
                    var cell = vp[(vp.LastIndexOf('/') + 1)..];
                    foreach (var ex in pkg.GetExports())
                    {
                        var loc = ex.GetOrDefault("RelativeLocation", FVector.ZeroVector);
                        var rot = ex.GetOrDefault("RelativeRotation", FRotator.ZeroRotator);
                        var scl = ex.GetOrDefault("RelativeScale3D", FVector.OneVector);
                        var sm = ex.GetOrDefault<FPackageIndex>("StaticMesh", null);
                        var om = ex.GetOrDefault<FPackageIndex[]>("OverrideMaterials", null);
                        // Instanced components carry the real geometry: a tiled
                        // water plane is ONE mesh plus N per-instance transforms.
                        var insts = new List<float[]>();
                        if (ex is CUE4Parse.UE4.Assets.Exports.Component.StaticMesh
                                  .UInstancedStaticMeshComponent ism
                            && ism.PerInstanceSMData != null)
                            foreach (var pi in ism.PerInstanceSMData)
                            {
                                var t = pi.TransformData;
                                insts.Add(new[]{ (float)t.Translation.X, (float)t.Translation.Y, (float)t.Translation.Z,
                                                 (float)t.Rotation.X, (float)t.Rotation.Y, (float)t.Rotation.Z, (float)t.Rotation.W,
                                                 (float)t.Scale3D.X, (float)t.Scale3D.Y, (float)t.Scale3D.Z });
                            }
                        outp.Add(new
                        {
                            tileCount = ex.GetOrDefault<int?>("TileCount", null),
                            meshScaleOffset = ex.GetOrDefault<float?>("MeshScaleOffset", null),
                            depth = ex.GetOrDefault<float?>("Depth", null),
                            worldOceanPlane = ex.GetOrDefault<bool?>("bWorldOceanPlane", null),
                            waterMaterial = ex.GetOrDefault<FPackageIndex>("WaterMaterial", null)?.ResolvedObject?.GetPathName(),
                            waterPlaneMesh = ex.GetOrDefault<FPackageIndex>("WaterPlaneMesh", null)?.ResolvedObject?.GetPathName(),
                            rootComponent = ex.GetOrDefault<FPackageIndex>("RootComponent", null)?.Name,
                            attachParent = ex.GetOrDefault<FPackageIndex>("AttachParent", null)?.Name,
                            instanceCount = insts.Count,
                            instances = insts,
                            cell,
                            cls = ex.ExportType,
                            name = ex.Name,
                            outer = ex.Outer?.Name,
                            loc = new[] { loc.X, loc.Y, loc.Z },
                            rot = new[] { rot.Pitch, rot.Yaw, rot.Roll },
                            scale = new[] { scl.X, scl.Y, scl.Z },
                            mesh = sm?.Name,
                            meshPath = sm?.ResolvedObject?.GetPathName(),
                            overrideMaterials = om?.Select(m => m?.ResolvedObject?.GetPathName() ?? m?.Name).ToArray(),
                            props = ex.Properties.Select(p => p.Name.Text).ToArray(),
                        });
                    }
                }
                catch (Exception e) { Console.Error.WriteLine($"  SKIP {vp}: {e.Message}"); }
                if (++done % 50 == 0) Console.Error.WriteLine($"  {done}/{maps.Count}");
            }
            var name = Environment.GetEnvironmentVariable("PALX_NAME") ?? "water_exports";
            var f = Path.Combine(OutDir, name + ".json");
            File.WriteAllText(f, JsonSerializer.Serialize(outp,
                new JsonSerializerOptions { WriteIndented = false }));
            Console.Error.WriteLine($"wrote {outp.Count} exports from {maps.Count} umaps -> {f}");
            return 0;
        }

        Console.Error.WriteLine("unknown mode " + mode);
        return 1;
    }
}
