"""Turn pal_index.json / player_index.json / the union files into the per-base
files the timelapse renderer feeds to MapPal's Pal and player layers.

Writes, per base b:
  mappal/public/union/pals_<b>.json
     {"pals":[{"id","char","url","level","first","last",
               "track":[[ts,x,y,z],...]}]}
     One entry per Pal the save ever placed inside this base's own recorded
     area_range. `track` is the Pal's recorded LastJumpedLocation, sampled at
     every snapshot where it changed, filtered to the samples that fall inside
     THIS base. At render time t the Pal is drawn at its latest sample <= t,
     and only while t is inside [first,last] (the window where the save actually
     held a record for that Pal).

  mappal/public/union/players_<b>.json
     {"players":[{"uid","runs":[{"from","to","parts":[...],"x","y","z","q*"}]}]}
     A run per stretch of history over which BOTH the player's recorded
     appearance and their recorded LastTransform were unchanged and the recorded
     position was inside this base. Snapshots with no LastTransform produce no
     run — never back-filled.

  mappal/public/union/builders_<b>.json
     {"builders":{instanceId: "54001e88"}, "counts":{uid: n}}
     Straight out of each map object's own `build_player_uid`. The all-zero uid
     means the save recorded no builder for that piece and is emitted as null.
"""
import json, os, glob
from collections import defaultdict

SP = os.path.dirname(os.path.abspath(__file__))
OUT = f"{SP}/mappal/public/union"
NAMES = {"07f13218": "Glass Tower", "16fca097": "Wooden Camp",
         "de44d9f4": "Stone Works", "5fed0024": "Lost Camp"}
ZERO = "00000000-0000-0000-0000-000000000000"


def main():
    # pal_index.json is the full historical walk; when it is not ready yet the
    # player/builder halves still build (the Pal files are then left alone).
    try:
        pal = json.load(open(f"{SP}/pal_index.json"))
    except FileNotFoundError:
        print("  pal_index.json not ready - Pal files left as-is")
        pal = None
    if pal is None:
        live = json.load(open(f"{SP}/player_index.json"))  # noqa: F841
        return _players_and_builders(_bases_from_union())
    return _full(pal)


def _bases_from_union():
    """Base centre + area_range straight out of each union file's own
    BaseCampSaveData record."""
    out = {}
    for f in sorted(glob.glob(f"{SP}/mappal/public/union/union_*.json")):
        b = os.path.basename(f)[6:14]
        d = json.load(open(f))
        bc = (d.get("base_camp") or {}).get("value", {}).get("RawData", {}).get("value")
        if not bc:
            continue
        tr = bc["transform"]["translation"]
        out[b] = {"x": tr["x"], "y": tr["y"], "z": tr["z"],
                  "area_range": float(bc["area_range"]), "name": NAMES.get(b, b)}
    return out


def _full(pal):
    manifest = json.load(open(f"{SP}/pal_manifest.json"))
    bases = pal["bases"]

    have_mesh = {os.path.basename(p)[:-4] for p in glob.glob(f"{SP}/pal_meshes/*.glb")}

    def mesh_for(cid):
        """Recorded CharacterID -> extracted SK_*.glb, via pal_manifest.json."""
        for key in (cid, cid.replace("BOSS_", ""), cid.replace("PREDATOR_", "")):
            e = manifest.get(key)
            if e and e.get("resolved") and e.get("meshName") in have_mesh:
                return e["meshName"]
        for k, e in manifest.items():          # case-insensitive fallback
            if k.lower() == cid.lower() and e.get("meshName") in have_mesh:
                return e["meshName"]
        return None

    perbase = defaultdict(list)
    nomesh = defaultdict(int)
    for p in pal["pals"]:
        if not p["base"]:
            continue
        m = mesh_for(p["char"])
        if not m:
            nomesh[p["char"]] += 1
            continue
        track = [[t, x, y, z] for t, x, y, z, b in p["track"] if b == p["base"]]
        if not track:
            continue
        perbase[p["base"]].append({
            "id": p["id"], "char": p["char"], "url": f"/pal_meshes/{m}.glb",
            "level": p["level"], "first": p["first"], "last": p["last"], "track": track,
        })

    for b, pals in perbase.items():
        json.dump({"base": b, "pals": pals}, open(f"{OUT}/pals_{b}.json", "w"))
        print(f"  pals_{b}.json  {len(pals):4d} pals  "
              f"{os.path.getsize(f'{OUT}/pals_{b}.json')//1024} KB   ({NAMES.get(b,b)})")
    for b in NAMES:
        if b not in perbase:
            json.dump({"base": b, "pals": []}, open(f"{OUT}/pals_{b}.json", "w"))
            print(f"  pals_{b}.json     0 pals   ({NAMES.get(b,b)}) "
                  f"- no Pal ever recorded inside this base's radius")
    if nomesh:
        print(f"  Pals skipped for want of an extracted mesh: {dict(nomesh)}")

    _players_and_builders(bases)


def _players_and_builders(bases):
    # ---- players --------------------------------------------------------
    try:
        pidx = json.load(open(f"{SP}/player_index.json"))
        parts = json.load(open(f"{SP}/player_parts.json"))
    except FileNotFoundError:
        print("  (player_index.json / player_parts.json not ready - skipping players)")
        pidx = None

    if pidx:
        def lookkey(a):
            return json.dumps({k: a[k] for k in
                               ("body", "head", "hair", "eqBody", "eqHead", "ovHead", "ovBody")},
                              sort_keys=True)

        for b, meta in bases.items():
            bx, by, r = meta["x"], meta["y"], meta["area_range"]
            out = []
            for uid8, d in pidx["players"].items():
                runs = []
                for tr in d["transform"]:
                    if ((tr["x"] - bx) ** 2 + (tr["y"] - by) ** 2) ** 0.5 > r:
                        continue
                    # the appearance run(s) overlapping this transform run
                    for ap in d["appearance"]:
                        if ap["to"] < tr["from"] or ap["from"] > tr["to"]:
                            continue
                        runs.append({
                            "from": max(ap["from"], tr["from"]), "to": min(ap["to"], tr["to"]),
                            "parts": parts.get(lookkey(ap), []),
                            "x": tr["x"], "y": tr["y"], "z": tr["z"],
                            "qx": tr["qx"], "qy": tr["qy"], "qz": tr["qz"], "qw": tr["qw"],
                        })
                if runs:
                    out.append({"uid": uid8, "runs": sorted(runs, key=lambda r_: r_["from"])})
            json.dump({"base": b, "players": out}, open(f"{OUT}/players_{b}.json", "w"))
            print(f"  players_{b}.json  {len(out)} players, "
                  f"{sum(len(p['runs']) for p in out)} in-base runs   ({NAMES.get(b,b)})")

    # ---- builder attribution -------------------------------------------
    for f in sorted(glob.glob(f"{OUT}/union_*.json")):
        b = os.path.basename(f)[6:14]
        d = json.load(open(f))
        builders, counts = {}, defaultdict(int)
        for m in d["map_objects"]:
            rd = m["Model"]["value"]["RawData"]["value"]
            uid = str(rd.get("build_player_uid"))
            iid = str(rd.get("instance_id"))
            builders[iid] = None if uid == ZERO else uid[:8]
            counts[builders[iid] or "unrecorded"] += 1
        json.dump({"base": b, "builders": builders, "counts": dict(counts)},
                  open(f"{OUT}/builders_{b}.json", "w"))
        print(f"  builders_{b}.json  {dict(counts)}   ({NAMES.get(b,b)})")


if __name__ == "__main__":
    main()
