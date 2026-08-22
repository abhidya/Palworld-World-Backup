"""Emit union/avatars.json: for each player UID, the real SK_Player_* part list
their save recorded, as time RUNS covering the whole history.

This is the APPEARANCE half of the builder-avatar feature. The other half —
which player is responsible for a given piece — is `build_player_uid` on the map
object itself (union/builders_<base>.json).

Everything here is recorded data:
  who    the UID keys are the Players/<UID>.sav files that exist in the history.
  when   the run boundaries are the snapshots at which that player's
         PlayerCharacterMakeData actually CHANGED (player_index.py compares the
         git-LFS oid of the .sav blob, so "unchanged" means the bytes were
         literally identical, not that we guessed).
  look   the part list resolved from BodyMeshName / HeadMeshName / HairMeshName
         and the equipment/skin ids through the GAME'S OWN character-creation
         data tables (resolve_players.py -> player_parts.json). No name-to-mesh
         mapping is invented here.

A look whose parts could not be resolved to real extracted GLBs is dropped
rather than substituted, so a player with no resolvable appearance simply has no
run at that time and no avatar is drawn.
"""
import json
import os

SP = os.path.dirname(os.path.abspath(__file__))
IDX = json.load(open(f"{SP}/player_index.json"))
PARTS = json.load(open(f"{SP}/player_parts.json"))
PUB = f"{SP}/mappal/public"

# player_parts.json is keyed by the JSON of the look dict with sorted keys.
LOOK_KEYS = ("body", "eqBody", "eqHead", "hair", "head", "ovBody", "ovHead")


def look_key(a):
    return json.dumps({k: a.get(k) for k in LOOK_KEYS}, sort_keys=True)


out = {}
report = {}
for uid, p in IDX["players"].items():
    runs = []
    for a in p.get("appearance", []):
        key = look_key(a)
        parts = PARTS.get(key)
        if not parts:
            report.setdefault(uid, []).append({"from": a["from"], "unresolved": key})
            continue
        # Only keep parts whose GLB was actually extracted and deployed.
        good = [q for q in parts if os.path.exists(PUB + q["url"])]
        if len(good) != len(parts):
            report.setdefault(uid, []).append(
                {"from": a["from"], "missingGlb": [q["url"] for q in parts if q not in good]}
            )
        if not good:
            continue
        runs.append({"from": a["from"], "to": a["to"], "parts": good})
    if runs:
        out[uid] = runs

json.dump({"players": out}, open(f"{PUB}/union/avatars.json", "w"), indent=1)
print(f"wrote union/avatars.json for {len(out)} players")
for uid, runs in out.items():
    span = f"{runs[0]['from']}..{runs[-1]['to']}"
    print(f"  {uid}: {len(runs)} appearance run(s) {span} parts={[len(r['parts']) for r in runs]}")
if report:
    print("NOT resolved (left undrawn, never substituted):")
    print(json.dumps(report, indent=1)[:1500])
