"""Resolve each wild-Pal spawner to the ONE group the game would roll, so the
renderer draws a spawn point instead of a pile.

THE BUG THIS FIXES
  wildpals_<base>.json lists every row of every spawner's SpawnGroupList. Rows
  sharing a spawnerId are the spawner's mutually exclusive weighted
  alternatives - the game rolls ONE group per spawner, not all of them. Drawing
  the raw `wild` array stacks ~8-13 Pals on every spawn point.

WHAT THIS SCRIPT DOES
  * Regroups the flat rows back into (spawnerId, groupIndex) groups. Every row
    of one group carries that group's Weight and OnlyTime; a group's PalList may
    hold more than one Pal, and those DO spawn together.
  * Picks one group per spawner, PROPORTIONAL to Weight (not argmax - argmax
    would collapse every plains spawner onto the same species and erase the
    diversity that is actually in the table).
  * Does it twice: once over the day-eligible pool and once over the
    night-eligible pool, so the renderer's in-game clock can choose. See
    "TIME" below.
  * Rolls each picked row's Num..Num_Max count and Level..Level_Max level.
  * Emits one record per Pal instance, ready to draw.

DETERMINISM (no Math.random, no wall clock)
  Every roll is a SHA-256 of a fixed string, so the same input file always
  produces the same output, on any machine, in any language:

      u(key) = int(sha256(key)[:8], big-endian) / 2**64        -> [0, 1)

  with keys
      group pick : "<spawnerId>|<phase>"
      count      : "<spawnerId>|<phase>|num|<rowId>"
      level      : "<spawnerId>|<phase>|lvl|<rowId>"
      scatter    : "<spawnerId>|<phase>|off|<rowId>|<instanceIndex>"

  `seed` on each record is the first 8 hex of the group-pick key's digest, so a
  reader can re-derive and verify the pick.

TIME
  OnlyTime is the group's own EPalOneDayTimeType. Across all 476 spawner
  classes in this pak only two values ever occur: Undefined (1793 groups) and
  Night (346). Undefined = no time restriction, so:
      day pool   = groups with OnlyTime Undefined
      night pool = groups with OnlyTime Undefined OR Night
  A spawner whose every group is Night-only therefore has NO day pick and is
  listed in `spawnersWithoutDayPick` rather than being given an invented one.

WEIGHT 0
  Some groups ship Weight 0. A zero-weight group can never be rolled, so it is
  dropped from the pool and counted in `zeroWeightGroupsDropped`.

COUNT (Num / Num_Max)
  A picked row's count is an inclusive uniform integer over [Num, Num_Max],
  drawn from the hash above. Instances beyond the first need somewhere to
  stand: the save has no per-Pal position for wild Pals at all, so the extra
  instances are placed on a small deterministic ring around the spawner's own
  authored point. That ring is DERIVED, not data - `x/y/z` on every record stay
  the spawner's verbatim authored RelativeLocation and the ring is exposed
  separately as `dx/dy` (and pre-added as `drawX/drawY`), so a renderer that
  wants only real coordinates can ignore it.

PVP
  Spawner class PvP_21_1_1 is the shipped PvP-arena table. It is real pak data
  and it really does stand within 600 m of Glass Tower, but it is arena content,
  not the island's wildlife. Every record it produces carries `pvp: true` and
  the file reports both totals, so hiding it is one filter.

Reads  mappal/public/union/wildpals_<base>.json   (not modified)
Writes mappal/public/union/wildpals_draw_<base>.json
"""
import json, os, glob, hashlib, math, collections

SP = os.path.dirname(os.path.abspath(__file__))
OUT = f"{SP}/mappal/public/union"
NAMES = {"07f13218": "Glass Tower", "16fca097": "Wooden Camp",
         "de44d9f4": "Stone Works", "5fed0024": "Lost Camp"}
RING_CM = 250.0          # radius of the derived multi-instance scatter ring
PVP_PREFIX = "PvP"


def digest(key):
    return hashlib.sha256(key.encode("utf-8")).digest()


def u01(key):
    """Deterministic [0,1) from a string key."""
    return int.from_bytes(digest(key)[:8], "big") / 2 ** 64


def randint(key, lo, hi):
    """Inclusive uniform integer in [lo, hi], deterministic in `key`."""
    if hi <= lo:
        return lo
    return lo + int(u01(key) * (hi - lo + 1))


def pick_group(groups, key):
    """Weight-proportional choice over `groups` [(gi, weight, rows)]. Returns gi."""
    total = sum(g[1] for g in groups)
    if total <= 0:
        return None
    target = u01(key) * total
    acc = 0.0
    for gi, w, _rows in groups:
        acc += w
        if target < acc:
            return gi
    return groups[-1][0]


def resolve(doc, base):
    wild = doc["wild"]
    # spawnerId -> groupIndex -> rows      (rows of one group spawn together)
    spawners = collections.defaultdict(lambda: collections.defaultdict(list))
    for e in wild:
        sid, gi, _pi = e["id"].split(":")
        spawners[sid][int(gi)].append(e)

    out = {"day": [], "night": []}
    stats = {
        "day": collections.Counter(), "night": collections.Counter(),
    }
    zero_w = 0
    no_day = []
    no_pool = []
    species = {"day": collections.Counter(), "night": collections.Counter()}
    pvp_spawners = set()

    for sid, groups in spawners.items():
        pools = {"day": [], "night": []}
        for gi, rows in sorted(groups.items()):
            w = rows[0]["weight"]
            only = rows[0]["onlyTime"]
            if rows[0]["spawner"].startswith(PVP_PREFIX):
                pvp_spawners.add(sid)
            if w <= 0:
                zero_w += 1
                continue
            if only == "Night":
                pools["night"].append((gi, w, rows))
            else:                       # Undefined = no time restriction
                pools["day"].append((gi, w, rows))
                pools["night"].append((gi, w, rows))

        for phase in ("day", "night"):
            pool = pools[phase]
            if not pool:
                (no_day if phase == "day" else no_pool).append(sid)
                continue
            key = f"{sid}|{phase}"
            gi = pick_group(pool, key)
            rows = groups[gi]
            seed = digest(key)[:4].hex()
            for r in rows:
                lo, hi = (r["num"] or [1, 1])[0] or 1, (r["num"] or [1, 1])[1] or 1
                n = max(1, randint(f"{sid}|{phase}|num|{r['id']}", lo, hi))
                llo, lhi = (r["level"] or [1, 1])[0] or 1, (r["level"] or [1, 1])[1] or 1
                lvl = randint(f"{sid}|{phase}|lvl|{r['id']}", llo, lhi)
                pvp = r["spawner"].startswith(PVP_PREFIX)
                for i in range(n):
                    if n == 1:
                        dx = dy = 0.0
                    else:
                        a = 2 * math.pi * (i / n + u01(f"{sid}|{phase}|off|{r['id']}|{i}"))
                        dx = round(RING_CM * math.cos(a), 2)
                        dy = round(RING_CM * math.sin(a), 2)
                    rec = {
                        "id": f"{r['id']}#{phase[0]}{i}",
                        "char": r["char"], "url": r["url"],
                        "x": r["x"], "y": r["y"], "z": r["z"], "yaw": r["yaw"],
                        "dx": dx, "dy": dy,
                        "drawX": round(r["x"] + dx, 2), "drawY": round(r["y"] + dy, 2),
                        "drawZ": r["z"],
                        "level": lvl, "levelRange": r["level"],
                        "instance": i, "count": n,
                        "spawnerId": sid, "spawner": r["spawner"],
                        "group": gi, "weight": r["weight"],
                        "onlyTime": r["onlyTime"], "seed": seed,
                    }
                    if pvp:
                        rec["pvp"] = True
                    out[phase].append(rec)
                    stats[phase]["instances"] += 1
                    if pvp:
                        stats[phase]["pvpInstances"] += 1
                    species[phase][r["char"]] += 1

    doc_out = dict(doc)
    doc_out.pop("wild", None)
    doc_out.update({
        "kind": "wildpals-draw",
        "sourceFile": f"wildpals_{base}.json",
        "note": doc["note"],
        "grouping": doc["grouping"],
        "drawNote": (
            "day[] and night[] are the RESOLVED draw lists: one group per spawner, "
            "chosen weight-proportionally, already expanded to one record per Pal "
            "instance. Draw one array or the other, never both, and never the "
            "source file's `wild` array (that one holds every mutually exclusive "
            "alternative and stacks ~8-13 Pals per point)."),
        "method": {
            "pick": "weight-proportional over the spawner's eligible groups; "
                    "zero-weight groups dropped",
            "deterministic": "u = int(sha256(key)[:8], 'big') / 2**64; "
                             "group key '<spawnerId>|<phase>', count key "
                             "'<spawnerId>|<phase>|num|<rowId>', level key "
                             "'...|lvl|...', scatter key '...|off|<rowId>|<i>'. "
                             "No RNG, no wall clock - byte-identical on re-run.",
            "seedField": "first 4 bytes of the group key's digest, hex - lets a "
                         "reader re-derive and check the pick",
            "time": "OnlyTime Undefined = unrestricted, Night = night only. "
                    "day pool = Undefined groups; night pool = Undefined + Night "
                    "groups. Only these two values occur in this pak.",
            "count": "inclusive uniform integer over the row's [Num, Num_Max]",
            "level": "inclusive uniform integer over the row's [Level, Level_Max]; "
                     "levelRange keeps the authored range",
            "positions": "x/y/z are the spawner's verbatim authored "
                         "RelativeLocation, unchanged. dx/dy are a DERIVED ring "
                         f"of radius {RING_CM:.0f} cm used only to keep the extra "
                         "instances of a multi-Pal group from occupying one point; "
                         "drawX/drawY are x+dx / y+dy for convenience. Ignore "
                         "dx/dy/drawX/drawY to draw only measured coordinates.",
            "pvp": "records from spawner class PvP_21_1_1 (the shipped PvP-arena "
                   "table) carry pvp:true; counts below are given with and without",
        },
        "spawners": len(spawners),
        "zeroWeightGroupsDropped": zero_w,
        "spawnersWithoutDayPick": sorted(no_day),
        "spawnersWithNoUsablePool": sorted(no_pool),
        "pvpSpawnerIds": sorted(pvp_spawners),
        "counts": {
            phase: {
                "instances": stats[phase]["instances"],
                "instancesExcludingPvp": stats[phase]["instances"] - stats[phase]["pvpInstances"],
                "pvpInstances": stats[phase]["pvpInstances"],
                "species": len(species[phase]),
                "speciesExcludingPvp": len({r["char"] for r in out[phase] if not r.get("pvp")}),
            } for phase in ("day", "night")
        },
        "day": out["day"],
        "night": out["night"],
    })
    return doc_out


def main():
    for f in sorted(glob.glob(f"{OUT}/wildpals_*.json")):
        if "_draw_" in os.path.basename(f):
            continue
        base = os.path.basename(f)[len("wildpals_"):-len(".json")]
        doc = json.load(open(f))
        d = resolve(doc, base)
        path = f"{OUT}/wildpals_draw_{base}.json"
        json.dump(d, open(path, "w"))
        c = d["counts"]
        print(f"wildpals_draw_{base}.json  {NAMES.get(base, base):12s} "
              f"spawners={d['spawners']:4d}  raw_entries={len(doc['wild']):5d}  "
              f"day={c['day']['instances']:4d} (no-pvp {c['day']['instancesExcludingPvp']:4d}, "
              f"{c['day']['species']:2d} spp)  "
              f"night={c['night']['instances']:4d} (no-pvp {c['night']['instancesExcludingPvp']:4d}, "
              f"{c['night']['species']:2d} spp)  "
              f"pvpSpawners={len(d['pvpSpawnerIds']):3d}  "
              f"zeroW={d['zeroWeightGroupsDropped']:3d}  "
              f"noDayPick={len(d['spawnersWithoutDayPick'])}  "
              f"{os.path.getsize(path)//1024}KB")


if __name__ == "__main__":
    main()
