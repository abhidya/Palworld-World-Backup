"""Per-snapshot Pal index, built the same way build_index.json was built for
structures: walk every Level.sav we hold (git-LFS history of /Users/mannybhidya/
Palworld + the NAS backup sets) and record, for each Pal, where the save says it
was at that moment.

WHAT IS REAL DATA HERE
  - instance id, CharacterID, Level, Gender, NickName: verbatim from
    CharacterSaveParameterMap[i].value.RawData.object.SaveParameter.
  - position: verbatim SaveParameter.LastJumpedLocation (a real recorded
    FVector, in Unreal cm). Palworld does NOT persist a live Pal transform;
    LastJumpedLocation is the only recorded world position on the record and
    means "where this Pal last jumped", not "where it is standing". It is used
    as-is. Nothing is jittered, scattered, or synthesised. A Pal with no
    LastJumpedLocation is simply absent from this index.
  - base assignment: the base camp whose BaseCampSaveData.transform.translation
    is within area_range (horizontal) of that recorded position, nearest wins.
    area_range comes from the save too.
  - first/last: real snapshot timestamps (git commit time / file mtime), same
    clock build_index.json uses.

Output: pal_index.json
  {"bases": {b8: {x,y,z,area_range,name}},
   "snapshots": [ts,...],                       # every snapshot successfully read
   "pals": [{"id","char","level","gender","nick","base",
             "first","last","track":[[ts,x,y,z],...]}]}
  `track` is de-duplicated: a sample is emitted only when the recorded position
  actually changed, so a Pal that never jumped again carries one sample.
"""
import json, os, sys, glob, time, subprocess
from collections import defaultdict
from multiprocessing import Pool

SP = os.environ.get("PALTL_WORK") or os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("PALTL_REPO") or os.path.expanduser("~/Palworld")
LINEAGE_GUILD = "017a45a0"   # same lineage filter merge_all.py uses


def read_raw(kind, ref):
    if kind == "git":
        blob = subprocess.run(["git", "-C", REPO, "show", f"{ref}:world/current/Level.sav"],
                              capture_output=True).stdout
        return subprocess.run(["git", "-C", REPO, "lfs", "smudge"],
                              input=blob, capture_output=True).stdout
    return open(ref, "rb").read()


def work(job):
    ts, kind, ref = job
    try:
        import ooz  # noqa: F401
        from palworld_save_tools.gvas import GvasFile
        from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES
        from palworld_save_tools.palsav import decompress_sav_to_gvas
        raw = read_raw(kind, ref)
        if len(raw) < 100000:
            return (ts, None, None)
        gvas, _ = decompress_sav_to_gvas(raw)
        w = GvasFile.read(gvas, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES) \
            .dump()["properties"]["worldSaveData"]["value"]

        guilds = {str(g["value"]["RawData"]["value"].get("group_id"))[:8]
                  for g in w["GroupSaveDataMap"]["value"]
                  if g["value"]["RawData"]["value"].get("group_type") == "EPalGroupType::Guild"}
        if LINEAGE_GUILD not in guilds:
            return (ts, None, None)          # a different playthrough in the backup set

        bases = {}
        for b in w["BaseCampSaveData"]["value"]:
            rd = b["value"]["RawData"]["value"]
            tr = rd["transform"]["translation"]
            bases[str(b["key"])[:8]] = (tr["x"], tr["y"], tr["z"], float(rd.get("area_range", 0)))

        pals = []
        for e in w["CharacterSaveParameterMap"]["value"]:
            sp = e["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"]
            if sp.get("IsPlayer", {}).get("value"):
                continue
            loc = sp.get("LastJumpedLocation")
            if not loc:
                continue
            v = loc["value"]
            iid = str(e["key"]["InstanceId"]["value"])
            pals.append((
                iid,
                sp["CharacterID"]["value"],
                v["x"], v["y"], v["z"],
                (sp.get("Level") or {}).get("value", {}).get("value", 1),
                (sp.get("Gender") or {}).get("value", {}).get("value", ""),
                (sp.get("NickName") or {}).get("value", ""),
            ))
        return (ts, bases, pals)
    except Exception as e:
        return (ts, None, str(e)[:120])


def main():
    src = {}
    for line in open(f"{SP}/commits.txt"):
        c, t = line.split()
        src.setdefault(int(t), ("git", c))
    for pat in ("nas", "nasbk", "nasbk2", "nasbk3"):
        for p in glob.glob(f"{SP}/{pat}/**/Level.sav", recursive=True):
            src.setdefault(int(os.path.getmtime(p)), ("file", p))
    jobs = [(ts, k, r) for ts, (k, r) in sorted(src.items())]
    print(f"snapshots to read: {len(jobs)}", flush=True)

    base_meta = {}
    first, last, meta = {}, {}, {}
    track = defaultdict(list)          # iid -> [(ts,x,y,z)] deduped
    lastpos = {}
    snapshots = []
    done = t0 = 0
    t0 = time.time()
    skipped = 0
    with Pool(int(os.environ.get("JOBS", "3"))) as pool:
        for ts, bases, pals in pool.imap(work, jobs, chunksize=2):
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(jobs)}  pals={len(first)} used={len(snapshots)} "
                      f"{time.time()-t0:.0f}s", flush=True)
            if bases is None:
                skipped += 1
                continue
            snapshots.append(ts)
            for b, v in bases.items():
                base_meta[b] = v
            for iid, cid, x, y, z, lvl, gen, nick in pals:
                if iid not in first:
                    first[iid] = ts
                last[iid] = ts
                meta[iid] = (cid, lvl, gen, nick)
                p = (round(x, 1), round(y, 1), round(z, 1))
                if lastpos.get(iid) != p:
                    lastpos[iid] = p
                    track[iid].append([ts, p[0], p[1], p[2]])

    snapshots.sort()
    NAMES = {"07f13218": "Glass Tower", "16fca097": "Wooden Camp",
             "de44d9f4": "Stone Works", "5fed0024": "Lost Camp"}

    def owner(x, y):
        """Nearest base whose recorded area_range (horizontal) contains (x,y).
        Both the base centre and area_range are read straight from
        BaseCampSaveData; nothing here is a guessed radius."""
        best, bd = None, None
        for b, (bx, by, bz, r) in base_meta.items():
            d = ((x - bx) ** 2 + (y - by) ** 2) ** 0.5
            if d <= r and (bd is None or d < bd):
                best, bd = b, d
        return best

    out_pals = []
    for iid, tr in track.items():
        cid, lvl, gen, nick = meta[iid]
        # A Pal's base is decided per sample; the record's `base` is the base it
        # was inside for the majority of its samples, and `track` keeps every
        # sample with the base it was inside at that moment (null = outside every
        # base, e.g. out on an expedition or carried by the player).
        cnt = defaultdict(int)
        tr2 = []
        for ts, x, y, z in tr:
            b = owner(x, y)
            if b:
                cnt[b] += 1
            tr2.append([ts, x, y, z, b])
        home = max(cnt, key=cnt.get) if cnt else None
        out_pals.append({"id": iid, "char": cid, "level": lvl, "gender": gen,
                         "nick": nick, "base": home,
                         "first": first[iid], "last": last[iid], "track": tr2})

    out = {"bases": {b: {"x": v[0], "y": v[1], "z": v[2], "area_range": v[3],
                         "name": NAMES.get(b, b)} for b, v in base_meta.items()},
           "snapshots": snapshots, "pals": out_pals}
    json.dump(out, open(f"{SP}/pal_index.json", "w"))

    print(f"\nsnapshots used={len(snapshots)} skipped={skipped}")
    print(f"pals with a recorded position at some point: {len(out_pals)}")
    per = defaultdict(int)
    for p in out_pals:
        per[p["base"]] += 1
    for b, n in sorted(per.items(), key=lambda kv: -kv[1]):
        print(f"  {NAMES.get(b, b) if b else '(outside every base)':22s} {n:4d} pals")
    print(f"bases seen: {[(b, NAMES.get(b,b)) for b in base_meta]}")
    print(f"-> {SP}/pal_index.json  ({os.path.getsize(SP+'/pal_index.json')//1024} KB) "
          f"in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
