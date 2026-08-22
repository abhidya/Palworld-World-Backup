"""Resolve the ids that appear in PlayerCharacterMakeData.OverrideEquipmentInfo
(the mirror appearance swap) to the meshes the game would show for them.

WHY A SEPARATE FILE. equipment_<base>.json resolves a mesh for every item the
player had IN A CONTAINER, because that is what its scan walked. The mirror
override names an item the player need not be carrying at all — Kat's override
is CopperArmorCold while she is wearing SFArmor — so those ids have no entry
there. This resolves them through the SAME shipped table the equipment pass
used, DT_CharacterCreationMeshPresetTable_Equipments[id].SkeletalMeshMap[bodyType],
with Mappings.usmap applied (eq_dt_item.json is that table, dumped verbatim).

An id with NO row in the table resolves to NOTHING and is recorded as such —
`Naked_Head` is exactly that case, and it is not a failure: it is the game's own
way of saying "show no headgear", which is why Kat wears no helmet on screen
despite having PlasticHelmet equipped. Nothing is ever substituted.
"""
import json, os, glob

SP = os.path.dirname(os.path.abspath(__file__))
E = json.load(open(f"{SP}/eq_dt_item.json"))["DT_CharacterCreationMeshPresetTable_Equipments"][0]["Rows"]
PUB = f"{SP}/mappal/public"
POSED = "/equipment_meshes_posed"

wanted = {}
for f in sorted(glob.glob(f"{PUB}/union/equipment_*.json")):
    d = json.load(open(f))
    if "players" not in d:
        continue
    for p in d["players"]:
        for r in p["runs"]:
            ao = r.get("appearanceOverride") or {}
            for fld in ("body", "head"):
                v = ao.get(fld)
                if v and v != "None":
                    wanted.setdefault(v, set()).add(r["bodyType"] or "TypeA")

out = {}
for item, bts in sorted(wanted.items()):
    row = E.get(item)
    rec = {"itemId": item, "meshes": {}}
    if row is None:
        rec["unresolved"] = ("no row in DT_CharacterCreationMeshPresetTable_Equipments - "
                             "the game shows NOTHING for this id; never substituted")
        out[item] = rec
        continue
    for kv in row.get("SkeletalMeshMap") or []:
        ap = (kv.get("Value") or {}).get("AssetPathName")
        if not ap or ap == "None":
            continue
        mesh = ap.split(".")[0].rsplit("/", 1)[-1]
        rec["meshes"][kv["Key"]] = {
            "mesh": mesh,
            "url": f"{POSED}/{mesh}.glb",
            "haveGlb": os.path.exists(f"{PUB}{POSED}/{mesh}.glb"),
            "assetPath": ap,
        }
    rec["bIsFullBodyEquipment"] = row.get("bIsFullBodyEquipment")
    rec["overrideBodyType"] = row.get("OverrideBodyType")
    out[item] = rec

doc = {"note": __doc__.strip(),
       "source": "DT_CharacterCreationMeshPresetTable_Equipments (eq_dt_item.json), Mappings.usmap applied",
       "items": out}
json.dump(doc, open(f"{PUB}/union/equipment_override_meshes.json", "w"), indent=1)
print(f"wrote equipment_override_meshes.json: {len(out)} override ids")
for k, v in out.items():
    print(f"  {k:20s} {v.get('unresolved') or {b: m['mesh'] + ('' if m['haveGlb'] else ' [NO GLB]') for b, m in v['meshes'].items()}}")
