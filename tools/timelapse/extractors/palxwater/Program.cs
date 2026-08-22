using System.Text;
using System.Text.Json;
using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Versions;
using CUE4Parse_Conversion.Dto;
using CUE4Parse_Conversion.Options;

namespace Palx;

public static class Program
{
    static readonly string Base = Environment.GetEnvironmentVariable("PALX_BASE") ?? throw new InvalidOperationException("PALX_BASE is not set - point it at your timelapse work directory");
    public static string BaseDir => Base;

    public static int Main(string[] args)
    {
        // World Partition cell reader (Level.cs) — terrain/foliage work.
        if (args.Length > 0 && args[0] == "--waterext") return WaterExtract.Run(args);
        if (args.Length > 0 && (args[0] == "--files" || args[0] == "--exports" || args[0] == "--spline" || args[0] == "--matparams"))
            return Water.Run(args);
        if (args.Length > 0 && args[0].StartsWith("--")) return Level.Run(args);

        var targetsFile = args.Length > 0 ? args[0] : Path.Combine(Base, "c4targets.json");
        var targets = JsonSerializer.Deserialize<List<Target>>(File.ReadAllText(targetsFile))!;

        var provider = new DefaultFileProvider(
            new DirectoryInfo(Path.Combine(Base, "rawassets")),
            SearchOption.AllDirectories,
            new VersionContainer(EGame.GAME_UE5_1),
            StringComparer.OrdinalIgnoreCase);
        provider.Initialize();
        provider.MappingsContainer = new FileUsmapTypeMappingsProvider(Path.Combine(Base, "Mappings.usmap"));
        provider.ReadNaniteData = true;
        Console.Error.WriteLine($"provider files: {provider.Files.Count}");

        // index by lowercased "/name.uasset" suffix -> full virtual path
        var byName = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var k in provider.Files.Keys)
        {
            if (!k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)) continue;
            var slash = k.LastIndexOf('/');
            var n = slash >= 0 ? k[(slash + 1)..] : k;
            byName.TryAdd(n, k);
        }

        var results = new List<Result>();
        foreach (var t in targets)
        {
            var r = new Result { name = t.name, meshPath = t.meshPath, kind = t.kind, outDir = t.outDir };
            try
            {
                var leaf = t.meshPath[(t.meshPath.LastIndexOf('/') + 1)..] + ".uasset";
                if (!byName.TryGetValue(leaf, out var vpath))
                {
                    r.err = "asset not found in loose dir"; results.Add(r); Report(r); continue;
                }
                r.vpath = vpath;
                var pkg = provider.LoadPackage(vpath);

                float[] pos; uint[] idx; bool isNanite = false; int lodCount;

                // auto-detect: manifests mislabel some animated props (SK_*) as static meshes
                var skMesh = pkg.GetExports().OfType<USkeletalMesh>().FirstOrDefault();
                var smMesh = pkg.GetExports().OfType<UStaticMesh>().FirstOrDefault();
                var kind = skMesh != null ? "SK" : smMesh != null ? "SM" : null;
                if (kind == null) { r.err = "no UStaticMesh or USkeletalMesh export"; results.Add(r); Report(r); continue; }
                r.kind = kind;

                if (kind == "SK")
                {
                    var mesh = skMesh!;
                    using var dto = new SkeletalMeshDto(mesh, EMeshQuality.All, ENaniteMeshFormat.NoNanite, false);
                    if (dto.LODs.Count == 0) { r.err = "no LODs in dto"; results.Add(r); Report(r); continue; }
                    var lod = dto.LODs[0];
                    lodCount = dto.LODs.Count;
                    isNanite = lod.IsNanite;
                    pos = Flatten(lod.Vertices.Select(v => v.Position).ToArray());
                    idx = lod.Indices;
                }
                else
                {
                    var mesh = smMesh!;
                    // NaniteLast: classic LOD0 wins when present; Nanite geometry only if there is no classic render data
                    using var dto = new StaticMeshDto(mesh, EMeshQuality.All, ENaniteMeshFormat.NaniteLast);
                    if (dto.LODs.Count == 0) { r.err = "no LODs in dto"; results.Add(r); Report(r); continue; }
                    var lod = dto.LODs[0];
                    lodCount = dto.LODs.Count;
                    isNanite = lod.IsNanite;
                    pos = Flatten(lod.Vertices.Select(v => v.Position).ToArray());
                    idx = lod.Indices;
                }

                if (pos.Length == 0 || idx.Length == 0) { r.err = "empty geometry"; results.Add(r); Report(r); continue; }

                var outDir = Path.Combine(Base, t.outDir);
                Directory.CreateDirectory(outDir);
                var glb = Path.Combine(outDir, t.name + ".glb");
                WriteGlb(glb, pos, idx, t.name);

                r.ok = true;
                r.verts = pos.Length / 3;
                r.tris = idx.Length / 3;
                r.nanite = isNanite;
                r.lods = lodCount;
                r.glb = Path.GetRelativePath(Base, glb);
                r.bbox_cm = BBox(pos);
                r.min_cm = MinV(pos);
                r.max_cm = MaxV(pos);
            }
            catch (Exception ex)
            {
                r.err = ex.GetType().Name + ": " + ex.Message;
            }
            results.Add(r); Report(r);
        }

        File.WriteAllText(Path.Combine(Base, Path.GetFileNameWithoutExtension(targetsFile) + "_report.json"),
            JsonSerializer.Serialize(results, new JsonSerializerOptions { WriteIndented = true }));
        Console.Error.WriteLine($"done: {results.Count(x => x.ok)}/{results.Count}");
        return 0;
    }

    static void Report(Result r)
    {
        if (r.ok) Console.WriteLine($"OK   {r.name,-46} {r.verts,8} v {r.tris,8} t  nanite={r.nanite,-5} bbox={string.Join(",", r.bbox_cm!.Select(x => x.ToString("F1")))}");
        else Console.WriteLine($"FAIL {r.name,-46} {r.err}");
    }

    static float[] Flatten(CUE4Parse.UE4.Objects.Core.Math.FVector[] v)
    {
        var o = new float[v.Length * 3];
        for (int i = 0; i < v.Length; i++) { o[i * 3] = v[i].X; o[i * 3 + 1] = v[i].Y; o[i * 3 + 2] = v[i].Z; }
        return o;
    }

    static float[] MinV(float[] p)
    {
        var m = new[] { float.MaxValue, float.MaxValue, float.MaxValue };
        for (int i = 0; i < p.Length; i += 3) for (int k = 0; k < 3; k++) if (p[i + k] < m[k]) m[k] = p[i + k];
        return m;
    }
    static float[] MaxV(float[] p)
    {
        var m = new[] { float.MinValue, float.MinValue, float.MinValue };
        for (int i = 0; i < p.Length; i += 3) for (int k = 0; k < 3; k++) if (p[i + k] > m[k]) m[k] = p[i + k];
        return m;
    }
    static float[] BBox(float[] p)
    {
        var lo = MinV(p); var hi = MaxV(p);
        return new[] { hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2] };
    }

    /// Vertex normals from area-weighted face normals (same convention as the python extractor).
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

    public static void WriteGlbPublic(string path, float[] pos, uint[] idx, string name) => WriteGlb(path, pos, idx, name);

    static void WriteGlb(string path, float[] pos, uint[] idx, string name)
    {
        var nrm = Normals(pos, idx);
        int nv = pos.Length / 3;
        bool u32 = nv > 65535;

        var pb = new byte[pos.Length * 4]; Buffer.BlockCopy(pos, 0, pb, 0, pb.Length);
        var nb = new byte[nrm.Length * 4]; Buffer.BlockCopy(nrm, 0, nb, 0, nb.Length);
        byte[] ib;
        if (u32) { ib = new byte[idx.Length * 4]; Buffer.BlockCopy(idx, 0, ib, 0, ib.Length); }
        else
        {
            var s = new ushort[idx.Length];
            for (int i = 0; i < idx.Length; i++) s[i] = (ushort)idx[i];
            ib = new byte[s.Length * 2]; Buffer.BlockCopy(s, 0, ib, 0, ib.Length);
        }
        if (ib.Length % 4 != 0) Array.Resize(ref ib, ib.Length + (4 - ib.Length % 4));

        var lo = MinV(pos); var hi = MaxV(pos);
        var bin = new byte[pb.Length + nb.Length + ib.Length];
        Buffer.BlockCopy(pb, 0, bin, 0, pb.Length);
        Buffer.BlockCopy(nb, 0, bin, pb.Length, nb.Length);
        Buffer.BlockCopy(ib, 0, bin, pb.Length + nb.Length, ib.Length);

        string F(float v) => v.ToString("R", System.Globalization.CultureInfo.InvariantCulture);
        var json = "{\"asset\":{\"version\":\"2.0\",\"generator\":\"mappal-cue4parse\"},"
            + "\"scene\":0,\"scenes\":[{\"nodes\":[0]}],\"nodes\":[{\"mesh\":0,\"name\":\"" + name + "\"}],"
            + "\"meshes\":[{\"name\":\"" + name + "\",\"primitives\":[{\"attributes\":{\"POSITION\":0,\"NORMAL\":1},\"indices\":2}]}],"
            + "\"buffers\":[{\"byteLength\":" + bin.Length + "}],"
            + "\"bufferViews\":["
            + "{\"buffer\":0,\"byteOffset\":0,\"byteLength\":" + pb.Length + ",\"target\":34962},"
            + "{\"buffer\":0,\"byteOffset\":" + pb.Length + ",\"byteLength\":" + nb.Length + ",\"target\":34962},"
            + "{\"buffer\":0,\"byteOffset\":" + (pb.Length + nb.Length) + ",\"byteLength\":" + ib.Length + ",\"target\":34963}],"
            + "\"accessors\":["
            + "{\"bufferView\":0,\"componentType\":5126,\"count\":" + nv + ",\"type\":\"VEC3\",\"min\":[" + F(lo[0]) + "," + F(lo[1]) + "," + F(lo[2]) + "],\"max\":[" + F(hi[0]) + "," + F(hi[1]) + "," + F(hi[2]) + "]},"
            + "{\"bufferView\":1,\"componentType\":5126,\"count\":" + nv + ",\"type\":\"VEC3\"},"
            + "{\"bufferView\":2,\"componentType\":" + (u32 ? 5125 : 5123) + ",\"count\":" + idx.Length + ",\"type\":\"SCALAR\"}]}";

        var js = Encoding.UTF8.GetBytes(json);
        if (js.Length % 4 != 0) { var pad = 4 - js.Length % 4; var t = new byte[js.Length + pad]; js.CopyTo(t, 0); for (int i = js.Length; i < t.Length; i++) t[i] = 0x20; js = t; }

        using var fs = File.Create(path);
        using var w = new BinaryWriter(fs);
        w.Write(Encoding.ASCII.GetBytes("glTF"));
        w.Write(2u);
        w.Write((uint)(12 + 8 + js.Length + 8 + bin.Length));
        w.Write((uint)js.Length); w.Write(0x4E4F534Au); w.Write(js);
        w.Write((uint)bin.Length); w.Write(0x004E4942u); w.Write(bin);
    }

    public class Target
    {
        public string name { get; set; } = "";
        public string meshPath { get; set; } = "";
        public string kind { get; set; } = "SM";
        public string outDir { get; set; } = "meshes_c4";
    }

    public class Result
    {
        public string name { get; set; } = "";
        public string meshPath { get; set; } = "";
        public string kind { get; set; } = "";
        public string outDir { get; set; } = "";
        public string? vpath { get; set; }
        public bool ok { get; set; }
        public string? err { get; set; }
        public int verts { get; set; }
        public int tris { get; set; }
        public bool nanite { get; set; }
        public int lods { get; set; }
        public string? glb { get; set; }
        public float[]? bbox_cm { get; set; }
        public float[]? min_cm { get; set; }
        public float[]? max_cm { get; set; }
    }
}
