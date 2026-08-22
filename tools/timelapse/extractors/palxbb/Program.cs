// Measure the real bounding box of a buildable, from the pak.
//
// MapPal's objects.json carries a gray-box size per typeId. 30 of its 453 types
// have null dimensions and render magenta. The honest fix is not "read the
// primary mesh's bbox": a buildable is a Blueprint whose mesh COMPONENTS each
// carry their own RelativeLocation / RelativeRotation / RelativeScale3D, and
// several of these types are multi-component (statue + its plinth, conveyor +
// its arm) or apply a scale the mesh alone does not show. Taking one component's
// raw mesh bbox would be a fabricated number for those.
//
// So: walk the Blueprint's mesh components exactly as palxtex --bpmesh does,
// load each referenced mesh, take LOD0's real vertex extents, push the eight
// corners through that component's own transform, and union. The result is the
// Blueprint's own local-space AABB - measured, never assumed.
//
//   dotnet run -c Release --project palxbb -- <BP_Name> [BP_Name...]
//   dotnet run -c Release --project palxbb -- @list.txt
//
// Mappings.usmap is mandatory: Palworld cooks with PKG_UnversionedProperties,
// so without it every property read comes back empty and the tool would report
// "no components" for assets that are perfectly present.
//
// CONCURRENT PAK ACCESS: run with DOTNET_SYSTEM_IO_DISABLEFILELOCKING=1. The pak
// lives on an SMB mount; .NET takes an advisory flock on open, and while another
// CUE4Parse tool (palxground, palxterr, palxtex...) holds the pak every other
// tool fails with "The process cannot access the file because it is being used
// by another process" - for as long as that run lasts. The switch skips the
// flock; the pak is only ever read, so nothing is at risk. Plain read() from
// python was never blocked, which is what identified the lock as .NET-side.
//
// Writes bpbbox.json (new file; does not touch bpmesh_dump.json).
using System.Text.Json;
using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;
using CUE4Parse_Conversion.Dto;
using CUE4Parse_Conversion.Options;

namespace Palxbb;

public static class Program
{
    static readonly string Base = Environment.GetEnvironmentVariable("PALX_BASE") ?? throw new InvalidOperationException("PALX_BASE is not set - point it at your timelapse work directory");

    public class Comp
    {
        public string export { get; set; }
        public string exportClass { get; set; }
        public string mesh { get; set; }
        public string meshPath { get; set; }
        public string kind { get; set; }
        public bool visible { get; set; }
        public float[] loc_cm { get; set; }
        public float[] rot_pyr { get; set; }
        public float[] scale { get; set; }
        public float[] mesh_min_cm { get; set; }
        public float[] mesh_max_cm { get; set; }
        public float[] world_min_cm { get; set; }
        public float[] world_max_cm { get; set; }
        public int verts { get; set; }
        public string via { get; set; }
        public string err { get; set; }
    }

    public class Rec
    {
        public string bp { get; set; }
        public string vpath { get; set; }
        public List<Comp> comps { get; set; } = new();
        /// union over VISIBLE components that produced real vertex extents
        public float[] min_cm { get; set; }
        public float[] max_cm { get; set; }
        public float[] size_cm { get; set; }
        /// union including components flagged bVisible=false
        public float[] all_min_cm { get; set; }
        public float[] all_max_cm { get; set; }
        public int measured { get; set; }
        public int unmeasured { get; set; }
        public string err { get; set; }
    }

    static DefaultFileProvider Mount()
    {
        var pakDir = Path.Combine(Base, "mnt_d/SteamLibrary/steamapps/common/Palworld/Pal/Content/Paks");
        var p = new DefaultFileProvider(new DirectoryInfo(pakDir), SearchOption.TopDirectoryOnly,
            new VersionContainer(EGame.GAME_UE5_1), StringComparer.OrdinalIgnoreCase);
        p.Initialize();
        p.Mount();
        var usmap = Path.Combine(Base, "Mappings.usmap");
        if (!File.Exists(usmap)) throw new FileNotFoundException("Mappings.usmap missing - every property read would come back empty", usmap);
        p.MappingsContainer = new FileUsmapTypeMappingsProvider(usmap);
        p.ReadNaniteData = true;
        return p;
    }

    /// mesh name -> (min, max, verts, kind) from LOD0's actual vertex positions
    static readonly Dictionary<string, (float[] min, float[] max, int verts, string kind, string err)> MeshCache = new();

    static (float[] min, float[] max, int verts, string kind, string err) MeshExtents(
        DefaultFileProvider provider, string meshPath, string meshName)
    {
        var key = meshPath ?? meshName;
        if (MeshCache.TryGetValue(key, out var hit)) return hit;
        var res = Measure(provider, meshPath, meshName);
        MeshCache[key] = res;
        return res;
    }

    static (float[], float[], int, string, string) Measure(DefaultFileProvider provider, string meshPath, string meshName)
    {
        string vpath = null;
        if (meshPath != null)
        {
            // CUE4Parse object paths look like "Pal/Content/.../SM_X.SM_X"
            var p = meshPath.Contains('.') ? meshPath[..meshPath.LastIndexOf('.')] : meshPath;
            if (p.StartsWith("/Game/")) p = "Pal/Content/" + p["/Game/".Length..];
            if (provider.Files.ContainsKey(p + ".uasset")) vpath = p + ".uasset";
        }
        vpath ??= provider.Files.Keys.FirstOrDefault(k =>
            k.EndsWith("/" + meshName + ".uasset", StringComparison.OrdinalIgnoreCase));
        if (vpath == null) return (null, null, 0, null, "asset not found");
        try
        {
            var pkg = provider.LoadPackage(vpath);
            var sk = pkg.GetExports().OfType<USkeletalMesh>().FirstOrDefault();
            var sm = pkg.GetExports().OfType<UStaticMesh>().FirstOrDefault();
            FVector[] vpos;
            string kind;
            if (sk != null)
            {
                kind = "SK";
                using var dto = new SkeletalMeshDto(sk, EMeshQuality.All, ENaniteMeshFormat.NoNanite, false);
                if (dto.LODs.Count == 0) return (null, null, 0, kind, "no LODs");
                vpos = dto.LODs[0].Vertices.Select(v => v.Position).ToArray();
            }
            else if (sm != null)
            {
                kind = "SM";
                using var dto = new StaticMeshDto(sm, EMeshQuality.All, ENaniteMeshFormat.NaniteLast);
                if (dto.LODs.Count == 0) return (null, null, 0, kind, "no LODs");
                vpos = dto.LODs[0].Vertices.Select(v => v.Position).ToArray();
            }
            else return (null, null, 0, null, "no UStaticMesh or USkeletalMesh export");
            if (vpos.Length == 0) return (null, null, 0, kind, "empty geometry");
            var mn = new[] { vpos.Min(v => v.X), vpos.Min(v => v.Y), vpos.Min(v => v.Z) };
            var mx = new[] { vpos.Max(v => v.X), vpos.Max(v => v.Y), vpos.Max(v => v.Z) };
            return (mn, mx, vpos.Length, kind, null);
        }
        catch (Exception e) { return (null, null, 0, null, e.Message); }
    }

    /// Walk a blueprint's mesh components; follow imported BP packages one level
    /// when the BP has none of its own (identical rule to palxtex --bpmesh).
    static List<Comp> Walk(DefaultFileProvider provider, string vpath, int depth = 0, HashSet<string> seen = null)
    {
        seen ??= new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var outl = new List<Comp>();
        if (vpath == null || !seen.Add(vpath)) return outl;
        CUE4Parse.UE4.Assets.IPackage pkg;
        try { pkg = provider.LoadPackage(vpath); } catch { return outl; }

        foreach (var ex in pkg.GetExports())
        {
            var cls = ex.ExportType ?? "";
            if (!cls.Contains("StaticMeshComponent") && !cls.Contains("SkeletalMeshComponent")) continue;
            var mp = ex.GetOrDefault<FPackageIndex>("StaticMesh", null)
                     ?? ex.GetOrDefault<FPackageIndex>("SkeletalMesh", null);
            if (mp?.Name == null) continue;
            var r = ex.GetOrDefault("RelativeRotation", FRotator.ZeroRotator);
            var l = ex.GetOrDefault("RelativeLocation", FVector.ZeroVector);
            var s = ex.GetOrDefault("RelativeScale3D", FVector.OneVector);
            outl.Add(new Comp
            {
                export = ex.Name, exportClass = cls, mesh = mp.Name,
                meshPath = mp.ResolvedObject?.GetPathName(),
                rot_pyr = new[] { r.Pitch, r.Yaw, r.Roll },
                loc_cm = new[] { l.X, l.Y, l.Z },
                scale = new[] { s.X, s.Y, s.Z },
                visible = ex.GetOrDefault("bVisible", true),
                via = depth == 0 ? null : vpath,
            });
        }
        if (outl.Count > 0 || depth >= 1) return outl;

        if (pkg is CUE4Parse.UE4.Assets.IoPackage io)
        {
            foreach (var dep in io.ImportedPackages.Value)
            {
                var op = dep?.Name;
                if (op == null || !op.StartsWith("/Game/")) continue;
                var n = op[(op.LastIndexOf('/') + 1)..];
                if (!n.StartsWith("BP_")) continue;
                var vp = "Pal/Content/" + op["/Game/".Length..] + ".uasset";
                if (!provider.Files.ContainsKey(vp)) continue;
                outl.AddRange(Walk(provider, vp, depth + 1, seen));
                if (outl.Count > 0) break;
            }
        }
        return outl;
    }

    /// Transform the mesh's 8 local corners by scale -> rotation -> translation
    /// and return the AABB of the result. Component rotations here are real
    /// (yaw -90 on wall kits, small pitches on props), so the corner transform
    /// is needed - scaling the axis extents alone would be wrong for any
    /// non-axis-aligned component.
    static (float[] mn, float[] mx) Xform(float[] min, float[] max, float[] loc, float[] rot, float[] scale)
    {
        var q = new FRotator(rot[0], rot[1], rot[2]).Quaternion();
        float[] mn = { float.MaxValue, float.MaxValue, float.MaxValue };
        float[] mx = { float.MinValue, float.MinValue, float.MinValue };
        for (int i = 0; i < 8; i++)
        {
            var v = new FVector(
                (i & 1) == 0 ? min[0] : max[0],
                (i & 2) == 0 ? min[1] : max[1],
                (i & 4) == 0 ? min[2] : max[2]);
            v = new FVector(v.X * scale[0], v.Y * scale[1], v.Z * scale[2]);
            v = q.RotateVector(v);
            v = new FVector(v.X + loc[0], v.Y + loc[1], v.Z + loc[2]);
            mn[0] = Math.Min(mn[0], v.X); mn[1] = Math.Min(mn[1], v.Y); mn[2] = Math.Min(mn[2], v.Z);
            mx[0] = Math.Max(mx[0], v.X); mx[1] = Math.Max(mx[1], v.Y); mx[2] = Math.Max(mx[2], v.Z);
        }
        return (mn, mx);
    }

    public static int Main(string[] args)
    {
        var wants = new List<string>();
        foreach (var a in args)
            if (a.StartsWith("@")) wants.AddRange(File.ReadAllLines(a[1..]).Select(x => x.Trim()).Where(x => x.Length > 0));
            else wants.Add(a);
        if (wants.Count == 0) { Console.Error.WriteLine("usage: palxbb <BP_Name|@list.txt>..."); return 2; }

        var provider = Mount();
        Console.Error.WriteLine($"mounted files={provider.Files.Count}");
        var res = new Dictionary<string, Rec>();
        foreach (var want in wants)
        {
            var rec = new Rec { bp = want };
            var leaf = "/" + want + ".uasset";
            rec.vpath = provider.Files.Keys.FirstOrDefault(k => k.EndsWith(leaf, StringComparison.OrdinalIgnoreCase));
            if (rec.vpath == null) { rec.err = "blueprint asset not found"; res[want] = rec; Console.WriteLine($"MISS {want}"); continue; }
            rec.comps = Walk(provider, rec.vpath);
            float[] mn = null, mx = null, amn = null, amx = null;
            foreach (var c in rec.comps)
            {
                var (cmin, cmax, verts, kind, err) = MeshExtents(provider, c.meshPath, c.mesh);
                c.kind = kind; c.err = err; c.verts = verts;
                if (cmin == null) { rec.unmeasured++; continue; }
                c.mesh_min_cm = cmin; c.mesh_max_cm = cmax;
                var (wmn, wmx) = Xform(cmin, cmax, c.loc_cm, c.rot_pyr, c.scale);
                c.world_min_cm = wmn; c.world_max_cm = wmx;
                rec.measured++;
                amn = Merge(amn, wmn, Math.Min); amx = Merge(amx, wmx, Math.Max);
                if (!c.visible) continue;
                mn = Merge(mn, wmn, Math.Min); mx = Merge(mx, wmx, Math.Max);
            }
            rec.all_min_cm = amn; rec.all_max_cm = amx;
            // If every component is bVisible=false the visible union is empty;
            // fall back to the full union rather than reporting nothing.
            rec.min_cm = mn ?? amn; rec.max_cm = mx ?? amx;
            if (rec.min_cm != null)
                rec.size_cm = new[] { rec.max_cm[0] - rec.min_cm[0], rec.max_cm[1] - rec.min_cm[1], rec.max_cm[2] - rec.min_cm[2] };
            else rec.err ??= rec.comps.Count == 0 ? "no mesh components" : "no component could be measured";
            res[want] = rec;
            Console.WriteLine($"{want,-46} comps={rec.comps.Count,2} measured={rec.measured,2} "
                + (rec.size_cm == null ? $"UNRESOLVED ({rec.err})"
                   : $"size={rec.size_cm[0]:F1} x {rec.size_cm[1]:F1} x {rec.size_cm[2]:F1}  min=({rec.min_cm[0]:F1},{rec.min_cm[1]:F1},{rec.min_cm[2]:F1})"));
        }
        File.WriteAllText(Path.Combine(Base, "bpbbox.json"),
            JsonSerializer.Serialize(res, new JsonSerializerOptions { WriteIndented = true }));
        Console.Error.WriteLine($"-> bpbbox.json  resolved={res.Values.Count(r => r.size_cm != null)}/{res.Count}");
        return 0;
    }

    static float[] Merge(float[] a, float[] b, Func<float, float, float> f)
        => a == null ? (float[])b.Clone() : new[] { f(a[0], b[0]), f(a[1], b[1]), f(a[2], b[2]) };
}
