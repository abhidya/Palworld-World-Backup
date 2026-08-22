"""Wild-Pal spawn points for the base timelapse, straight out of the game pak.

The save cannot supply wild Pals (CharacterSaveParameterMap only ever holds
OWNED Pals), so the only honest source is the shipped spawner data:

  * every World Partition cell of PL_MainWorld5 (MainGrid_L0_*) is read with
    CUE4Parse and every actor whose class carries a SpawnGroupList is kept ->
    allspawners_L0.json.  That gives the spawner's authored world transform.
  * every BP_PalSpawner_* blueprint's class default object carries its own
    spawn table (SpawnGroupList: weighted groups of PalID + level range +
    count range + time/weather condition) -> spawner_bp.json.

Both come from palspawn (see palspawn/Program.cs).  Nothing here is invented:
positions are the spawner actors' own RelativeLocation, verbatim, and the
species/level/weight are the blueprint's own rows.

Writes mappal/public/union/wildpals_<base>.json per base.
"""
import json, glob, os, re, math, collections

SP = os.path.dirname(os.path.abspath(__file__))
OUT = f"{SP}/mappal/public/union"
NAMES = {"07f13218": "Glass Tower", "16fca097": "Wooden Camp",
         "de44d9f4": "Stone Works", "5fed0024": "Lost Camp"}
GROUND_R = 60000.0          # 600 m, same as the terrain layer's GROUND_R
CAP = 400                   # max spawn points per base, nearest first

NOTE = ("These are spawn-table positions authored in the game pak - where the "
        "game's wild-Pal spawners stand and which species each one may roll - "
        "not recorded positions of individual Pals at any moment in this "
        "server's history.")


def bases_from_union():
    out = {}
    for f in sorted(glob.glob(f"{OUT}/union_*.json")):
        b = os.path.basename(f)[6:14]
        d = json.load(open(f))
        bc = (d.get("base_camp") or {}).get("value", {}).get("RawData", {}).get("value")
        if not bc:
            continue
        tr = bc["transform"]["translation"]
        out[b] = {"x": tr["x"], "y": tr["y"], "z": tr["z"],
                  "area_range": float(bc["area_range"])}
    return out


def mesh_lookup():
    man = json.load(open(f"{SP}/pal_manifest.json"))
    have = {os.path.basename(p)[:-4] for p in glob.glob(f"{SP}/pal_meshes/*.glb")}

    def mesh_for(cid):
        for key in (cid, cid.replace("BOSS_", ""), cid.replace("PREDATOR_", "")):
            e = man.get(key)
            if e and e.get("resolved") and e.get("meshName") in have:
                return e["meshName"]
        for k, e in man.items():
            if k.lower() == cid.lower() and e.get("meshName") in have:
                return e["meshName"]
        return None
    return mesh_for


def enum(v):
    return v.split("::")[-1] if isinstance(v, str) and "::" in v else v


def main():
    bases = bases_from_union()
    acts = json.load(open(f"{SP}/allspawners_L0.json"))
    bp = json.load(open(f"{SP}/spawner_bp.json"))
    cdo = {k[len("Default__"):]: v for k, v in bp.items()}
    mesh_for = mesh_lookup()

    # only actors whose class actually ships a spawn table are Pal spawners;
    # BP_PalMapObjectSpawner_* (ore/logs/berries), NPC camp and city spawners
    # carry none and are not wild Pals.
    pal_actors = [a for a in acts if a["cls"] in cdo]
    print(f"spawner actors world-wide with a SpawnGroupList: {len(pal_actors)}")

    for b, meta in sorted(bases.items()):
        bx, by = meta["x"], meta["y"]
        near = []
        for a in pal_actors:
            d = math.hypot(a["loc"][0] - bx, a["loc"][1] - by)
            if d <= GROUND_R:
                near.append((d, a))
        near.sort(key=lambda t: t[0])
        dropped_cap = max(0, len(near) - CAP)
        kept = near[:CAP]

        wild = []
        nomesh = collections.Counter()
        empty_groups = 0
        spec = collections.Counter()
        for d, a in kept:
            c = cdo[a["cls"]]
            sname = c.get("SpawnerName") or a["cls"]
            sid = re.sub(r"^.*_UAID_", "", a["name"]) or a["name"]
            groups = c.get("SpawnGroupList") or []
            if not groups:
                empty_groups += 1
                continue
            x, y, z = a["loc"]
            yaw = a["rot"][1]
            for gi, g in enumerate(groups):
                if not isinstance(g, dict):
                    continue
                for pi, p in enumerate(g.get("PalList") or []):
                    if not isinstance(p, dict):
                        continue
                    cid = (p.get("PalID") or {}).get("Key")
                    if cid in (None, "", "None", "RowName"):
                        continue
                    m = mesh_for(cid)
                    if not m:
                        nomesh[cid] += 1
                        continue
                    spec[cid] += 1
                    wild.append({
                        "id": f"{sid}:{gi}:{pi}",
                        "char": cid,
                        "url": f"/pal_meshes/{m}.glb",
                        "x": round(x, 2), "y": round(y, 2), "z": round(z, 2),
                        "yaw": round(yaw, 3),
                        "level": [p.get("Level", 0), p.get("Level_Max", 0)],
                        "num": [p.get("Num", 0), p.get("Num_Max", 0)],
                        "spawnerId": sid,
                        "spawner": sname,
                        "weight": g.get("Weight", 0),
                        "onlyTime": enum(g.get("OnlyTime")),
                        "onlyWeather": enum(g.get("OnlyWeather")),
                    })

        doc = {
            "base": b,
            "source": "pak-spawner",
            "note": NOTE,
            "z": ("verbatim - each entry's x/y/z is the spawner actor's own "
                  "authored RelativeLocation from its World Partition cell, "
                  "unmodified; nothing was snapped to terrain"),
            "grouping": ("entries sharing a spawnerId are the mutually exclusive "
                         "weighted alternatives of one spawner; the game rolls one "
                         "group per spawn, so pick by weight rather than drawing all"),
            "radiusCm": GROUND_R,
            "spawnRadiusCm": 15000,
            "spawnPoints": len({w["spawnerId"] for w in wild}),
            "spawnPointsInRange": len(near),
            "droppedForCap": dropped_cap,
            "droppedNoMesh": dict(nomesh),
            "droppedEmptyTable": empty_groups,
            "spawnPointsNoUsableRow": len(kept) - empty_groups - len({w["spawnerId"] for w in wild}),
            "speciesCount": len(spec),
            "wild": wild,
        }
        path = f"{OUT}/wildpals_{b}.json"
        json.dump(doc, open(path, "w"))
        print(f"  wildpals_{b}.json  {doc['spawnPoints']:4d} spawn points  "
              f"{len(wild):5d} entries  {len(spec):3d} species  "
              f"{os.path.getsize(path)//1024} KB   ({NAMES.get(b, b)})"
              + (f"  droppedForCap={dropped_cap}" if dropped_cap else "")
              + (f"  noMesh={dict(nomesh)}" if nomesh else ""))


if __name__ == "__main__":
    main()
