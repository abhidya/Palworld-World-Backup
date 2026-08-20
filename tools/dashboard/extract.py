"""Extract Palworld dashboard data from a world backup into data.js.

Usage:  py -3 extract.py [path-to-world-dir]
Default world dir: <repo-root>/world/current (repo root = two levels up from
this script, i.e. tools/dashboard/../..)
Writes: data.js  (window.PALDATA = {...})
"""
import json, os, re, sys, glob

import ooz
from palworld_save_tools.gvas import GvasFile
from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
SUMMARY = "--summary" in sys.argv          # print compact JSON summary and exit
OUT_PATH = None
for a in sys.argv[1:]:
    if a.startswith("--out="):
        OUT_PATH = a[6:]
WORLD = ARGS[0] if ARGS else os.path.join(REPO_ROOT, "world", "current")

_print = print
def print(*a, **k):        # noqa: A001 - status goes to stderr; stdout stays clean for --summary
    _print(*a, file=sys.stderr, **k)

ZERO = "00000000-0000-0000-0000-000000000000"


def load_sav(path):
    data = open(path, "rb").read()
    ulen = int.from_bytes(data[0:4], "little")
    magic = data[8:11]
    save_type = data[11]
    if magic == b"PlM":
        raw = ooz.decompress(data[12:], ulen)
    elif magic == b"PlZ":
        import zlib
        raw = zlib.decompress(data[12:])
        if save_type == 0x32:
            raw = zlib.decompress(raw)
    else:
        raise Exception(f"unknown save magic {magic!r} in {path}")
    return GvasFile.read(raw, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES, allow_nan=True)


def V(prop, default=None):
    """Unwrap a gvas property value."""
    if prop is None:
        return default
    v = prop.get("value", default)
    if isinstance(v, dict):
        if "value" in v and set(v) <= {"type", "value"}:  # enum/byte
            return v["value"]
        if "Value" in v:  # FixedPoint64
            return V(v["Value"], default)
        if "values" in v:
            return v["values"]
    return v


def uid_str(x):
    return str(x) if x else ZERO


# ---------------------------------------------------------------- datasets
paldb = json.load(open(os.path.join(HERE, "palcalc_db.json"), encoding="utf-8-sig"))
items_raw = json.load(open(os.path.join(HERE, "items.json"), encoding="utf-8-sig"))["items"]
recipes = json.load(open(os.path.join(HERE, "recipes.json"), encoding="utf-8"))

PALS_DB = {}
for p in paldb["Pals"]:
    PALS_DB[p["InternalName"].lower()] = p

ITEMS_DB = {}
for it in items_raw:
    a = it.get("asset")
    if a and a not in ITEMS_DB:
        ITEMS_DB[a] = it

PASSIVES_DB = {}
for ps in paldb.get("PassiveSkills", []):
    PASSIVES_DB[ps["InternalName"]] = {
        "name": (ps.get("LocalizedNames") or {}).get("en") or ps["InternalName"],
        "rank": ps.get("Rank", 0),
    }

chars_raw = json.load(open(os.path.join(HERE, "characters.json"), encoding="utf-8-sig"))
CHAR_DB = {p["asset"].lower(): p for p in chars_raw["pals"]}
skills_raw = json.load(open(os.path.join(HERE, "skills.json"), encoding="utf-8-sig"))
SKILLS_DB = {s["asset"]: s for s in skills_raw["skills"]}
PASSIVES_DD = {p["asset"]: p for p in skills_raw["passives"]}
ELEMENTS = {e["name"]: {"display": e["display"], "color": e["color"], "index": e["index"]}
            for e in skills_raw["elements"]}
for a, p in PASSIVES_DD.items():
    entry = PASSIVES_DB.setdefault(a, {"name": p["name"], "rank": p.get("rank", 0)})
    entry["desc"] = p.get("description")
    entry["rank"] = p.get("rank", entry.get("rank", 0))

def clean_waza(w):
    return str(w).replace("EPalWazaID::", "")

world_raw = json.load(open(os.path.join(HERE, "world.json"), encoding="utf-8-sig"))
STRUCT_DB = {}
for st_ in world_raw.get("structures", []):
    a = st_.get("asset")
    if a and a not in STRUCT_DB:
        STRUCT_DB[a] = st_


def pal_lookup(char_id):
    """Resolve save CharacterID -> (clean internal id, is_boss/alpha, db entry)."""
    cid = char_id or ""
    boss = False
    base = cid
    for pre in ("BOSS_", "Boss_", "GYM_", "RAID_", "PREDATOR_", "SUMMON_"):
        if base.startswith(pre):
            boss = True
            base = base[len(pre):]
            break
    db = PALS_DB.get(base.lower())
    return base, boss, db


def pal_name(char_id):
    base, boss, db = pal_lookup(char_id)
    if db:
        n = db["LocalizedNames"].get("en") or db["Name"]
        return ("Alpha " + n) if boss else n
    return char_id


def item_name(static_id):
    it = ITEMS_DB.get(static_id)
    if it:
        return it["name"]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", static_id).replace("_", " ")


# ---------------------------------------------------------------- parse level
print("parsing Level.sav ...")
level = load_sav(os.path.join(WORLD, "Level.sav"))
wsd = level.properties["worldSaveData"]["value"]

# ---- players / pals -------------------------------------------------------
players = {}
pals = []
npcs = []
char_entries = wsd["CharacterSaveParameterMap"]["value"]
for e in char_entries:
    uid = uid_str(e["key"]["PlayerUId"]["value"])
    iid = uid_str(e["key"]["InstanceId"]["value"])
    sp = e["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"]
    if V(sp.get("IsPlayer")):
        players[uid] = {
            "uid": uid,
            "iid": iid,
            "name": V(sp.get("NickName"), "?"),
            "level": V(sp.get("Level"), 1),
            "exp": V(sp.get("Exp"), 0),
            "hp": V(sp.get("Hp"), 0),
            "fullStomach": V(sp.get("FullStomach")),
            "unusedStatusPoints": V(sp.get("UnusedStatusPoint"), 0),
        }
        continue
    cid = V(sp.get("CharacterID"), "")
    base_id, boss, db = pal_lookup(cid)
    slot = sp.get("SlotId", {}).get("value") or {}
    slot_cid = uid_str(V((slot.get("ContainerId") or {}).get("value", {}).get("ID"))) if slot else ZERO
    slot_idx = V(slot.get("SlotIndex"), -1) if slot else -1
    gender = V(sp.get("Gender"), "")
    gender = "F" if "Female" in str(gender) else ("M" if "Male" in str(gender) else "?")
    rec = {
        "iid": iid,
        "id": base_id,
        "name": pal_name(cid),
        "nick": V(sp.get("NickName")),
        "boss": boss,
        "lucky": bool(V(sp.get("IsRarePal"))),
        "g": gender,
        "lv": V(sp.get("Level"), 1),
        "exp": V(sp.get("Exp"), 0),
        "rank": V(sp.get("Rank"), 1),          # condensation stars (1 = none)
        "iv": [V(sp.get("Talent_HP"), 0), V(sp.get("Talent_Melee"), 0),
               V(sp.get("Talent_Shot"), 0), V(sp.get("Talent_Defense"), 0)],
        "souls": [V(sp.get("Rank_HP"), 0), V(sp.get("Rank_Attack"), 0),
                  V(sp.get("Rank_Defence"), 0), V(sp.get("Rank_CraftSpeed"), 0)],
        "passives": V(sp.get("PassiveSkillList"), []) or [],
        "san": V(sp.get("SanityValue")),
        "hunger": V(sp.get("FullStomach")),
        "hp": V(sp.get("Hp"), 0),
        "friend": V(sp.get("FriendshipPoint"), 0),
        "owner": uid_str(V(sp.get("OwnerPlayerUId"))),
        "cont": slot_cid,
        "slot": slot_idx,
        "sick": V(sp.get("WorkerSick")),
        "hungerType": V(sp.get("HungerType")),
        "waza": [clean_waza(w) for w in (V(sp.get("EquipWaza"), []) or [])],
        "wazaAll": [clean_waza(w) for w in (V(sp.get("MasteredWaza"), []) or [])],
    }
    if db is None and not cid.lower().startswith("sheepball"):
        # humans / NPCs (merchants, visitors) have no pal db entry
        if V(sp.get("UniqueNPCID")) or "NPC" in cid or db is None:
            npcs.append({"iid": iid, "id": cid, "name": cid, "cont": slot_cid})
            continue
    ws = None
    if db:
        ws = {k: v for k, v in db["WorkSuitability"].items() if v}
    rec["work"] = ws
    pals.append(rec)

print(f"  players={len(players)} pals={len(pals)} npcs={len(npcs)}")

# ---- guilds ---------------------------------------------------------------
guilds = []
for e in wsd["GroupSaveDataMap"]["value"]:
    rd = e["value"]["RawData"]["value"]
    if rd.get("group_type") != "EPalGroupType::Guild":
        continue
    members = []
    for pl in rd.get("players", []):
        pi = pl.get("player_info", {})
        members.append({
            "uid": uid_str(pl.get("player_uid")),
            "name": pi.get("player_name", "?"),
            "lastOnline": pi.get("last_online_real_time"),
        })
    guilds.append({
        "id": uid_str(rd.get("group_id")),
        "name": rd.get("guild_name", "Unnamed Guild"),
        "campLevel": rd.get("base_camp_level"),
        "members": members,
        "baseIds": [uid_str(b) for b in rd.get("base_ids", [])],
        "handles": len(rd.get("individual_character_handle_ids", [])),
    })

# ---- bases ----------------------------------------------------------------
bases = []
base_by_id = {}
for e in wsd["BaseCampSaveData"]["value"]:
    bid = uid_str(e["key"])
    val = e["value"]
    rd = val["RawData"]["value"]
    wd = val["WorkerDirector"]["value"]["RawData"]["value"]
    tr = wd.get("spawn_transform", {}).get("translation", {})
    b = {
        "id": bid,
        "name": rd.get("name") or None,
        "pos": [round(tr.get("x", 0)), round(tr.get("y", 0)), round(tr.get("z", 0))],
        "workerContainer": uid_str(wd.get("container_id")),
        "guildId": uid_str(rd.get("group_id_belong_to")),
        "objects": {},
        "chests": [],
    }
    bases.append(b)
    base_by_id[bid] = b

# label bases by index for display
for i, b in enumerate(sorted(bases, key=lambda x: x["pos"][0])):
    b["label"] = f"Base {i+1}"

# ---- item containers ------------------------------------------------------
cont_slots = {}
for e in wsd["ItemContainerSaveData"]["value"]:
    cid = uid_str(e["key"]["ID"]["value"])
    slots = []
    for s in (e["value"]["Slots"]["value"]["values"] or []):
        rdv = s.get("RawData", {}).get("value")
        if not rdv:
            continue
        sid = rdv.get("item", {}).get("static_id")
        ct = rdv.get("count", 0)
        if sid and sid != "None" and ct:
            dyn = rdv.get("item", {}).get("dynamic_id", {}).get("local_id_in_created_world")
            slots.append({"i": rdv.get("slot_index", 0), "id": sid, "ct": ct,
                          "dyn": uid_str(dyn) if uid_str(dyn) != ZERO else None})
    cont_slots[cid] = slots

# ---- dynamic items (egg contents, gear durability) ------------------------
dyn_items = {}   # local_id -> egg payload
dyn_gear = {}    # local_id -> {dur, bullets, passives}
for e in wsd.get("DynamicItemSaveData", {}).get("value", {}).get("values", []):
    try:
        rd = e.get("RawData", {}).get("value", {})
        lid = uid_str(rd.get("id", {}).get("local_id_in_created_world"))
        t = rd.get("type")
        if t == "egg":
            cid = rd.get("character_id", "")
            obj = rd.get("object", {}) or {}
            sp = (obj.get("SaveParameter", {}) or {}).get("value")
            info = {"species": pal_name(cid), "speciesId": pal_lookup(cid)[0]}
            if sp:
                info.update({
                    "g": "F" if "Female" in str(V(sp.get("Gender"), "")) else "M",
                    "passives": V(sp.get("PassiveSkillList"), []) or [],
                    "iv": [V(sp.get("Talent_HP"), 0), V(sp.get("Talent_Melee"), 0),
                           V(sp.get("Talent_Shot"), 0), V(sp.get("Talent_Defense"), 0)],
                    "waza": [clean_waza(w) for w in (V(sp.get("EquipWaza"), []) or [])],
                    "wazaAll": [clean_waza(w) for w in (V(sp.get("MasteredWaza"), []) or [])],
                })
            else:
                info["rolledAtHatch"] = True
            if cid:
                dyn_items[lid] = info
        elif t in ("weapon", "armor"):
            dyn_gear[lid] = {
                "dur": rd.get("durability"),
                "bullets": rd.get("remaining_bullets"),
                "passives": rd.get("passive_skill_list") or [],
            }
    except Exception:
        pass
print(f"  dynamic payloads: eggs={len(dyn_items)} gear={len(dyn_gear)}")

# ---- map objects: chests, incubators, feed boxes, per-base census --------
incubators = []
chest_types = {}
mo_values = wsd["MapObjectSaveData"]["value"]["values"]
for m in mo_values:
    oid = m["MapObjectId"]["value"]
    model = m["Model"]["value"]["RawData"]["value"]
    base_id = uid_str(model.get("base_camp_id_belong_to"))
    in_base = base_id in base_by_id
    if in_base:
        base_by_id[base_id]["objects"][oid] = base_by_id[base_id]["objects"].get(oid, 0) + 1
    cm = m.get("ConcreteModel", {}).get("value", {})
    modules = cm.get("ModuleMap", {}).get("value", []) or []
    target_container = None
    for mod in modules:
        if "ItemContainer" in str(mod.get("key")):
            target_container = uid_str(mod["value"]["RawData"]["value"].get("target_container_id"))
    if oid == "HatchingPalEgg":
        cmrd = cm.get("RawData", {}).get("value", {})
        egg_item = None
        if target_container and cont_slots.get(target_container):
            s0 = cont_slots[target_container][0]
            egg_item = {"id": s0["id"], "name": item_name(s0["id"]), "dyn": s0.get("dyn")}
            if s0.get("dyn") and s0["dyn"] in dyn_items:
                egg_item["contents"] = dyn_items[s0["dyn"]]
        unhatched = cmrd.get("hatched_character_guid", ZERO)
        incubators.append({
            "baseId": base_id if in_base else None,
            "egg": egg_item,
            "hatchedReady": uid_str(unhatched) != ZERO or bool(cmrd.get("hatched_character_save_parameter")),
            "pos": [round(model.get("initital_transform_cache", {}).get("translation", {}).get("x", 0)),
                    round(model.get("initital_transform_cache", {}).get("translation", {}).get("y", 0))],
        })
        continue
    if target_container and in_base and cont_slots.get(target_container) is not None:
        tr = model.get("initital_transform_cache", {}).get("translation", {})
        base_by_id[base_id]["chests"].append({
            "type": oid,
            "cont": target_container,
            "pos": [round(tr.get("x", 0)), round(tr.get("y", 0))],
        })
        chest_types[target_container] = oid

# ---- player saves ---------------------------------------------------------
print("parsing player saves ...")
player_files = glob.glob(os.path.join(WORLD, "Players", "*.sav"))
for pf in player_files:
    try:
        g = load_sav(pf)
    except Exception as ex:
        print("  ! failed", pf, ex)
        continue
    if "SaveData" not in g.properties:  # e.g. *_dps.sav pal-storage sidecars
        print("  ! skipping (no SaveData):", os.path.basename(pf))
        continue
    sd = g.properties["SaveData"]["value"]
    uid = uid_str(V(sd.get("PlayerUId")))
    p = players.setdefault(uid, {"uid": uid, "name": os.path.basename(pf)[:8]})
    inv = sd.get("InventoryInfo", {}).get("value", {})
    def cids(key, src):
        s = src.get(key, {}).get("value", {})
        return uid_str(V(s.get("ID")))
    p["containers"] = {
        "inventory": cids("CommonContainerId", inv),
        "essential": cids("EssentialContainerId", inv),
        "weapons": cids("WeaponLoadOutContainerId", inv),
        "armor": cids("PlayerEquipArmorContainerId", inv),
        "food": cids("FoodEquipContainerId", inv),
        "party": cids("OtomoCharacterContainerId", sd),
        "palbox": cids("PalStorageContainerId", sd),
    }
    p["techPoints"] = V(sd.get("TechnologyPoint"), 0)
    p["bossTechPoints"] = V(sd.get("bossTechnologyPoint"), 0)
    p["platform"] = str(V(sd.get("PlayerPlatform"), "")).split("::")[-1]
    lod = sd.get("LastOnlineDateTime")
    p["lastOnlineTicks"] = V(lod)
    rec = sd.get("RecordData", {}).get("value", {})
    def mapcount(key):
        m = rec.get(key, {}).get("value", [])
        try:
            return sum(x["value"]["value"] if isinstance(x.get("value"), dict) else x.get("value", 0) for x in m)
        except Exception:
            return None
    def maplen(key):
        return len(rec.get(key, {}).get("value", []) or [])
    p["records"] = {
        "captures": mapcount("PalCaptureCount"),
        "paldeck": maplen("PaldeckUnlockFlag"),
        "towerBosses": maplen("TowerBossDefeatFlag"),
        "bossKills": maplen("NormalBossDefeatFlag"),
        "predatorKills": V(rec.get("PredatorDefeatCount"), 0),
        "dungeons": (V(rec.get("NormalDungeonClearCount"), 0) or 0) + (V(rec.get("FixedDungeonClearCount"), 0) or 0),
        "oilrig": V(rec.get("OilrigClearCount"), 0),
        "campsConquered": V(rec.get("CampConqueredCount"), 0),
        "fishing": mapcount("FishingCountMap"),
        "crafts": mapcount("CraftItemCount"),
        "fastTravels": maplen("FastTravelPointUnlockFlag"),
        "notes": maplen("NoteObtainForInstanceFlag"),
        "relics": V(rec.get("RelicPossessNum"), 0),
        "tribeCaptures": V(rec.get("TribeCaptureCount"), 0),
    }
    def mapkeys(key):
        return [str(x.get("key")) for x in (rec.get(key, {}).get("value", []) or [])
                if (x.get("value") is True or V(x.get("value")) in (True, 1) or not isinstance(x.get("value"), dict))]
    p["towers"] = [str(x.get("key")) for x in (rec.get("TowerBossDefeatFlag", {}).get("value", []) or [])]
    p["paldeck"] = mapkeys("PaldeckUnlockFlag")
    p["techUnlocked"] = len(V(sd.get("UnlockedRecipeTechnologyNames"), []) or [])

# ---- container "where" map ------------------------------------------------
cont_where = {}
for b in bases:
    for ch in b["chests"]:
        cont_where[ch["cont"]] = {"kind": "chest", "base": b["label"], "type": ch["type"]}
for p in players.values():
    for kind, cid in (p.get("containers") or {}).items():
        if kind in ("party", "palbox"):
            continue
        if cid and cid != ZERO:
            cont_where[cid] = {"kind": kind, "player": p.get("name", "?")}

# guild chest (bControllableOthers / GroupId set)
for e in wsd["ItemContainerSaveData"]["value"]:
    cid = uid_str(e["key"]["ID"]["value"])
    gi = e["value"].get("BelongInfo", {}).get("value", {})
    gid = uid_str(V(gi.get("GroupId")))
    if gid != ZERO and cid not in cont_where:
        cont_where[cid] = {"kind": "guild-chest"}

# only ship containers we can place (plus any with loot that belongs to players)
containers_out = {}
for cid, where in cont_where.items():
    slots = cont_slots.get(cid)
    if slots is None:
        continue
    containers_out[cid] = {"where": where, "slots": slots}

# ---- player gear -----------------------------------------------------------
for p in players.values():
    gear = []
    for kind in ("weapons", "armor", "food"):
        cid = (p.get("containers") or {}).get(kind)
        for s in cont_slots.get(cid, []) or []:
            g = {"kind": kind, "id": s["id"], "name": item_name(s["id"]), "ct": s["ct"]}
            it = ITEMS_DB.get(s["id"]) or {}
            g["rarity"] = it.get("rarity")
            g["maxDur"] = it.get("durability")
            dg = dyn_gear.get(s.get("dyn") or "")
            if dg:
                g["dur"] = dg["dur"]
                if dg.get("passives"):
                    g["passives"] = dg["passives"]
            gear.append(g)
    p["gear"] = gear

# ---- eggs stored anywhere --------------------------------------------------
stored_eggs = []
for cid, c in containers_out.items():
    for s in c["slots"]:
        if s["id"].startswith("PalEgg"):
            info = dyn_items.get(s.get("dyn") or "", {})
            stored_eggs.append({
                "egg": s["id"], "eggName": item_name(s["id"]), "ct": s["ct"],
                "where": c["where"], **info,
            })

# ---- pal->base assignment --------------------------------------------------
base_conts = {b["workerContainer"]: b["label"] for b in bases}
party_conts = {}
palbox_conts = {}
for p in players.values():
    c = p.get("containers") or {}
    if c.get("party"): party_conts[c["party"]] = p.get("name", "?")
    if c.get("palbox"): palbox_conts[c["palbox"]] = p.get("name", "?")
for pal in pals:
    c = pal["cont"]
    if c in base_conts:
        pal["loc"] = base_conts[c]
    elif c in party_conts:
        pal["loc"] = "Party (" + party_conts[c] + ")"
    elif c in palbox_conts:
        pal["loc"] = "Palbox (" + palbox_conts[c] + ")"
    else:
        pal["loc"] = "Wild/Other"

# viewing-cage or dropped pals w/o container stay "Wild/Other" — drop wild ones with no owner
pals = [p for p in pals if not (p["loc"] == "Wild/Other" and p["owner"] == ZERO and not p["nick"])]

# ---- world stats -----------------------------------------------------------
gt = wsd.get("GameTimeSaveData", {}).get("value", {})
game_ticks = V(gt.get("GameDateTimeTicks"), 0)
real_ticks = V(gt.get("RealDateTimeTicks"), 0)

total_money = sum(s["ct"] for c in containers_out.values() for s in c["slots"] if s["id"] == "Money")

from collections import Counter
world_objs = Counter(m["MapObjectId"]["value"] for m in mo_values)
built_at_bases = Counter()
for b in bases:
    built_at_bases.update(b["objects"])

# ---------------------------------------------------------------- summary mode
if SUMMARY:
    workers = [p for p in pals if p["cont"] in base_conts]
    sans = [(p["san"] if p["san"] is not None else 100.0) for p in workers]
    key_items = {}
    for c in containers_out.values():
        for s in c["slots"]:
            key_items[s["id"]] = key_items.get(s["id"], 0) + s["ct"]
    summary = {
        "gameTicks": game_ticks,
        "realTicks": real_ticks,
        "money": total_money,
        "palCount": len(pals),
        "structures": sum(built_at_bases.values()),
        "guildLevel": max((g["campLevel"] or 0) for g in guilds) if guilds else 0,
        "eggsReady": sum(1 for i in incubators if i["hatchedReady"]),
        "eggsIncubating": sum(1 for i in incubators if i["egg"] and not i["hatchedReady"]),
        "avgSanity": round(sum(sans) / len(sans), 2) if sans else None,
        "workerCount": len(workers),
        "players": {
            p.get("name", p["uid"][:8]): {
                "level": p.get("level"), "exp": p.get("exp"),
                "captures": (p.get("records") or {}).get("captures"),
                "paldeck": (p.get("records") or {}).get("paldeck"),
                "crafts": (p.get("records") or {}).get("crafts"),
                "fishing": (p.get("records") or {}).get("fishing"),
                "dungeons": (p.get("records") or {}).get("dungeons"),
                "towers": len(p.get("towers") or []),
                "relics": (p.get("records") or {}).get("relics"),
                "bossKills": (p.get("records") or {}).get("bossKills"),
                "fastTravels": (p.get("records") or {}).get("fastTravels"),
            } for p in players.values()
        },
        "sickWorkers": sum(1 for p in workers if p["sick"]),
        "items": key_items,
    }
    _print(json.dumps(summary, ensure_ascii=False))
    sys.exit(0)

meta = {}
snap_path = os.path.join(os.path.dirname(WORLD), "..", "metadata", "snapshot.json")
snap_path2 = os.path.join(WORLD, "..", "..", "metadata", "snapshot.json")
for sp_ in (snap_path, snap_path2):
    if os.path.exists(sp_):
        snap = json.load(open(sp_, encoding="utf-8"))
        meta = {"serverName": snap.get("server_name"), "snapshotTime": snap.get("snapshot_timestamp"),
                "serverVersion": snap.get("server_version"), "worldId": snap.get("world_id")}
        break

# ---------------------------------------------------------------- breeding
print("building breeding table ...")
bre = json.load(open(os.path.join(HERE, "palcalc_breeding.json"), encoding="utf-8-sig"))["Breeding"]
pal_order = sorted({p["InternalName"] for p in paldb["Pals"]})
pidx = {n: i for i, n in enumerate(pal_order)}
pairs = {}
gender_pairs = []
for r in bre:
    p1, p2, ch = r["Parent1InternalName"], r["Parent2InternalName"], r["ChildInternalName"]
    if p1 not in pidx or p2 not in pidx or ch not in pidx:
        continue
    if r["Parent1Gender"] == "WILDCARD" and r["Parent2Gender"] == "WILDCARD":
        i, j = sorted((pidx[p1], pidx[p2]))
        pairs[f"{i},{j}"] = pidx[ch]
    else:
        gender_pairs.append([pidx[p1], r["Parent1Gender"][0], pidx[p2], r["Parent2Gender"][0], pidx[ch]])

# compact: child index list keyed by upper-triangle position
n = len(pal_order)
tri = [-1] * (n * (n + 1) // 2)
def tri_idx(i, j):
    # i<=j
    return i * n - i * (i - 1) // 2 + (j - i)
for k, ch in pairs.items():
    i, j = map(int, k.split(","))
    tri[tri_idx(i, j)] = ch
missing = tri.count(-1)
print(f"  pals={n} pairs={len(pairs)} gender-specific={len(gender_pairs)} missing={missing}")

breeding_out = {
    "order": pal_order,
    "tri": tri,
    "genderPairs": gender_pairs,
}

# pal display db (trimmed for embed)
pal_meta = {}
for nm in pal_order:
    p = PALS_DB[nm.lower()]
    cd = CHAR_DB.get(nm.lower()) or {}
    stats = cd.get("stats") or {}
    els = [e for e in [stats.get("element_type1"), stats.get("element_type2")] if e and e != "None"]
    pal_meta[nm] = {
        "name": p["LocalizedNames"].get("en") or p["Name"],
        "dex": p["Id"]["PalDexNo"],
        "variant": p["Id"]["IsVariant"],
        "power": p["BreedingPower"],
        "night": p.get("Nocturnal", False),
        "food": p.get("FoodAmount"),
        "stomach": p.get("MaxFullStomach"),
        "work": {k: v for k, v in p["WorkSuitability"].items() if v},
        "rarity": p.get("Rarity"),
        "el": els,
        "icon": (cd.get("icon") or "").split("/")[-1] or None,
        "cs": stats.get("craft_speed"),
    }

# active skills referenced by owned pals or eggs
waza_refs = set()
for p in pals:
    waza_refs.update(p.get("waza", []));  waza_refs.update(p.get("wazaAll", []))
for e in dyn_items.values():
    waza_refs.update(e.get("waza", []));  waza_refs.update(e.get("wazaAll", []))
skill_meta = {}
for w in waza_refs:
    s = SKILLS_DB.get(w) or SKILLS_DB.get(w.replace("Unique_", "")) or None
    if s:
        skill_meta[w] = {"name": s["name"], "el": s.get("element"), "pw": s.get("display_power") or s.get("power"), "ct": s.get("cooldown")}
    else:
        skill_meta[w] = {"name": re.sub(r"(?<=[a-z])(?=[A-Z])", " ", w).replace("_", " "), "el": None, "pw": None, "ct": None}

# item display db: only ids present in world + all recipe result names
present_ids = {s["id"] for c in containers_out.values() for s in c["slots"]}
item_meta = {}
for sid in present_ids:
    it = ITEMS_DB.get(sid)
    item_meta[sid] = {
        "name": item_name(sid),
        "type": (it or {}).get("type_a_display"),
        "sub": (it or {}).get("type_b_display"),
        "weight": (it or {}).get("weight"),
        "rarity": (it or {}).get("rarity"),
        "icon": ((it or {}).get("icon") or "").split("/")[-1] or None,
    }
# name -> id map for joining recipes (recipes keyed by display name)
name_to_id = {}
for sid, it in ITEMS_DB.items():
    name_to_id.setdefault(it["name"], sid)

STATION_RE = re.compile(r"(?:crafted|produced|cooked|made)\s+at\s+(?:the\s+)?([A-Z][A-Za-z0-9' -]+?)(?:\.|,|$)", re.M)
recipes_out = {}
for rname, r in recipes.items():
    rid = name_to_id.get(rname)
    station = None
    desc = (ITEMS_DB.get(rid) or {}).get("description") or ""
    ms = STATION_RE.search(desc)
    if ms:
        station = ms.group(1).strip()
    recipes_out[rname] = {
        "id": rid,
        "station": station,
        "type": r.get("t"),
        "variants": [{"mats": [{"name": m.rsplit("*", 1)[0], "ct": int(m.rsplit("*", 1)[1]),
                                "id": name_to_id.get(m.rsplit("*", 1)[0])}
                               for m in v["m"]],
                      "work": v.get("w"), "out": v.get("n", 1)} for v in r["r"]],
    }

# ---------------------------------------------------------------- write
out = {
    "meta": meta,
    "gameTicks": game_ticks,
    "realTicks": real_ticks,
    "players": list(players.values()),
    "guilds": guilds,
    "bases": [{k: v for k, v in b.items() if k != "chests"} | {"chests": b["chests"]} for b in bases],
    "pals": pals,
    "npcs": npcs,
    "containers": containers_out,
    "incubators": incubators,
    "storedEggs": stored_eggs,
    "totalMoney": total_money,
    "worldObjects": dict(world_objs.most_common(60)),
    "builtAtBases": dict(built_at_bases.most_common()),
    "palMeta": pal_meta,
    "itemMeta": item_meta,
    "passives": PASSIVES_DB,
    "skills": skill_meta,
    "elements": ELEMENTS,
    "structMeta": {a: {"name": (STRUCT_DB.get(a) or {}).get("name") or a,
                       "icon": ((STRUCT_DB.get(a) or {}).get("icon") or "").split("/")[-1] or None}
                   for b in bases for a in b["objects"]},
    "recipes": recipes_out,
    "breeding": breeding_out,
}

path = os.path.join(HERE, "data.js")
with open(path, "w", encoding="utf-8") as f:
    f.write("window.PALDATA = ")
    json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    f.write(";\n")
print("wrote", path, round(os.path.getsize(path) / 1e6, 2), "MB")
