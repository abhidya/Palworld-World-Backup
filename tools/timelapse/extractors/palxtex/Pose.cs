using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse_Conversion.Animations;
using CUE4Parse_Conversion.Dto;
using CUE4Parse_Conversion.Writers.ActorX.Structs.Animations;

namespace Palxtex;

/// Bakes one frame of a real in-game AnimSequence into mesh vertex positions.
/// The renderer only loads flat single-node GLBs (no skins, one node), so we
/// skin CPU-side and write posed positions rather than emitting a rigged glTF.
/// Nothing here invents a pose: every rotation comes from the shipped sequence,
/// and bones the sequence does not touch keep the skeleton's own reference pose.
public static class Pose
{
    /// Evaluate every bone of the animation skeleton at one frame, in component
    /// space. Bones without keys fall back to the skeleton reference pose.
    /// Matrices rather than FTransform: this skeleton carries thousands of
    /// zero-quaternion padding entries that trip FTransform's normalize assert.
    static FMatrix[] Component(CAnimSequence seq, FReferenceSkeleton refSkel, float frame)
    {
        var n = refSkel.FinalRefBonePose.Length;
        var comp = new FMatrix[n];
        for (int b = 0; b < n; b++)
        {
            var rp = refSkel.FinalRefBonePose[b];
            var q = rp.Rotation; var p = rp.Translation; var s = rp.Scale3D;
            if (seq != null && b < seq.Tracks.Count && seq.Tracks[b].HasKeys())
                seq.Tracks[b].GetBoneTransform(frame, seq.NumFrames, ref q, ref p, ref s);
            var local = Local(q, p, s);
            var pi = refSkel.FinalRefBoneInfo[b].ParentIndex;
            // parents always precede children in a UE reference skeleton
            comp[b] = pi >= 0 && pi < b ? local * comp[pi] : local;
        }
        return comp;
    }

    /// Bone-local matrix, tolerant of the degenerate quaternions this skeleton
    /// stores for unused bone slots.
    static FMatrix Local(FQuat q, FVector p, FVector s)
    {
        var len = MathF.Sqrt(q.X * q.X + q.Y * q.Y + q.Z * q.Z + q.W * q.W);
        if (len < 1e-6f) q = FQuat.Identity; else { q.X /= len; q.Y /= len; q.Z /= len; q.W /= len; }
        if (s.X == 0 && s.Y == 0 && s.Z == 0) s = FVector.OneVector;
        return new FTransform(q, p, s).ToMatrixWithScale();
    }

    static FVector Origin(FMatrix m) => new(m.M30, m.M31, m.M32);


    static (UAnimSequence useq, CAnimSet set) Load(CUE4Parse.FileProvider.DefaultFileProvider provider, string animName)
    {
        var leaf = "/" + animName + ".uasset";
        var vpath = provider.Files.Keys.FirstOrDefault(k => k.EndsWith(leaf, StringComparison.OrdinalIgnoreCase))
                    ?? throw new FileNotFoundException("anim not found: " + animName);
        var useq = provider.LoadPackage(vpath).GetExports().OfType<UAnimSequence>().FirstOrDefault()
                   ?? throw new InvalidDataException("no UAnimSequence in " + vpath);
        return (useq, useq.ConvertAnims());
    }

    public static int Probe(string[] args)
    {
        var provider = P.Mount();
        Console.Error.WriteLine($"mounted files={provider.Files.Count}");
        var want = args.Length > 1 ? args[1] : "AS_Player_Female_Architecture2";
        var leaf = "/" + want + ".uasset";
        var vpath = provider.Files.Keys.FirstOrDefault(k => k.EndsWith(leaf, StringComparison.OrdinalIgnoreCase));
        if (vpath == null) { Console.WriteLine("NOT FOUND " + want); return 1; }
        Console.WriteLine("vpath=" + vpath);

        var seq = provider.LoadPackage(vpath).GetExports().OfType<UAnimSequence>().FirstOrDefault();
        if (seq == null) { Console.WriteLine("no UAnimSequence"); return 1; }
        Console.WriteLine($"name={seq.Name} numFrames={seq.NumFrames} len={seq.SequenceLength} rate={seq.RateScale}");
        Console.WriteLine($"additive={seq.AdditiveAnimType} refPoseType={seq.RefPoseType} refFrame={seq.RefFrameIndex}");
        Console.WriteLine($"compressed={seq.CompressedDataStructure?.GetType().Name ?? "<null>"}");
        Console.WriteLine($"rawData={(seq.RawAnimationData?.Length.ToString() ?? "<null>")}");
        Console.WriteLine($"skeleton={seq.Skeleton.ResolvedObject?.GetPathName()}");
        Console.WriteLine($"numTracks={seq.GetNumTracks()}");
        foreach (var nt in seq.Notifies ?? [])
            Console.WriteLine($"  notify {nt.NotifyName} link={nt.LinkValue}");
        try
        {
            var s = seq.ConvertAnims().Sequences[0];
            Console.WriteLine($"CONVERT OK tracks={s.Tracks.Count} frames={s.NumFrames} fps={s.FramesPerSecond} withKeys={s.Tracks.Count(t => t.HasKeys())}");
        }
        catch (Exception ex) { Console.WriteLine($"CONVERT FAIL {ex.GetType().Name}: {ex.Message}"); return 2; }
        return 0;
    }

    /// Per-frame readout of the bones that decide whether a pose reads as
    /// "building": how far the hands sit from the body centreline and how high
    /// they are. Used to choose ONE frame to bake.
    public static int Scan(string[] args)
    {
        var provider = P.Mount();
        var animName = args.Length > 1 ? args[1] : "AS_Player_Female_Architecture2";
        int step = args.Length > 2 ? int.Parse(args[2]) : 10;
        var (_, set) = Load(provider, animName);
        var seq = set.Sequences[0];
        var refSkel = set.Skeleton.ReferenceSkeleton;
        var names = refSkel.FinalRefBoneInfo.Select(b => b.Name.Text).ToArray();

        Console.WriteLine($"bones={names.Length} tracksWithKeys={seq.Tracks.Count(t => t.HasKeys())} frames={seq.NumFrames}");
        Console.WriteLine("animated: " + string.Join(" ",
            Enumerable.Range(0, Math.Min(names.Length, seq.Tracks.Count))
                      .Where(i => seq.Tracks[i].HasKeys()).Select(i => names[i])));

        int Idx(string n) => Array.FindIndex(names, x => string.Equals(x, n, StringComparison.OrdinalIgnoreCase));
        int lh = Idx("hand_l"), rh = Idx("hand_r");
        if (lh < 0 || rh < 0) { Console.WriteLine("hand bones not found; names[0..40]=" + string.Join(" ", names.Take(40))); return 1; }

        // reference-pose vs posed component positions for the bones that decide
        // overall stance, so a whole-body offset can't be mistaken for a crouch
        if (args.Length > 3 && args[3] == "--bones")
        {
            var probe = new[] { "root", "pelvis", "spine_03", "neck_01", "head", "thigh_l", "calf_l", "foot_l", "hand_l", "hand_r" };
            float bf = args.Length > 4 ? float.Parse(args[4]) : 31f;
            var refC = Component(null, refSkel, 0f);   // null seq -> pure reference pose
            var posC = Component(seq, refSkel, bf);
            Console.WriteLine($"bone         | refpose x,y,z              | frame {bf} x,y,z");
            foreach (var bn in probe)
            {
                int bi = Idx(bn); if (bi < 0) { Console.WriteLine($"{bn,-12} | MISSING"); continue; }
                var a = Origin(refC[bi]); var b = Origin(posC[bi]);
                Console.WriteLine($"{bn,-12} |{a.X,8:F1},{a.Y,8:F1},{a.Z,8:F1}    |{b.X,8:F1},{b.Y,8:F1},{b.Z,8:F1}");
            }
            return 0;
        }

        Console.WriteLine("frame |      handL x,y,z      |      handR x,y,z      | armSpanX | handZmax");
        for (float f = 0; f < seq.NumFrames; f += step)
        {
            var c = Component(seq, refSkel, f);
            var a = Origin(c[lh]); var b = Origin(c[rh]);
            Console.WriteLine($"{f,5} |{a.X,7:F1},{a.Y,7:F1},{a.Z,7:F1} |{b.X,7:F1},{b.Y,7:F1},{b.Z,7:F1} |{Math.Abs(a.X - b.X),9:F1} |{Math.Max(a.Z, b.Z),9:F1}");
        }
        return 0;
    }

    /// Stance comparison across several animations in ONE mount (the pak file
    /// lock is exclusive, so separate processes cannot run in parallel).
    public static int Stance(string[] args)
    {
        var provider = P.Mount();
        foreach (var animName in args.Skip(1))
        {
            Console.WriteLine($"=== {animName} ===");
            CAnimSet set;
            UAnimSequence useq;
            try { (useq, set) = Load(provider, animName); }
            catch (Exception ex) { Console.WriteLine("  " + ex.Message); continue; }
            var seq = set.Sequences[0];
            var refSkel = set.Skeleton.ReferenceSkeleton;
            var names = refSkel.FinalRefBoneInfo.Select(b => b.Name.Text).ToArray();
            int Idx(string n) => Array.FindIndex(names, x => string.Equals(x, n, StringComparison.OrdinalIgnoreCase));
            int pv = Idx("pelvis"), hd = Idx("head"), fl = Idx("foot_l"), hr = Idx("hand_r"), hl = Idx("hand_l");
            Console.WriteLine($"  codec={useq.CompressedDataStructure?.GetType().Name} frames={seq.NumFrames} withKeys={seq.Tracks.Count(t => t.HasKeys())}");
            var refC = Component(null, refSkel, 0f);
            Console.WriteLine($"  refpose  pelvisZ={Origin(refC[pv]).Z,6:F1} headZ={Origin(refC[hd]).Z,6:F1} footZ={Origin(refC[fl]).Z,6:F1} handR=({Origin(refC[hr]).X,6:F1},{Origin(refC[hr]).Y,6:F1},{Origin(refC[hr]).Z,6:F1})");
            for (float f = 0; f < seq.NumFrames; f += Math.Max(1, seq.NumFrames / 8))
            {
                var c = Component(seq, refSkel, f);
                var r = Origin(c[hr]); var l = Origin(c[hl]);
                Console.WriteLine($"  f{f,5}  pelvisZ={Origin(c[pv]).Z,6:F1} headZ={Origin(c[hd]).Z,6:F1} footZ={Origin(c[fl]).Z,6:F1} handR=({r.X,6:F1},{r.Y,6:F1},{r.Z,6:F1}) handL=({l.X,6:F1},{l.Y,6:F1},{l.Z,6:F1})");
            }
        }
        return 0;
    }

    /// List animation assets whose virtual path contains a substring.
    public static int AnimLs(string[] args)
    {
        var provider = P.Mount();
        foreach (var pat in args.Skip(1))
        {
            var hits = provider.Files.Keys
                .Where(k => k.Contains(pat, StringComparison.OrdinalIgnoreCase) && k.EndsWith(".uasset"))
                .OrderBy(k => k).ToList();
            Console.WriteLine($"=== {pat}: {hits.Count} ===");
            foreach (var h in hits.Take(40)) Console.WriteLine("  " + h);
        }
        return 0;
    }


    /// Component-space transform of every bone at ONE frame of a real in-game
    /// AnimSequence, written out so an ATTACHMENT can be placed on the posed
    /// character rather than on its bind pose.
    ///
    /// Why this exists. Baking the pose into vertex positions (Extract.cs's SK
    /// branch) fixes any mesh that is SKINNED to SK_PalHuman_Skeleton — armour,
    /// hair, heads, and the head equips that are authored in character space.
    /// It does nothing for a RIGID prop: SK_HeadEquip034 is ten centimetres
    /// across and lives in its own socket-local space, so it is positioned by a
    /// socket transform, not by skinning. eq_sockets.json / the `sockets` block
    /// in equipment_&lt;base&gt;.json give those transforms in the BIND pose, which
    /// would hang the helmet where the head is when the arms are straight out —
    /// i.e. in the wrong place on a posed avatar. Composing the socket's own
    /// relative transform onto the bone transform emitted here puts it where
    /// the head actually is at the baked frame.
    ///
    /// Nothing is invented: these are the shipped sequence's own bone rotations
    /// evaluated by the same Component() the vertex bake uses, so an attachment
    /// and the body it sits on are guaranteed to agree.
    public static int PoseBones(string[] args)
    {
        var provider = P.Mount();
        var animName = args.Length > 1 ? args[1] : "AS_Player_Female_Architecture2";
        float frame = args.Length > 2 ? float.Parse(args[2], System.Globalization.CultureInfo.InvariantCulture) : 0f;
        var outPath = args.Length > 3 ? args[3] : Path.Combine(P.Base, $"posebones_{animName}_{frame}.json");
        var (useq, set) = Load(provider, animName);
        var seq = set.Sequences[0];
        var refSkel = set.Skeleton.ReferenceSkeleton;
        var comp = Component(seq, refSkel, frame);
        var bones = new Dictionary<string, object>();
        for (int b = 0; b < refSkel.FinalRefBoneInfo.Length; b++)
        {
            var name = refSkel.FinalRefBoneInfo[b].Name.Text;
            if (bones.ContainsKey(name)) continue;   // padding slots reuse names
            var m = comp[b];
            var o = m.GetOrigin();
            var q = m.ToQuat();
            bones[name] = new Dictionary<string, object>
            {
                ["t"] = new[] { o.X, o.Y, o.Z },
                ["q"] = new[] { q.X, q.Y, q.Z, q.W },
            };
        }
        var doc = new Dictionary<string, object>
        {
            ["anim"] = useq.GetPathName(),
            ["animName"] = animName,
            ["frame"] = frame,
            ["numFrames"] = seq.NumFrames,
            ["bones"] = bones,
        };
        File.WriteAllText(outPath, System.Text.Json.JsonSerializer.Serialize(doc,
            new System.Text.Json.JsonSerializerOptions { WriteIndented = false }));
        Console.WriteLine($"{animName} frame {frame}/{seq.NumFrames}: {bones.Count} bones -> {outPath}");
        return 0;
    }

    // ---- baker: one animation, one frame, reused across every mesh ----
    public sealed class Baker
    {
        public readonly string AnimPath;
        public readonly string AnimName;
        public readonly float Frame;
        public readonly int NumFrames;
        readonly FMatrix[] _animComponent;
        readonly Dictionary<string, int> _animByName;

        public Baker(CUE4Parse.FileProvider.DefaultFileProvider provider, string animName, float frame)
        {
            AnimName = animName;
            var (useq, set) = Load(provider, animName);
            AnimPath = useq.GetPathName();
            var seq = set.Sequences[0];
            NumFrames = seq.NumFrames;
            Frame = frame;

            var refSkel = set.Skeleton.ReferenceSkeleton;
            _animComponent = Component(seq, refSkel, frame);
            _animByName = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < refSkel.FinalRefBoneInfo.Length; i++)
                _animByName.TryAdd(refSkel.FinalRefBoneInfo[i].Name.Text, i);
        }

        /// Skinning matrices for one mesh's own bone list: inverse(bind) * animPose,
        /// in UE's row-vector convention. Null for bones the animation skeleton
        /// does not have — those vertices keep their bind position.
        public FMatrix?[] SkinMatrices(MeshBoneDto[] meshBones, out int matched)
        {
            var bind = new FMatrix[meshBones.Length];
            for (int b = 0; b < meshBones.Length; b++)
            {
                var t = meshBones[b].Transform;
                var local = Local(t.Rotation, t.Translation, t.Scale3D);
                var pi = meshBones[b].ParentIndex;
                bind[b] = pi >= 0 && pi < b ? local * bind[pi] : local;
            }

            matched = 0;
            var outM = new FMatrix?[meshBones.Length];
            for (int b = 0; b < meshBones.Length; b++)
            {
                if (!_animByName.TryGetValue(meshBones[b].Name, out var ai)) continue;
                matched++;
                outM[b] = bind[b].Inverse() * _animComponent[ai];
            }
            return outM;
        }
    }

    public static FVector XformPos(FMatrix m, FVector v) => new(
        v.X * m.M00 + v.Y * m.M10 + v.Z * m.M20 + m.M30,
        v.X * m.M01 + v.Y * m.M11 + v.Z * m.M21 + m.M31,
        v.X * m.M02 + v.Y * m.M12 + v.Z * m.M22 + m.M32);

    public static FVector XformDir(FMatrix m, FVector v) => new(
        v.X * m.M00 + v.Y * m.M10 + v.Z * m.M20,
        v.X * m.M01 + v.Y * m.M11 + v.Z * m.M21,
        v.X * m.M02 + v.Y * m.M12 + v.Z * m.M22);
}
