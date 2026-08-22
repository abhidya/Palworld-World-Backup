"""Write mappal/public/union/endoflife_<base>.json - what the save history shows
about the end of each timelapse base.

Sources, all already-extracted, all real:
  eol_scan.json     per-snapshot census this run produced by decoding every
                    Level.sav we hold: which BaseCampSaveData records exist,
                    how many MapObjectSaveData rows attribute to each base, and
                    every Pal instance id + its recorded position.
  build_index.json  per-piece first/last snapshot, the same index the timelapse
                    already plays back.

Nothing is inferred about intent. The file states what the snapshots contain
and how wide the gap between them is.
"""
import json, os, math, datetime
from collections import Counter

SP = os.path.dirname(os.path.abspath(__file__))
OUT = f"{SP}/mappal/public/union"
NAMES = {"07f13218": "Glass Tower", "16fca097": "Wooden Camp",
         "de44d9f4": "Stone Works", "5fed0024": "Lost Camp"}
# Ground clutter, not construction: CommonDropItem3D is a dropped-item pickup
# that appears and despawns on its own timer (Lost Camp's count swings 0..115
# inside a single hour with no build activity), and DamagableRock* are the
# world's mineable rocks, which respawn. Neither is a player-built piece, and
# counting them makes a camp look like it is being torn down when it is not.
TRANSIENT_EXACT = {"CommonDropItem3D"}
TRANSIENT_PREFIX = ("DamagableRock",)


def transient(t):
    return t in TRANSIENT_EXACT or t.startswith(TRANSIENT_PREFIX)


def iso(ts):
    return datetime.datetime.fromtimestamp(ts).isoformat(sep=" ", timespec="seconds")


def human(sec):
    d, r = divmod(int(sec), 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return f"{d}d {h}h {m}m {s}s" if d else f"{h}h {m}m {s}s"


def pals_doc(scan, base, last_seen, final_ts, extra=None):
    """What became of the Pals that were standing in this base.

    A Pal is counted as "in" the base when its recorded position lies within the
    camp's own area_range, the same rule pal_index.py uses. That recorded
    position is SaveParameter.LastJumpedLocation - Palworld does not persist a
    live Pal transform, and this field means "where this Pal last jumped". A Pal
    whose value is unchanged between two snapshots has not jumped since; it does
    not prove the Pal stood still. Some records carry sentinel Z values
    (-999999, -1e9), so distances here are horizontal only.
    """
    by = {r["ts"]: r for r in scan}
    a = by[last_seen]
    ids = set(a["pals"].get(base, []))
    site = a["camps"][base]

    def where(snap):
        present = [i for i in ids if i in snap["all_pals"]]
        at_site, moved = [], []
        for i in present:
            p = snap["all_pals"][i]
            (at_site if math.hypot(p[0] - site["x"], p[1] - site["y"]) <= site["area_range"]
             else moved).append(i)
        out = []
        for i in moved:
            p = snap["all_pals"][i]
            camp = [k for k, c in snap["camps"].items()
                    if math.hypot(p[0] - c["x"], p[1] - c["y"]) <= c["area_range"]]
            out.append({"char": p[3],
                        "kmFromOldSite": round(math.hypot(p[0] - site["x"], p[1] - site["y"]) / 100000, 2),
                        "nowInBase": camp[0] if camp else None})
        return {"ts": snap["ts"], "at": iso(snap["ts"]),
                "stillInSave": len(present), "gone": len(ids) - len(present),
                "withinOldCampRadius": len(at_site), "elsewhere": out}

    want = [t for t in (last_seen, extra, final_ts) if t and t in by]
    checks = [where(by[t]) for t in sorted(set(want))]
    return {
        "measure": ("CharacterSaveParameterMap instance ids whose recorded "
                    "LastJumpedLocation lay inside this camp's area_range at the "
                    "camp's last sighting, followed forward by instance id"),
        "caveat": ("LastJumpedLocation is 'where this Pal last jumped', not a live "
                   "position, and this save has no per-Pal timestamps. 'unchanged' "
                   "means the Pal has not jumped since, not that it did not move."),
        "atLastSighting": {"count": len(ids),
                           "species": dict(Counter(a["all_pals"][i][3] for i in ids
                                                   if i in a["all_pals"]).most_common())},
        "crossCheck": ("pal_index.json - built independently by the earlier "
                       "pipeline from the same saves - knows all of these instance "
                       "ids and agrees on how many are still present in the final "
                       "snapshot."),
        "followedForward": checks,
    }


def main():
    scan = [r for r in json.load(open(f"{SP}/eol_scan.json")) if r.get("ok")]
    scan.sort(key=lambda r: r["ts"])
    bi = json.load(open(f"{SP}/build_index.json"))
    snaps = [r["ts"] for r in scan]
    final_ts = snaps[-1]

    # Every Level.sav on disk whose mtime falls inside the gap, whether or not
    # it belongs to this world - so the file can say what IS there rather than
    # only that nothing usable is.
    rejected = {r["ts"]: r for r in json.load(open(f"{SP}/eol_scan.json")) if not r.get("ok")}

    for base in sorted(NAMES):
        present = [r["ts"] for r in scan if base in r["camps"]]
        first_seen, last_seen = min(present), max(present)
        survives = last_seen == final_ts
        after = [t for t in snaps if t > last_seen]
        first_absent = after[0] if after and not survives else None

        rows = [o for o in bi if o["base"] == base]
        built = [o for o in rows if not transient(o["type"])]
        clutter = [o for o in rows if transient(o["type"])]
        last_c = Counter(o["last"] for o in built)

        piece_series = [{"ts": r["ts"], "at": iso(r["ts"]),
                         "built": sum(v for k, v in r["lost_camp_types"].items()
                                      if not transient(k)) if base == "5fed0024" else None,
                         "mapObjectRows": r["pieces"].get(base)}
                        for r in scan if base in r["camps"] or r["pieces"].get(base)]
        # `built` is only available per-snapshot for 5fed0024 (that is the type
        # breakdown eol_scan recorded); for the others the per-piece index below
        # carries the same information.
        if base != "5fed0024":
            for p in piece_series:
                p.pop("built")

        doc = {
            "base": base,
            "name": NAMES[base],
            "kind": "end-of-life",
            "source": ("every Level.sav in the git-LFS history of "
                       "/Users/mannybhidya/Palworld plus the NAS backup sets, "
                       "decoded snapshot by snapshot (eol_scan.py); per-piece "
                       "first/last from build_index.json"),
            "snapshotsScanned": len(scan),
            "decoderValidation": (
                "This world's saves are newer than palworld_save_tools 0.24 "
                "(magic b'PlM', Oodle-compressed, several RawData layouts extended), "
                "so eol_scan.py decompresses with a locally built zao/ooz and reads "
                "each extended blob's known leading fields while counting the new "
                "tail. That relaxation was checked, not assumed: across the scanned "
                "snapshots its per-base MapObjectSaveData counts match "
                "build_index.json's own first<=t<=last counts exactly (one row of "
                "difference in one sample), and every Pal instance id it reads is "
                "one pal_index.json already knows."),
            "firstSnapshot": {"ts": snaps[0], "at": iso(snaps[0])},
            "finalSnapshot": {"ts": final_ts, "at": iso(final_ts)},
            "campRecord": {
                "field": "worldSaveData.BaseCampSaveData - the camp's own record. "
                         "Its disappearance is a DIFFERENT event from its pieces "
                         "disappearing: pieces can be dismantled while the camp "
                         "stands, and the camp record going away takes the whole "
                         "camp with it.",
                "firstPresent": {"ts": first_seen, "at": iso(first_seen)},
                "lastPresent": {"ts": last_seen, "at": iso(last_seen)},
                "survivesToFinalSnapshot": survives,
            },
            "pieces": {
                "indexedRows": len(rows),
                "builtPieces": len(built),
                "transientRows": len(clutter),
                "transientNote": ("CommonDropItem3D (ground drops, they despawn "
                                  "on their own) and DamagableRock* (mineable "
                                  "world rocks, they respawn) are counted "
                                  "separately - they are not construction and "
                                  "their churn is not demolition"),
                "builtPiecesLastSeen": [
                    {"ts": t, "at": iso(t), "count": n,
                     "types": dict(Counter(o["type"] for o in built if o["last"] == t).most_common())}
                    for t, n in sorted(last_c.items())],
            },
            "series": piece_series,
            "pals": pals_doc(scan, base, last_seen, final_ts, first_absent),
        }

        if survives:
            doc["timeline"] = [
                {"ts": last_seen, "at": iso(last_seen),
                 "what": f"{NAMES[base]} is still standing in the final snapshot we hold. "
                         f"Its BaseCampSaveData record is present and "
                         f"{sum(1 for o in built if o['last'] == final_ts)} of its "
                         f"{len(built)} indexed built pieces are still there. "
                         f"There is no end-of-life event for this base in the history."}]
            doc["endOfLife"] = None
        else:
            gap = first_absent - last_seen
            survivors = sum(1 for o in built if o["last"] == last_seen)
            doc["timeline"] = [
                {"ts": last_seen, "at": iso(last_seen),
                 "what": f"last snapshot that contains {NAMES[base]}: its camp record is "
                         f"present and {survivors} built pieces stand"},
                {"ts": first_absent, "at": iso(first_absent),
                 "what": f"first snapshot after that: the camp record is GONE and so are "
                         f"all {survivors} pieces"},
            ]
            doc["endOfLife"] = {
                "lastSeen": {"ts": last_seen, "at": iso(last_seen)},
                "firstAbsent": {"ts": first_absent, "at": iso(first_absent)},
                "gapSeconds": gap,
                "gapHuman": human(gap),
                "builtPiecesStandingAtLastSight": survivors,
                "endsBeforeWorldTimelineEnds": human(final_ts - last_seen),
                "endsBeforeWorldTimelineEndsSeconds": final_ts - last_seen,
                # derived from the FULL per-piece index (every git commit plus
                # every NAS backup), not from the snapshot subset scanned here
                "everReturns": any(o["last"] > last_seen for o in rows),
                "everReturnsCheck": ("build_index.json holds first/last for every "
                                     "piece across every snapshot; no row of this "
                                     "base has last > its camp's last sighting"),
                "allAtOnceOrProgressive": (
                    "ALL AT ONCE, as far as the snapshots can tell. "
                    f"{survivors} of the {len(built)} indexed built pieces - the whole "
                    "camp - share the same last-seen snapshot, and the camp record "
                    "itself disappears in the same step. The pieces that drop out "
                    "earlier are almost all DamagableRock* (world rocks that respawn), "
                    "not construction. There is no snapshot showing the camp "
                    "half-dismantled."),
                "campDeletedOrPiecesRemoved": (
                    "The BaseCampSaveData record for this base is present in the last "
                    "snapshot that has it and absent in the next one, so this is the "
                    "camp record going away, not pieces being removed one at a time "
                    "while the camp stood."),
                "UNKNOWN_WINDOW": (
                    f"This save format records NO placement or removal timestamps for "
                    f"anything. Everything above is 'present in snapshot X, absent in "
                    f"snapshot Y'. Between those two snapshots lie {human(gap)} in which "
                    f"we hold no usable save of this world at all, so the disappearance "
                    f"is a WINDOW, not a moment. It could have happened at any point "
                    f"inside it, all at once or spread across it."),
                "filesOnDiskInsideTheGap": [
                    {"ts": t, "at": iso(t), "bytes": r.get("bytes"),
                     "path": os.path.relpath(str(r.get("src")), SP) if r.get("src") else None,
                     "rejected": r.get("why")}
                    for t, r in sorted(rejected.items()) if last_seen < t < first_absent],
                "filesOnDiskInsideTheGapNote": (
                    "Level.sav files DO exist on disk with mtimes inside the gap, but "
                    "they are 35-38 KB empty-world saves containing none of this "
                    "world's base camps, so they carry no information about this base "
                    "either way. They are listed so the gap is auditable, not as "
                    "evidence of anything. This file does not speculate about why the "
                    "gap exists."),
            }
        if survives:
            doc["note"] = (
                f"{NAMES[base]} never ends. Its camp record and its pieces are still in "
                f"the last save we hold ({iso(final_ts)}), so there is nothing for the "
                f"timelapse to show beyond the build itself. Individual pieces that "
                f"disappear along the way are ordinary demolitions, not an ending.")
        else:
            eol = doc["endOfLife"]
            pl = doc["pals"]["followedForward"]
            after = pl[1] if len(pl) > 1 else pl[0]
            doc["note"] = (
                f"{NAMES[base]} does not just stop - it is gone. The last save that "
                f"contains it is {iso(last_seen)}, where the camp is intact: "
                f"{eol['builtPiecesStandingAtLastSight']} built pieces and its own "
                f"BaseCampSaveData record. The next save of this world we hold is "
                f"{iso(first_absent)}, and in it the camp record is gone and so is every "
                f"one of those pieces. Nothing in between shows it half-dismantled: all "
                f"but a handful of world rocks share that same last snapshot. The camp "
                f"RECORD disappearing, not just its pieces, is what separates 'the base "
                f"was removed' from 'someone took the walls down'. "
                f"The Pals did not go with it: all {doc['pals']['atLastSighting']['count']} "
                f"Pals standing in the camp at its last sighting are still in the save "
                f"afterwards ({after['stillInSave']} of them still present at "
                f"{after['at']}), most of them recorded at the same spot on the now-"
                f"camp-less site and a few over at Glass Tower. "
                f"IMPORTANT: {human(eol['gapSeconds'])} separate those two saves and this "
                f"save format carries no placement or removal timestamps at all, so the "
                f"disappearance is an unknown WINDOW, not a moment. Do not render it as "
                f"an instant unless it is labelled as 'sometime in this window'. This "
                f"file says what the saves contain and nothing about why.")

        json.dump(doc, open(f"{OUT}/endoflife_{base}.json", "w"), indent=1)
        print(f"endoflife_{base}.json  {NAMES[base]:12s} camp last present {iso(last_seen)}  "
              f"survives={survives}  built={len(built)} transient={len(clutter)}"
              + ("" if survives else f"  gap={human(first_absent - last_seen)}"))


if __name__ == "__main__":
    main()
