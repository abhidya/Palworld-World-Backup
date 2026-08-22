using System.Text.Json;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;

namespace Palxtex;

public static class Bp
{
    public class Comp
    {
        public string export { get; set; }
        public string exportClass { get; set; }
        public string mesh { get; set; }
        public string meshPath { get; set; }
        public float[] rot_pyr { get; set; }   // UE FRotator degrees: Pitch, Yaw, Roll
        public float[] loc_cm { get; set; }
        public float[] scale { get; set; }
    }
    public class Rec
    {
        public string type { get; set; }
        public string bp { get; set; }
        public string err { get; set; }
        public List<Comp> comps { get; set; } = new();
    }

    public static int Run(string[] args)
    {
        var provider = P.Mount();
        Console.Error.WriteLine($"mounted files={provider.Files.Count}");
        var manifest = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(
            File.ReadAllText(Path.Combine(P.Base, "mesh_manifest.json")));

        var outp = new List<Rec>();
        int i = 0;
        foreach (var (type, e) in manifest)
        {
            if (++i % 50 == 0) Console.Error.WriteLine($"  {i}/{manifest.Count}");
            if (!e.TryGetProperty("blueprint", out var bpEl)) continue;
            var bp = bpEl.GetString();
            if (string.IsNullOrEmpty(bp)) continue;
            var rec = new Rec { type = type, bp = bp };
            try
            {
                var vpath = "Pal/Content/" + bp.Substring("/Game/".Length) + ".uasset";
                if (!provider.Files.ContainsKey(vpath))
                {
                    var leaf = "/" + bp[(bp.LastIndexOf('/') + 1)..] + ".uasset";
                    vpath = provider.Files.Keys.FirstOrDefault(k => k.EndsWith(leaf, StringComparison.OrdinalIgnoreCase));
                }
                if (vpath == null) { rec.err = "bp package not found"; outp.Add(rec); continue; }

                foreach (var ex in provider.LoadPackage(vpath).GetExports())
                {
                    var cls = ex.ExportType ?? "";
                    if (!cls.Contains("StaticMeshComponent") && !cls.Contains("SkeletalMeshComponent")) continue;
                    var mp = ex.GetOrDefault<FPackageIndex>("StaticMesh", null)
                             ?? ex.GetOrDefault<FPackageIndex>("SkeletalMesh", null);
                    if (mp?.Name == null) continue;
                    var r = ex.GetOrDefault("RelativeRotation", FRotator.ZeroRotator);
                    var l = ex.GetOrDefault("RelativeLocation", FVector.ZeroVector);
                    var s = ex.GetOrDefault("RelativeScale3D", FVector.OneVector);
                    rec.comps.Add(new Comp
                    {
                        export = ex.Name, exportClass = cls, mesh = mp.Name,
                        meshPath = mp.ResolvedObject?.GetPathName(),
                        rot_pyr = new[] { r.Pitch, r.Yaw, r.Roll },
                        loc_cm = new[] { l.X, l.Y, l.Z },
                        scale = new[] { s.X, s.Y, s.Z },
                    });
                }
                if (rec.comps.Count == 0) rec.err = "no mesh component with a mesh set";
            }
            catch (Exception ex) { rec.err = ex.GetType().Name + ": " + ex.Message; }
            outp.Add(rec);
        }
        File.WriteAllText(Path.Combine(P.Base, "bp_xform.json"),
            JsonSerializer.Serialize(outp, new JsonSerializerOptions { WriteIndented = true }));
        Console.WriteLine($"types={outp.Count} ok={outp.Count(x => x.err == null)} comps={outp.Sum(x => x.comps.Count)}");
        return 0;
    }
}
