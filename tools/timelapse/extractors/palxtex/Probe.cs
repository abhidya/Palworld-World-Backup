using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Versions;

namespace Palxtex;

public static class P
{
    public static readonly string Base = Environment.GetEnvironmentVariable("PALX_BASE") ?? throw new InvalidOperationException("PALX_BASE is not set - point it at your timelapse work directory");

    public static DefaultFileProvider Mount()
    {
        var pakDir = Path.Combine(Base, "mnt_d/SteamLibrary/steamapps/common/Palworld/Pal/Content/Paks");
        var p = new DefaultFileProvider(new DirectoryInfo(pakDir), SearchOption.TopDirectoryOnly,
            new VersionContainer(EGame.GAME_UE5_1), StringComparer.OrdinalIgnoreCase);
        p.Initialize();
        p.Mount();
        p.MappingsContainer = new FileUsmapTypeMappingsProvider(Path.Combine(Base, "Mappings.usmap"));
        p.ReadNaniteData = true;
        return p;
    }

    public static int Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "--bpxform") return Bp.Run(args);
        if (args.Length > 0 && args[0] == "--extract") return Extract.Run(args);
        if (args.Length > 0 && args[0] == "--dt") return Resolve.RunDt(args);
        if (args.Length > 0 && args[0] == "--bpmesh") return Resolve.RunBpMesh(args);
        if (args.Length > 0 && args[0] == "--find") return Resolve.RunFind(args);
        if (args.Length > 0 && args[0] == "--animprobe") return Pose.Probe(args);
        if (args.Length > 0 && args[0] == "--posescan") return Pose.Scan(args);
        if (args.Length > 0 && args[0] == "--stance") return Pose.Stance(args);
        if (args.Length > 0 && args[0] == "--animls") return Pose.AnimLs(args);
        if (args.Length > 0 && args[0] == "--posebones") return Pose.PoseBones(args);

        var sw = System.Diagnostics.Stopwatch.StartNew();
        var provider = Mount();
        Console.WriteLine($"mounted in {sw.ElapsedMilliseconds}ms files={provider.Files.Count}");
        sw.Restart();
        var key = provider.Files.Keys.FirstOrDefault(k => k.EndsWith("/SM_Wall_Wood.uasset", StringComparison.OrdinalIgnoreCase));
        Console.WriteLine($"lookup {sw.ElapsedMilliseconds}ms key={key}");
        if (key == null) return 1;
        sw.Restart();
        var pkg = provider.LoadPackage(key);
        var mesh = pkg.GetExports().OfType<UStaticMesh>().First();
        Console.WriteLine($"loaded mesh in {sw.ElapsedMilliseconds}ms mats={mesh.StaticMaterials.Length}");
        foreach (var m in mesh.StaticMaterials)
        {
            var mi = m.MaterialInterface;
            Console.WriteLine($"  slot={m.MaterialSlotName} name={mi?.Name} path={mi?.ResolvedObject?.GetPathName()}");
            if (mi != null && mi.TryLoad(out var obj) && obj is UMaterialInterface umat)
            {
                var pr = new CMaterialParams2();
                umat.GetParams(pr, EMaterialDepth.AllLayersNoRef);
                Console.WriteLine($"    class={umat.ExportType} textures={pr.Textures.Count} blend={pr.BlendMode}");
                foreach (var kv in pr.Textures) Console.WriteLine($"      {kv.Key} -> {kv.Value?.GetPathName()}");
            }
        }
        return 0;
    }
}
