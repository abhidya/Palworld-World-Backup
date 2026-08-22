using System.Text.Json;
using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;

namespace PalSpawn;

public static class Program
{
    static readonly string Base = Environment.GetEnvironmentVariable("PALX_BASE") ?? throw new InvalidOperationException("PALX_BASE is not set - point it at your timelapse work directory");

    static AbstractFileProvider MakeProvider(string src)
    {
        DefaultFileProvider p;
        if (src == "pak")
            p = new DefaultFileProvider(
                new DirectoryInfo("/Users/mannybhidya/PalworldServer/palworld/Pal/Content/Paks"),
                SearchOption.AllDirectories,
                new VersionContainer(EGame.GAME_UE5_1), StringComparer.OrdinalIgnoreCase);
        else
            p = new DefaultFileProvider(new DirectoryInfo(Path.Combine(Base, src)),
                SearchOption.AllDirectories,
                new VersionContainer(EGame.GAME_UE5_1), StringComparer.OrdinalIgnoreCase);
        p.Initialize();
        if (src == "pak") { var n = p.Mount(); p.PostMount(); Console.Error.WriteLine($"mounted vfs: {n}"); }
        p.MappingsContainer = new FileUsmapTypeMappingsProvider(Path.Combine(Base, "Mappings.usmap"));
        return p;
    }

    public static int Main(string[] args)
    {
        var src = args[0];      // "pak" or a scratchpad-relative loose dir
        var mode = args[1];
        var pats = args.Skip(2).ToArray();
        var prov = MakeProvider(src);
        Console.Error.WriteLine($"provider files: {prov.Files.Count}");

        IEnumerable<string> Match(string ext) => prov.Files.Keys
            .Where(k => k.EndsWith(ext, StringComparison.OrdinalIgnoreCase)
                && (pats.Length == 0 || pats.Any(w => k.Contains(w, StringComparison.OrdinalIgnoreCase))))
            .OrderBy(k => k);

        if (mode == "list")
        {
            var keys = prov.Files.Keys
                .Where(k => pats.Length == 0 || pats.Any(w => k.Contains(w, StringComparison.OrdinalIgnoreCase)))
                .OrderBy(k => k).ToList();
            foreach (var k in keys) Console.WriteLine(k);
            Console.Error.WriteLine($"matched {keys.Count}");
            return 0;
        }

        if (mode == "survey")
        {
            foreach (var vp in Match(".umap"))
            {
                var pkg = prov.LoadPackage(vp);
                var hist = new SortedDictionary<string, int>();
                foreach (var e in pkg.GetExports())
                {
                    var c = e.ExportType ?? e.GetType().Name;
                    hist[c] = hist.GetValueOrDefault(c) + 1;
                }
                Console.WriteLine($"\n### {vp[(vp.LastIndexOf('/') + 1)..]}");
                foreach (var kv in hist.OrderByDescending(k => k.Value))
                    Console.WriteLine($"   {kv.Value,6}  {kv.Key}");
            }
            return 0;
        }

        if (mode == "props")
        {
            // dump every export whose class name contains any of the extra filters
            var cls = Environment.GetEnvironmentVariable("CLSFILTER") ?? "Spawn";
            var clsPats = cls.Split(',');
            foreach (var vp in Match(".umap").Concat(Match(".uasset")))
            {
                UObject[] exps;
                try { exps = prov.LoadPackage(vp).GetExports().ToArray(); }
                catch (Exception ex) { Console.WriteLine($"FAIL {vp}: {ex.Message}"); continue; }
                foreach (var e in exps)
                {
                    var c = e.ExportType ?? "";
                    if (!clsPats.Any(w => c.Contains(w, StringComparison.OrdinalIgnoreCase))) continue;
                    Console.WriteLine($"\n### {vp}  [{c}] {e.Name}");
                    foreach (var pr in e.Properties)
                        Console.WriteLine($"    {pr.Name.Text} ({pr.Tag?.GetType().Name}) = {Trunc(pr.Tag?.GenericValue?.ToString())}");
                }
            }
            return 0;
        }

        if (mode == "bp")
        {
            // Every BP_PalSpawner_Sheets_* blueprint bakes its own spawn table into
            // the class default object as SpawnGroupList. Dump it verbatim.
            var dump = new Dictionary<string, object>();
            int n = 0;
            foreach (var vp in Match(".uasset"))
            {
                try
                {
                    var pkg = prov.LoadPackage(vp);
                    foreach (var ex in pkg.GetExports())
                    {
                        if (!ex.Name.StartsWith("Default__", StringComparison.Ordinal)) continue;
                        if (!ex.Properties.Any(pr => pr.Name.Text == "SpawnGroupList")) continue;
                        dump[ex.Name] = Flat(ex.Properties);
                        n++;
                    }
                }
                catch (Exception ex) { Console.Error.WriteLine($"FAIL {vp}: {ex.Message}"); }
            }
            var of2 = Environment.GetEnvironmentVariable("OUTFILE") ?? "spawner_bp.json";
            File.WriteAllText(Path.Combine(Base, of2), JsonSerializer.Serialize(dump));
            Console.Error.WriteLine($"CDOs with SpawnGroupList: {n} -> {of2}");
            return 0;
        }

        if (mode == "dt")
        {
            var dump = new Dictionary<string, object>();
            foreach (var vp in Match(".uasset"))
            {
                try
                {
                    var pkg = prov.LoadPackage(vp);
                    foreach (var ex in pkg.GetExports())
                    {
                        if (ex is not CUE4Parse.UE4.Assets.Exports.Engine.UDataTable dt) continue;
                        var rows = new Dictionary<string, object>();
                        foreach (var kv in dt.RowMap)
                            rows[kv.Key.Text] = Flat(kv.Value.Properties);
                        dump[vp] = rows;
                        Console.Error.WriteLine($"{vp}: {rows.Count} rows");
                    }
                }
                catch (Exception ex) { Console.Error.WriteLine($"FAIL {vp}: {ex.Message}"); }
            }
            var o = Environment.GetEnvironmentVariable("OUTFILE") ?? "spawn_datatables.json";
            File.WriteAllText(Path.Combine(Base, o), JsonSerializer.Serialize(dump));
            Console.Error.WriteLine("-> " + o);
            return 0;
        }

        if (mode == "spawners")
        {
            var clsFilter = (Environment.GetEnvironmentVariable("CLSFILTER") ?? "Spawn").Split(',');
            var recs = new List<object>();
            int cells = 0;
            foreach (var vp in Match(".umap"))
            {
                cells++;
                CUE4Parse.UE4.Assets.IPackage pkg;
                try { pkg = prov.LoadPackage(vp); }
                catch (Exception ex) { Console.Error.WriteLine($"FAILPKG {vp}: {ex.Message}"); continue; }
                var cell = vp[(vp.LastIndexOf('/') + 1)..];
                var exps = pkg.GetExports().ToArray();
                // index exports by name so RootComponent references resolve locally
                foreach (var e in exps)
                {
                    var c = e.ExportType ?? "";
                    if (!clsFilter.Any(w => c.Contains(w, StringComparison.OrdinalIgnoreCase))) continue;
                    var loc = e.GetOrDefault("RelativeLocation", FVector.ZeroVector);
                    var rot = e.GetOrDefault("RelativeRotation", FRotator.ZeroRotator);
                    // actor: transform lives on its RootComponent
                    var rootIdx = e.GetOrDefault<FPackageIndex>("RootComponent", null);
                    string rootName = rootIdx?.Name;
                    if (loc.IsZero() && rootIdx != null)
                    {
                        try
                        {
                            var root = rootIdx.ResolvedObject?.Load();
                            if (root != null)
                            {
                                loc = root.GetOrDefault("RelativeLocation", FVector.ZeroVector);
                                rot = root.GetOrDefault("RelativeRotation", FRotator.ZeroRotator);
                            }
                        }
                        catch { }
                    }
                    recs.Add(new
                    {
                        cell,
                        cls = c,
                        name = e.Name,
                        root = rootName,
                        loc = new[] { loc.X, loc.Y, loc.Z },
                        rot = new[] { rot.Pitch, rot.Yaw, rot.Roll },
                        props = Flat(e.Properties),
                    });
                }
            }
            var of = Environment.GetEnvironmentVariable("OUTFILE") ?? "spawners_raw.json";
            File.WriteAllText(Path.Combine(Base, of), JsonSerializer.Serialize(recs));
            Console.Error.WriteLine($"cells={cells} records={recs.Count} -> {of}");
            return 0;
        }

        Console.Error.WriteLine("modes: list survey props dt spawners");
        return 1;
    }

    static string Trunc(string s) => s == null ? "" : (s.Length > 400 ? s[..400] + "..." : s);

    static Dictionary<string, object> Flat(IEnumerable<CUE4Parse.UE4.Assets.Objects.FPropertyTag> props)
    {
        var d = new Dictionary<string, object>();
        foreach (var p in props)
        {
            var k = p.Name.Text;
            if (d.ContainsKey(k)) k = k + "#" + d.Count;
            d[k] = Val(p.Tag?.GenericValue);
        }
        return d;
    }

    static object Val(object g)
    {
        switch (g)
        {
            case null: return null;
            case string s: return s;
            case bool b: return b;
            case int i: return i;
            case float f: return f;
            case double dd: return dd;
            case byte by: return (int)by;
            case FName n: return n.Text;
            case FPackageIndex pi: return pi.Name;
            case FVector v: return new[] { v.X, v.Y, v.Z };
            case FRotator r: return new[] { r.Pitch, r.Yaw, r.Roll };
            case CUE4Parse.UE4.Objects.UObject.FSoftObjectPath sp: return sp.AssetPathName.Text;
            case CUE4Parse.UE4.Assets.Objects.FScriptStruct fss: return Val(fss.StructType);
            case CUE4Parse.UE4.Assets.Objects.FStructFallback sf: return Flat(sf.Properties);
            case CUE4Parse.UE4.Assets.Objects.UScriptArray arr:
                return arr.Properties.Select(x => Val(x?.GenericValue)).ToList();
            case CUE4Parse.UE4.Assets.Objects.UScriptMap map:
                {
                    var o = new List<object>();
                    foreach (var kv in map.Properties)
                        o.Add(new { k = Val(kv.Key?.GenericValue), v = Val(kv.Value?.GenericValue) });
                    return o;
                }
            default: return g.ToString();
        }
    }
}
