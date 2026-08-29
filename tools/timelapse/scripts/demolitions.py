"""Pair removed structures with the structures that replaced them, and attribute
the demolition to the replacement's recorded builder.

DATA ONLY. Emits mappal/public/union/demolitions_<base>.json. Touches nothing else.

The save records that a piece EXISTED in one snapshot and DID NOT in a later one.
It never records a destruction event, an actor, or a time. Everything below is an
inference from presence/absence plus geometry.
"""
import json, os, sys, re, collections

SP = os.environ.get("PALTL_WORK") or os.path.dirname(os.path.abspath(__file__))
UNION = os.path.join(SP, "mappal", "public", "union")
LOOT = "CommonDropItem3D"          # ground loot, ~1h despawn timer, not construction
# Things that come and go on their own and were never *built*, so their
# disappearance is not a demolition and their arrival is not a replacement:
#   CommonDropItem3D  - ground loot bags, ~1h despawn timer
#   Palegg / PalEgg_* - breeding-pen eggs, laid and collected/hatched
#                       (HatchingPalEgg is the incubator MACHINE and is kept)
#   DamagableRock<nn>  - mineable ore/rock nodes, mined out and respawning;
#                       buildorder.js already classes these "natural: terrain,
#                       not built"
NONBUILT = re.compile(r"^(CommonDropItem3D|Palegg|PalEgg_.+|DamagableRock\d+)$")
SENTINEL = "00000000"              # save's "no recorded builder" marker, not a person
BUILT_MARGIN = 300                 # update_timelapse.py: first > t0 + 300  -> "built"
REMOVED_MARGIN = 600               # update_timelapse.py: last  < t1 - 600  -> "removed"
BASES = ["07f13218", "16fca097", "de44d9f4", "5fed0024"]
NAMES = {"07f13218": "Glass Tower", "16fca097": "Wooden Camp",
         "de44d9f4": "Stone Works", "5fed0024": "Lost Camp"}


def load():
    bi = json.load(open(os.path.join(SP, "build_index.json")))
    mr = json.load(open(os.path.join(SP, "mappal/src/data/meshRegistry.json")))
    c4 = {r["name"]: r for r in json.load(open(os.path.join(SP, "c4all_report.json")))}
    obj = json.load(open(os.path.join(SP, "mappal/src/data/objects.json")))["types"]
    return bi, mr, c4, obj


def box_table(types, mr, c4, obj):
    """type -> (min[3], max[3]) local-space AABB in cm, origin at the piece's position."""
    out, src = {}, collections.Counter()
    for t in types:
        m = mr.get(t, {}).get("mesh")
        r = c4.get(m) if m else None
        if r and r.get("min_cm") and r.get("max_cm"):
            out[t] = (list(r["min_cm"]), list(r["max_cm"]))
            src["mesh"] += 1
            continue
        s = (obj.get(t) or {}).get("size")
        if s and all(v for v in s):
            hx, hy, hz = s[0] / 2.0, s[1] / 2.0, s[2]
            if (obj.get(t) or {}).get("originAtTop"):
                out[t] = ([-hx, -hy, -hz], [hx, hy, 0.0])
            else:
                out[t] = ([-hx, -hy, 0.0], [hx, hy, hz])
            src["graybox"] += 1
            continue
        out[t] = ([-100.0, -100.0, -100.0], [100.0, 100.0, 100.0])   # last resort
        src["nominal"] += 1
    return out, src


def world_box(r, boxes):
    lo, hi = boxes[r["type"]]
    return ((r["x"] + lo[0], r["y"] + lo[1], r["z"] + lo[2]),
            (r["x"] + hi[0], r["y"] + hi[1], r["z"] + hi[2]))


def iou(a, b):
    inter = 1.0
    for k in range(3):
        d = min(a[1][k], b[1][k]) - max(a[0][k], b[0][k])
        if d <= 0:
            return 0.0
        inter *= d
    va = 1.0; vb = 1.0
    for k in range(3):
        va *= max(1e-6, a[1][k] - a[0][k])
        vb *= max(1e-6, b[1][k] - b[0][k])
    return inter / (va + vb - inter)


def analyse(base, rows, builders, boxes, snaps, thr, kwin):
    """rows: this base's non-loot pieces. snaps: global snapshot timeline (sorted).
    Returns (pairs, removals, unpaired)."""
    t0 = min(r["first"] for r in rows)
    t1 = max(r["last"] for r in rows)
    removals = [r for r in rows if r["last"] < t1 - REMOVED_MARGIN]
    # candidate replacements: anything that appeared after the base's own start
    news = [r for r in rows if r["first"] > t0 + BUILT_MARGIN]

    idx = {t: i for i, t in enumerate(snaps)}
    by_first = collections.defaultdict(list)
    for r in news:
        by_first[r["first"]].append(r)

    cands = []
    for rem in removals:
        i = idx[rem["last"]]
        for j in range(i + 1, min(i + 1 + kwin, len(snaps))):
            for new in by_first[snaps[j]]:
                if new["id"] == rem["id"]:
                    continue
                o = iou(world_box(rem, boxes), world_box(new, boxes))
                if o >= thr:
                    cands.append((o, j - i, rem, new))
    # Greedy, strongest overlap first. Each REMOVED piece is claimed by at most one
    # replacement (nobody gets demolished twice), but one replacement MAY absorb
    # several removals: a single blast furnace really can be built over the crusher
    # and the old furnace that were both standing there.
    cands.sort(key=lambda c: (-c[0], c[1], c[2]["id"], c[3]["id"]))
    usedR, pairs = set(), []
    for o, dk, rem, new in cands:
        if rem["id"] in usedR:
            continue
        usedR.add(rem["id"])
        ruid = builders.get(rem["id"])
        nuid = builders.get(new["id"])
        if not nuid or nuid == SENTINEL:
            attr, auid = "unattributed", None
        elif ruid and ruid != SENTINEL and ruid == nuid:
            attr, auid = "same-builder", nuid
        else:
            attr, auid = "replacement-builder", nuid
        pairs.append({
            "removedId": rem["id"], "removedType": rem["type"], "removedAt": rem["last"],
            "replacementId": new["id"], "replacementType": new["type"],
            "replacedAt": new["first"],
            "attributedUid": auid, "attribution": attr,
            "overlap": round(o, 4), "gapSeconds": int(new["first"] - rem["last"]),
            "snapshotSteps": dk, "removedBuilderUid": ruid or None,
        })
    pairs.sort(key=lambda p: (p["replacedAt"], p["removedId"]))
    return pairs, removals, [r for r in removals if r["id"] not in usedR], t0, t1


def main():
    bi, mr, c4, obj = load()
    rows_all = [r for r in bi if not NONBUILT.match(r["type"])]
    boxes, src = box_table({r["type"] for r in rows_all}, mr, c4, obj)
    sys.stderr.write(f"[box] sources: {dict(src)}\n")
    # A snapshot captures the whole world at once, so the timeline is global AND
    # built from EVERY object including the excluded churn: a loot bag's first/last
    # are real snapshot times and they are what make the grid dense enough for
    # "the next snapshot or two" to mean a short interval rather than days.
    snaps = sorted({r["first"] for r in bi} | {r["last"] for r in bi})
    sys.stderr.write(f"[snap] {len(snaps)} observable snapshots\n")

    THR, KWIN = 0.30, 2
    report = {}
    for b in BASES:
        rows = [r for r in rows_all if r["base"] == b]
        builders = json.load(open(os.path.join(UNION, f"builders_{b}.json")))["builders"]
        sens = {}
        for thr in (0.15, 0.20, 0.30, 0.50, 0.70):
            for kw in (1, 2, 3):
                p, rem, un, _, _ = analyse(b, rows, builders, boxes, snaps, thr, kw)
                sens[f"iou{thr}_k{kw}"] = len(p)
        pairs, removals, unpaired, t0, t1 = analyse(b, rows, builders, boxes, snaps, THR, KWIN)
        att = sum(1 for p in pairs if p["attribution"] != "unattributed")
        doc = {
            "base": b,
            "note": (
                "INFERRED, NOT RECORDED. The save history records only that a piece was "
                "present in one snapshot and absent in a later one, and it records a "
                "build_player_uid for each piece that still exists. It does NOT record any "
                "destruction: not that a piece was demolished rather than despawned, not who "
                "demolished it, and not when. This file pairs a vanished piece with a piece "
                "that appeared in substantially the same volume within the next 2 snapshots, "
                "and names the REPLACEMENT's recorded builder as the person to animate "
                "demolishing the old piece. That naming is a rendering choice — a plausible "
                "reading of 'someone tore this down to put that there' — not a fact read out "
                "of the save. "
                "There are also no placement timestamps anywhere in this save (established "
                "and verified earlier); every time here comes from snapshot boundaries, so "
                "removedAt is 'the last snapshot the piece was seen in' and replacedAt is "
                "'the first snapshot the new piece was seen in'. The actual removal happened "
                "somewhere in the gap between them, at an unknown moment. gapSeconds is the "
                "width of that unknown window, not a duration. "
                "Method: removals use the same staleness convention as "
                "scripts/update_timelapse.py — a piece counts as removed when last < t1-600s "
                "and as built when first > t0+300s, with t0/t1 the base's own first/last "
                "piece timestamps. CommonDropItem3D is excluded throughout: it is ground loot "
                "on a ~1h despawn timer, not construction. Same place = axis-aligned "
                "bounding-box IoU >= 0.30 using real per-type mesh bounds "
                "(meshRegistry.json -> c4all_report.json min_cm/max_cm), falling back to "
                "objects.json gray-box sizes for the few types with no extracted mesh. "
                "build_index.json carries position only, no rotation, so boxes are placed "
                "unrotated; two pieces filling the same build slot share the same yaw, so the "
                "approximation is identically wrong for both and their IoU survives it. "
                "Matching is greedy, strongest overlap first: no removed piece is claimed "
                "twice, but one replacement may absorb several removals (one blast furnace "
                "built over both the crusher and the old furnace that stood there), so "
                "replacementId is not unique in this list. "
                "Attribution: attributedUid is always the REPLACEMENT piece's build_player_uid. "
                "'same-builder' means the removed piece's recorded builder was the same person; "
                "'replacement-builder' means it was someone else or was not recorded. If the "
                "replacement's builder is the '00000000' sentinel or absent, attribution is "
                "'unattributed' and attributedUid is null — no fallback guess is made. "
                "Removals with no replacement are legitimate (a piece deleted and never "
                "rebuilt) and are counted in counts.unpaired_removals but deliberately kept "
                "out of demolitions, because there is no replacement builder to attribute to. "
                "Two limits worth knowing before rendering this. (1) t1 is per base, so a "
                "base that disappeared wholesale registers as ZERO removals: Lost Camp "
                "(5fed0024) stops at its own t1 and its final contents are 'still standing' "
                "by this convention even though the camp is gone from the world's last "
                "snapshot. (2) The snapshot cadence is wildly irregular - minutes apart "
                "during a play session, days apart when nobody is on - so 'the next snapshot' "
                "is a short beat at Glass Tower and up to 4.5 days at the quieter bases. Read "
                "gapSeconds before deciding how confidently to stage a demolition."
            ),
            "params": {"iouThreshold": THR, "snapshotWindow": KWIN,
                       "builtMargin": BUILT_MARGIN, "removedMargin": REMOVED_MARGIN,
                       "excludedTypes": NONBUILT.pattern, "t0": t0, "t1": t1,
                       "sensitivity_pairs": sens},
            "demolitions": pairs,
            "counts": {"pairs": len(pairs), "unpaired_removals": len(unpaired),
                       "attributed": att, "unattributed": len(pairs) - att,
                       "total_pieces": len(rows), "removals": len(removals)},
        }
        with open(os.path.join(UNION, f"demolitions_{b}.json"), "w") as f:
            json.dump(doc, f, indent=1)
        pat = collections.Counter(f'{p["removedType"]} -> {p["replacementType"]}' for p in pairs)
        report[b] = (doc["counts"], sens, pat.most_common(8),
                     collections.Counter(p["attribution"] for p in pairs),
                     collections.Counter(p["attributedUid"] for p in pairs))

    print(f'{"base":10s} {"name":12s} {"pieces":>7s} {"removals":>9s} {"pairs":>6s} {"attr":>5s} {"unattr":>7s} {"unpaired":>9s}')
    for b in BASES:
        c = report[b][0]
        print(f'{b:10s} {NAMES[b]:12s} {c["total_pieces"]:7d} {c["removals"]:9d} '
              f'{c["pairs"]:6d} {c["attributed"]:5d} {c["unattributed"]:7d} {c["unpaired_removals"]:9d}')
    for b in BASES:
        c, sens, pat, attr, uids = report[b]
        print(f"\n== {NAMES[b]} ({b})")
        print("  sensitivity (pairs):", sens)
        print("  attribution:", dict(attr), "| uids:", dict(uids))
        for k, v in pat:
            print(f"    {v:4d}  {k}")


if __name__ == "__main__":
    main()
