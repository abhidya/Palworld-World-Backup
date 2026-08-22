"""Per-base SURROUNDINGS from the client pak's World Partition cells.

Everything here is the game's own level data, read out of
PL_MainWorld5/_Generated_/<Grid>_L0_X<x>_Y<y>_DL<layer>.umap:
  - which meshes are placed, and each one's RelativeLocation / RelativeRotation /
    RelativeScale3D, straight off the cooked StaticMeshComponent.
  - The cell grid is 25600 cm; cellX = floor(ue.x/25600), cellY = floor(ue.y/25600).
    That is not inferred from the coordinates - the cooked InstancedFoliageActor
    in each cell is literally named InstancedFoliageActor_25600_<x>_<y>_-1, and
    the placed meshes' own world coordinates fall inside the implied box.
Nothing is placed, scaled, rotated or invented here.

TWO KINDS OF GROUND, because Palworld uses two, and BOTH are everywhere.
  The visible detail is placed ground/cliff STATIC MESHES
  (SM_pal_b00_cliff_ground_*, *_TOP_GrassA, Flat_horizontal_rock_TOP, ...), which
  arrive through the ordinary StaticMeshComponent path below. UNDER them is a
  real UE Landscape.

  CORRECTION, and it was load-bearing: this file used to claim "a full
  --landscape sweep of every MainGrid umap in the pak finds only ONE landscape
  actor group in the whole world (3 LandscapeStreamingProxy tiles, GUID
  F1S2CBISW7E8MH75TEF24TY0P), under Glass Tower". Both halves were wrong.
  That sweep ran over rawassets (583 umaps), not the pak's 7,085 MainGrid_L0
  cells; and, more importantly, it only ever looked at MainGrid. PL_MainWorld5
  ships MANY runtime grids - MainGrid, Foliage, FarMountain, CloseRange, oilrig
  and ~40 HLOD0_*/HLOD1_* grids - and an earlier census missed all but MainGrid
  because it matched only names containing the word "Grid".
  FarMountain_L0 alone holds 939 more LandscapeStreamingProxy actors, in several
  distinct landscape groups, and they cover all four bases. They are the ground
  the placed rock meshes sit on: see the LANDSCAPE section for the measurement
  that establishes it at Stone Works.

LANDSCAPE TILES fold into the SAME prop list, so the renderer needs no change.
  LandscapeMeshDto returns vertices in QUAD units, and the proxy's own cooked
  root component carries RelativeLocation / RelativeRotation (yaw 68.098 deg,
  the landscape grid is rotated) / RelativeScale3D (100). world = loc + Rz(yaw)
  * (vert * scale) is exactly the transform TerrainLayer already applies to a
  prop, so a tile is emitted as a prop with x/y/z = loc, the quaternion of its
  own FRotator, and sx=sy=sz=100. Clipping keeps whole triangles whose vertices
  fall inside the radius and rewrites them in unchanged QUAD units, so the
  transform - and therefore the geometry - is untouched.

Positions are RAW UNREAL CENTIMETRES, no axis swap, no scaling: the renderer
applies its own UE->three transform.
"""
import json, math, os, re, struct, sys

SP = os.path.dirname(os.path.abspath(__file__))
BASES = {"5fed0024": (-216463, 2028, 12482), "07f13218": (-338615, 341498, 6995),
         "16fca097": (-352470, 270982, 7784), "de44d9f4": (-71, -138355, 3538)}
OUT_MESH = f"{SP}/mappal/public/terrain_meshes"
# The clipped landscape tiles and the merged ocean/river meshes are written into
# public/terrain_meshes, which every concurrent render streams from. A re-run
# that only changes the JSON (see the footprint section at the bottom) has no
# reason to rewrite a byte of them, and overwriting a file another render is
# mid-fetch on would corrupt that render. KEEP_MESHES=1 reuses whatever is
# already on disk and rebuilds only what is missing.
KEEP_MESHES = os.environ.get("KEEP_MESHES") == "1"

# THE CELL DUMP. cellactors_wide.json is a re-sweep of every MainGrid_L0 and
# Foliage_L0 cell the pak dump actually contains (659 cells, 21,504 rows). The
# older cellactors_full.json covered only a 3x3 cell block per base - 271 cells,
# 10,810 rows - so a third to four fifths of the ground that sits inside
# GROUND_R was never in the dump at all (placements within 600 m: Glass Tower
# 41 -> 194, Lost Camp 1503 -> 2078, Wooden Camp 1985 -> 2742, Stone Works
# 2436 -> 2708). It also records each component's OverrideMaterials, which is
# what identifies the fog-volume cubes.
CELLACTORS = os.environ.get("CELLACTORS", f"{SP}/cellactors_wide.json")
if not os.path.exists(CELLACTORS):
    CELLACTORS = f"{SP}/cellactors_full.json"

# THREE RADII, because the three categories cost wildly different amounts.
#   GROUND_R  the big terrain chunks (cliff/ground/*_TOP). These are what stop
#             the world visibly ending, so they run widest.
#   PROP_R    everything else placed (rocks, ruins, waterfalls, shoreline).
#             Only matters near the base and is the bulk of the triangles.
#   FOLIAGE_R per-instance trees/bushes, capped further by FOLIAGE_CAP.
# MainGrid_L1..L6 hold only spawners/triggers/sound volumes, so there is no
# cheaper MainGrid tier to fall back on and ground range is bounded by what
# full-detail meshes cost. (Palworld DOES ship HLOD0_*/HLOD1_* runtime grids -
# ~40 of them - but a sweep of every one found nothing within 600 m of any of
# the four bases; they are the far-field silhouette, not near-field terrain.)
GROUND_R = float(os.environ.get("GROUND_R", 60000))
PROP_R = float(os.environ.get("PROP_R", 30000))
FOLIAGE_R = float(os.environ.get("FOLIAGE_R", 30000))
FOLIAGE_CAP = int(os.environ.get("FOLIAGE_CAP", 3000))
# Ground-class foliage (the rock/cliff meshes the level paints with the foliage
# tool) gets its own budget. Without one it competes with flowers for
# FOLIAGE_CAP and, since flowers outnumber it ~40:1 near a base, loses - the
# nearest-first cap is exhausted inside ~60 m by ground cover and the painted
# rocks never make it out. They are terrain, so they are drawn as terrain.
GROUND_FOLIAGE_CAP = int(os.environ.get("GROUND_FOLIAGE_CAP", 2000))

# The walkable/visible terrain chunks, by the naming the level uses for them.
GROUND_KEYS = ("ground", "_top", "top_", "flat_horizontal", "rockyground",
               "hugecliff", "cliff", "terrain", "field")
# ...and by the ASSET PACK they come out of, because a chunk's mesh NAME is not
# always descriptive. The Mega3D_GrayCliffRock set is the clearest case: its
# meshes are called c01-5k .. c08-8k, which matches none of GROUND_KEYS, so 203
# five-to-eight-thousand-triangle cliff chunks around Stone Works were being
# classed as ordinary props and cut at PROP_R (300 m) - only ONE of the 206
# within 600 m was inside that radius, so the rest never even reached the
# extraction manifest. The pack directory is the reliable signal, so ground is
# decided on the cooked meshPath as well as the name.
GROUND_PATH_KEYS = ("mega3d_graycliffrock", "modular_rock", "cliffs_and_rocks_pack",
                    "field_asset_customlargecliff", "cliff_rock_stone",
                    "stage/b00/rockpack")
# /Engine/BasicShapes/Cube placements whose component OverrideMaterial is a
# LOCAL FOG VOLUME material. In game these are volumetric fog bounds, not
# surfaces; drawn as opaque geometry a single one of them is a 157 x 116 m green
# slab lying across the shot. There are exactly three in the world.
FOG_MATERIAL_KEYS = ("fogvolume",)
# VEGETATION never counts as ground, whatever else its name contains. This is
# not cosmetic: "field" is a ground key, and Palworld's hero trees are called
# Oak_White_Desktop_Field_3_001 / Tree_Alder_Hero_Field, so every one of them
# was being classed as terrain and given the 600 m ground radius instead of the
# 300 m prop radius. With the wider cell sweep that alone added 3.2 M triangles
# of distant oak at Wooden Camp. Trees are props; they are still drawn, just at
# prop range and out of the ground budget.
PLANT_KEYS = ("tree", "oak", "alder", "sycamore", "banyan", "pine", "palm",
              "birch", "maple", "cherry", "sakura", "bush", "shrub", "grass",
              "flower", "fern", "leaf", "leaves", "ivy", "amaryllis", "clover",
              "geranium", "taro", "moss", "vine", "plant", "bamboo", "reed")
# Sub-metre ground cover placed in the hundreds of thousands (674k + 466k
# instances world-wide). It does not read at the camera distances the timelapse
# uses and would be ~100x the whole rest of the scene, so it is excluded and
# reported rather than silently thinned.
MEGA_GRASS = ("grass_lowpoly_sm", "grass_small_sm")

# WATER is handled by its own section at the bottom, not by the generic prop /
# foliage paths. It has to be, for two reasons:
#   1. these meshes ship NO base-colour texture (SingleLayerWater and unlit
#      translucent shaders), so the generic path renders them with
#      TerrainLayer's flat rock-green fallback - which is what "the waterfalls
#      are green" was;
#   2. the material a placement actually uses is an OverrideMaterial on the
#      component, which the generic dump never recorded, and for the waterfalls
#      it is never the mesh's default material.
# The water section re-emits exactly these same placements with the cooked
# material attached. See water_index.json / build_water_index.py.
WATER_MESHES = ("s_watermesh", "sm_waterfall04", "sm_waterfall05",
                "sm_bendwatermesh_001", "sm_river_plane")


def is_plant(name):
    """Vegetation, by the level's own naming (PLANT_KEYS above).

    This is the ONLY class the built-footprint cull touches. Painted rocks and
    cliff chunks arrive through the same foliage path but they are terrain — the
    surface the base stands on — so removing one under a deck would punch a hole
    in the ground rather than tidy the shot.
    """
    return any(k in (name or "").lower() for k in PLANT_KEYS)


def is_ground(name, path=None):
    n = (name or "").lower()
    if any(k in n for k in PLANT_KEYS):
        return False
    if any(k in n for k in GROUND_KEYS):
        return True
    p = (path or "").lower()
    return any(k in p for k in GROUND_PATH_KEYS)


def is_fog_volume(r):
    return any(k in (r.get("ovr") or "").lower() for k in FOG_MATERIAL_KEYS)


def cell_box(cell):
    """The world XY box a World Partition cell name implies, or None."""
    m = re.search(r"_L0_X(-?\d+)_Y(-?\d+)", cell or "")
    if not m:
        return None
    return int(m.group(1)) * 25600, int(m.group(2)) * 25600


def in_own_cell(r):
    """True when a component's RelativeLocation really is a WORLD position.

    A mesh component attached to something other than the actor root carries its
    RelativeLocation in PARENT space, so treating it as world coordinates drops
    it in the wrong place (or, as here, hundreds of kilometres away near the
    origin). The cell name states the world box the actor must lie in, so a row
    far outside its own cell is a parent-space transform, not a world one.
    """
    b = cell_box(r["cell"])
    if b is None:
        return True
    x0, y0 = b
    x, y = r["loc"][0], r["loc"][1]
    return x0 - 25600 <= x <= x0 + 51200 and y0 - 25600 <= y <= y0 + 51200


def selected_props(acts, bx, by):
    """Placed StaticMeshComponents for one base, under the tiered radii."""
    out = []
    for r in acts:
        if r["cls"] != "StaticMeshComponent" or not r["meshPath"] or not any(r["loc"]):
            continue
        if r["mesh"].lower() in WATER_MESHES:
            continue
        if is_fog_volume(r) or not in_own_cell(r):
            continue
        d = math.hypot(r["loc"][0] - bx, r["loc"][1] - by)
        if d < (GROUND_R if is_ground(r["mesh"], r["meshPath"]) else PROP_R):
            out.append((d, r))
    return out


def selected_foliage(acts, ifa, bx, by):
    """Per-instance foliage transforms, nearest-first, capped.

    Instances live in PerInstanceSMData on the cell's foliage components; each
    one's transform is relative to its component, which in turn hangs off the
    InstancedFoliageActor's RootComponent0 (that root carries the cell-centre
    offset). world = instance.T + component.RelativeLocation + IFA root loc.
    """
    got = []
    for r in acts:
        if not r.get("instanceCount") or not r["mesh"]:
            continue
        if r["mesh"].lower() in MEGA_GRASS or r["mesh"].lower() in WATER_MESHES:
            continue
        ox, oy, oz = ifa.get(r["cell"], (0, 0, 0)) if r["cell"].startswith("Foliage") else (0, 0, 0)
        lx, ly, lz = r["loc"]
        for i in r["instances"]:
            x, y, z = i[0] + lx + ox, i[1] + ly + oy, i[2] + lz + oz
            d = math.hypot(x - bx, y - by)
            if d < FOLIAGE_R:
                got.append((d, r["mesh"], r["meshPath"], x, y, z, i))
    got.sort(key=lambda t: t[0])
    return got


def capped_foliage(fol):
    """Nearest-first, but ground-class instances draw on their own budget.

    Same ordering as before; the only change is that a painted rock/cliff no
    longer has to out-rank a flower to be drawn.
    """
    out, n_dec, n_gnd = [], 0, 0
    for t in fol:
        if is_ground(t[1], t[2]):
            if n_gnd >= GROUND_FOLIAGE_CAP:
                continue
            n_gnd += 1
        else:
            if n_dec >= FOLIAGE_CAP:
                continue
            n_dec += 1
        out.append(t)
    return out


# ==================================================== THE BUILT FOOTPRINT
# In game, placing a piece CLEARS the foliage it stands on. The renderer draws
# the pak's foliage and the save's buildings from two independent sources, so
# nothing removed the trees the player removed: at Wooden Camp — an elevated
# wooden deck — oaks stood ON the deck and grew up through the floor.
#
# The footprint is the ACTUAL PLACED PIECES, not a radius. Every map object in
# union_<base>.json carries its own initital_transform_cache (translation,
# rotation, scale3d) — the same record the renderer's blueprintView.extractObjects
# reads — and mappal/src/data/objects.json carries a per-type box for it. Those
# two give one oriented XY rectangle per piece, and their union is the footprint.
#
# AXIS ORDER is not a guess and it is not symmetric: coords.ts's header records
# the numeric check (3 calibration walls, 3 yaws) that establishes objects.json
# `size` = [length, thickness, height] with LENGTH along the piece's local Y and
# THICKNESS along its local X. Reading size[0] as the local-X extent would lay
# every wall's rectangle across the deck instead of along its edge.
#
# Z IS DELIBERATELY IGNORED. A tree's instance origin is its trunk base, so an XY
# test asks exactly "is this trunk standing on built ground", which is the
# question the game answers when it clears foliage — and it is what keeps the
# surrounding treeline, whose trunks are outside the footprint however far their
# canopies lean in. The alternative (a Z band per piece) would spare a trunk
# under an elevated deck and leave it growing through the floor, which is the
# defect.
FOOTPRINT_MARGIN = float(os.environ.get("FOOTPRINT_MARGIN", 100))
_REG = json.load(open(f"{SP}/mappal/src/data/objects.json"))["types"]
# A type with no measured box still occupies ground. objects.json's own renderer
# fallback for an unregistered type is a 100 cm cube (objectTypes.ts), so the
# footprint uses the same number rather than inventing a size or ignoring the
# piece. It is the smallest box in the registry, so it can only under-cull.
_UNSIZED_BOX = 100.0


def built_footprint(base):
    """One oriented XY rectangle per placed piece: (x, y, cos, sin, hx, hy, id)."""
    u = json.load(open(f"{SP}/mappal/public/union/union_{base}.json"))
    out, unsized = [], 0
    for e in u["map_objects"]:
        tid = e["MapObjectId"]["value"]
        rd = e["Model"]["value"]["RawData"]["value"]
        t = rd["initital_transform_cache"]
        p, q, s = t["translation"], t["rotation"], t["scale3d"]
        ent = _REG.get(tid)
        sz = ent["size"] if ent else None
        if not sz or sz[0] is None or sz[1] is None:
            sz = [_UNSIZED_BOX, _UNSIZED_BOX, _UNSIZED_BOX]
            unsized += 1
        # yaw = 2*atan2(z, w) — coords.ts's yawFromQuat, and every piece in these
        # saves is a pure yaw (docs/CALIBRATION.md).
        yaw = 2 * math.atan2(q["z"], q["w"])
        out.append((p["x"], p["y"], math.cos(yaw), math.sin(yaw),
                    abs(sz[1]) * 0.5 * abs(s["x"]) + FOOTPRINT_MARGIN,
                    abs(sz[0]) * 0.5 * abs(s["y"]) + FOOTPRINT_MARGIN,
                    rd["instance_id"]))
    return out, unsized


_FP_CELL = 800.0


def footprint_index(fp):
    g = {}
    for o in fp:
        r = math.hypot(o[4], o[5])
        for gx in range(int((o[0] - r) // _FP_CELL), int((o[0] + r) // _FP_CELL) + 1):
            for gy in range(int((o[1] - r) // _FP_CELL), int((o[1] + r) // _FP_CELL) + 1):
                g.setdefault((gx, gy), []).append(o)
    return g


def footprint_cullers(gidx, x, y):
    """instance_ids of every placed piece whose rectangle covers (x, y).

    ALL of them, not the first: the renderer hides the plant while ANY of these
    pieces is alive, so a partial list would grow the tree back the moment one
    of several overlapping pieces came down.
    """
    ids = []
    for ox, oy, c, s, hx, hy, iid in gidx.get((int(x // _FP_CELL), int(y // _FP_CELL)), ()):
        dx, dy = x - ox, y - oy
        if abs(dx * c + dy * s) <= hx and abs(-dx * s + dy * c) <= hy:
            ids.append(iid)
    return ids


if sys.argv[1] == "--manifest":
    # One extraction manifest covering every mesh all four bases can ask for.
    acts = json.load(open(CELLACTORS))
    ifa = {r["cell"]: r["loc"] for r in acts
           if r["name"] == "RootComponent0" and r["cell"].startswith("Foliage")}
    meshes = {}
    for b, (bx, by, bz) in BASES.items():
        for d, r in selected_props(acts, bx, by):
            meshes[r["mesh"]] = r["meshPath"].split(".")[0]
        for t in capped_foliage(selected_foliage(acts, ifa, bx, by)):
            meshes[t[1]] = t[2].split(".")[0]
    json.dump({n: {"meshPath": p} for n, p in sorted(meshes.items())},
              open(f"{SP}/terrain_manifest.json", "w"), indent=1)
    print(f"terrain_manifest.json: {len(meshes)} distinct meshes "
          f"(ground<{GROUND_R/100:.0f}m, props<{PROP_R/100:.0f}m, "
          f"foliage<{FOLIAGE_R/100:.0f}m cap {FOLIAGE_CAP})")
    raise SystemExit(0)

# Clip radius for the LANDSCAPE heightfield tiles, overridable as argv[2].
# 300 m, not GROUND_R, and the reason is cost: this landscape is 1 quad per
# metre, so a 600 m disc of it is ~2.3 M triangles per base - more than the
# entire rest of a base scene - while a 300 m disc is ~565 k. CUE4Parse's
# LandscapeMeshDto exposes only ONE LOD for these proxies, so there is no
# coarser tier of the game's own to drop to, and decimating it ourselves would
# be inventing a surface. 300 m is amply beyond frame: the timelapse camera
# orbits the base at ~50 m, so the disc's edge is six times further out than
# anything the shot can see. Raise it (with GROUND_R) for wider cameras.
RADIUS = float(sys.argv[2]) if len(sys.argv) > 2 else 30000.0
b = sys.argv[1]
bx, by, bz = BASES[b]


def rotator_to_quat(pitch, yaw, roll):
    """Unreal FRotator (degrees; Roll about X, Pitch about Y, Yaw about Z) ->
    FQuat, using Unreal's own FRotator::Quaternion() sign convention. Emitted as
    a quaternion so the renderer can hand it straight to coords.ts's already
    numerically-verified ueQuatToThree()."""
    hp, hy, hr = (math.radians(v) * 0.5 for v in (pitch, yaw, roll))
    sp, cp = math.sin(hp), math.cos(hp)
    sy, cy = math.sin(hy), math.cos(hy)
    sr, cr = math.sin(hr), math.cos(hr)
    return (cr * sp * sy - sr * cp * cy,
            -cr * sp * cy - sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
            cr * cp * cy + sr * sp * sy)


# ---------------------------------------------------------------- GLB helpers
def read_glb(path):
    d = open(path, "rb").read()
    o, js, bin_ = 12, None, None
    while o < len(d):
        ln, ty = struct.unpack("<II", d[o:o + 8]); o += 8
        ch = d[o:o + ln]; o += ln
        if ty == 0x4E4F534A: js = json.loads(ch)
        elif ty == 0x004E4942: bin_ = ch
    pa, ia = js["accessors"][0], js["accessors"][2]
    bv = js["bufferViews"]
    v = bv[pa["bufferView"]]; n = pa["count"]
    f = struct.unpack("<%df" % (n * 3), bin_[v["byteOffset"]:v["byteOffset"] + n * 12])
    iv = bv[ia["bufferView"]]
    w = 4 if ia["componentType"] == 5125 else 2
    idx = struct.unpack("<%d%s" % (ia["count"], "I" if w == 4 else "H"),
                        bin_[iv["byteOffset"]:iv["byteOffset"] + ia["count"] * w])
    return [(f[i * 3], f[i * 3 + 1], f[i * 3 + 2]) for i in range(n)], list(idx)


def read_glb_prim(path):
    """POSITION + indices of a GLB's first primitive, resolved through the
    primitive's own accessor references. read_glb() above assumes the fixed
    accessor layout palx emits; the textured GLBs palxtex writes carry a UV
    accessor in between, so their indices must be looked up, not assumed."""
    d = open(path, "rb").read()
    o, js, bin_ = 12, None, None
    while o < len(d):
        ln, ty = struct.unpack("<II", d[o:o + 8]); o += 8
        ch = d[o:o + ln]; o += ln
        if ty == 0x4E4F534A: js = json.loads(ch)
        elif ty == 0x004E4942: bin_ = ch
    prim = js["meshes"][0]["primitives"][0]
    pa = js["accessors"][prim["attributes"]["POSITION"]]
    ia = js["accessors"][prim["indices"]]
    bv = js["bufferViews"]
    v = bv[pa["bufferView"]]; n = pa["count"]
    off = v.get("byteOffset", 0) + pa.get("byteOffset", 0)
    f = struct.unpack("<%df" % (n * 3), bin_[off:off + n * 12])
    iv = bv[ia["bufferView"]]
    w = 4 if ia["componentType"] == 5125 else 2
    ioff = iv.get("byteOffset", 0) + ia.get("byteOffset", 0)
    idx = struct.unpack("<%d%s" % (ia["count"], "I" if w == 4 else "H"),
                        bin_[ioff:ioff + ia["count"] * w])
    return [(f[i * 3], f[i * 3 + 1], f[i * 3 + 2]) for i in range(n)], list(idx)


def write_glb_tex(path, verts, uvs, idx, name, tex_uri):
    """Landscape tile GLB: POSITION + NORMAL + TEXCOORD_0 + one material whose
    base-colour map is the tile's baked landscape texture.

    Same accessor layout palxtex emits for the placed props (0=POSITION,
    1=NORMAL, 2=TEXCOORD_0, 3=indices), so the renderer's buildGlb() reads it
    through exactly the path it already uses for every other textured mesh --
    no TerrainLayer change, and the tiles stop falling through to the flat
    fallback colour.

    tex_uri is relative to the GLB, so it resolves to public/terrain_meshes/tex/
    the same way the props' textures do.
    """
    nrm = [[0.0, 0.0, 0.0] for _ in verts]
    for t in range(0, len(idx), 3):
        a, bb, c = idx[t], idx[t + 1], idx[t + 2]
        pa, pb, pc = verts[a], verts[bb], verts[c]
        ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
        vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        for i in (a, bb, c):
            nrm[i][0] += nx; nrm[i][1] += ny; nrm[i][2] += nz
    for n in nrm:
        l = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
        if l <= 0: n[0], n[1], n[2] = 0.0, 0.0, 1.0
        else: n[0] /= l; n[1] /= l; n[2] /= l

    pos = b"".join(struct.pack("<3f", *v) for v in verts)
    nb = b"".join(struct.pack("<3f", *n) for n in nrm)
    tb = b"".join(struct.pack("<2f", *t) for t in uvs)
    use32 = len(verts) > 65535
    ib = b"".join(struct.pack("<I" if use32 else "<H", i) for i in idx)
    while len(ib) % 4: ib += b"\0"
    blob = pos + nb + tb + ib
    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
    o_n, o_t, o_i = len(pos), len(pos) + len(nb), len(pos) + len(nb) + len(tb)
    g = {"asset": {"version": "2.0", "generator": "mappal-terrain-clip"},
         "scenes": [{"nodes": [0]}], "scene": 0,
         "nodes": [{"mesh": 0, "name": name}],
         "meshes": [{"name": name, "primitives": [
             {"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
              "indices": 3, "material": 0}]}],
         "images": [{"uri": tex_uri}],
         "samplers": [{"wrapS": 33071, "wrapT": 33071, "magFilter": 9729, "minFilter": 9987}],
         "textures": [{"source": 0, "sampler": 0}],
         "materials": [{"name": "landscape", "doubleSided": True,
                        "pbrMetallicRoughness": {"baseColorTexture": {"index": 0},
                                                 "metallicFactor": 0, "roughnessFactor": 1}}],
         "buffers": [{"byteLength": len(blob)}],
         "bufferViews": [
             {"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "target": 34962},
             {"buffer": 0, "byteOffset": o_n, "byteLength": len(nb), "target": 34962},
             {"buffer": 0, "byteOffset": o_t, "byteLength": len(tb), "target": 34962},
             {"buffer": 0, "byteOffset": o_i, "byteLength": len(ib), "target": 34963}],
         "accessors": [
             {"bufferView": 0, "componentType": 5126, "count": len(verts), "type": "VEC3",
              "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]},
             {"bufferView": 1, "componentType": 5126, "count": len(verts), "type": "VEC3"},
             {"bufferView": 2, "componentType": 5126, "count": len(verts), "type": "VEC2"},
             {"bufferView": 3, "componentType": 5125 if use32 else 5123,
              "count": len(idx), "type": "SCALAR"}]}
    js = json.dumps(g).encode()
    while len(js) % 4: js += b" "
    out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
    out += struct.pack("<II", len(js), 0x4E4F534A) + js
    out += struct.pack("<II", len(blob), 0x004E4942) + blob
    open(path, "wb").write(out)


def write_glb(path, verts, idx, name):
    """POSITION + NORMAL + index GLB, same accessor layout palx emits (accessor
    0 = POSITION, 1 = NORMAL, 2 = indices). Normals are area-weighted face
    normals, the same convention Program.cs uses."""
    nrm = [[0.0, 0.0, 0.0] for _ in verts]
    for t in range(0, len(idx), 3):
        a, bb, c = idx[t], idx[t + 1], idx[t + 2]
        pa, pb, pc = verts[a], verts[bb], verts[c]
        ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
        vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        for i in (a, bb, c):
            nrm[i][0] += nx; nrm[i][1] += ny; nrm[i][2] += nz
    for n in nrm:
        l = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
        if l <= 0: n[0], n[1], n[2] = 0.0, 0.0, 1.0
        else: n[0] /= l; n[1] /= l; n[2] /= l

    pos = b"".join(struct.pack("<3f", *v) for v in verts)
    nb = b"".join(struct.pack("<3f", *n) for n in nrm)
    use32 = len(verts) > 65535
    ib = b"".join(struct.pack("<I" if use32 else "<H", i) for i in idx)
    while len(ib) % 4: ib += b"\0"
    blob = pos + nb + ib
    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
    g = {"asset": {"version": "2.0", "generator": "mappal-terrain-clip"},
         "scenes": [{"nodes": [0]}], "scene": 0,
         "nodes": [{"mesh": 0, "name": name}],
         "meshes": [{"name": name, "primitives": [
             {"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2}]}],
         "buffers": [{"byteLength": len(blob)}],
         "bufferViews": [
             {"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "target": 34962},
             {"buffer": 0, "byteOffset": len(pos), "byteLength": len(nb), "target": 34962},
             {"buffer": 0, "byteOffset": len(pos) + len(nb), "byteLength": len(ib), "target": 34963}],
         "accessors": [
             {"bufferView": 0, "componentType": 5126, "count": len(verts), "type": "VEC3",
              "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]},
             {"bufferView": 1, "componentType": 5126, "count": len(verts), "type": "VEC3"},
             {"bufferView": 2, "componentType": 5125 if use32 else 5123,
              "count": len(idx), "type": "SCALAR"}]}
    js = json.dumps(g).encode()
    while len(js) % 4: js += b" "
    out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
    out += struct.pack("<II", len(js), 0x4E4F534A) + js
    out += struct.pack("<II", len(blob), 0x004E4942) + blob
    open(path, "wb").write(out)


# ------------------------------------------------------- 1. placed prop meshes
acts = json.load(open(CELLACTORS))
ifa = {r["cell"]: r["loc"] for r in acts
       if r["name"] == "RootComponent0" and r["cell"].startswith("Foliage")}
near = selected_props(acts, bx, by)
ngrd = sum(1 for d, r in near if is_ground(r["mesh"], r["meshPath"]))
print(f"{b}: {len(near)} placed meshes ({ngrd} ground < {GROUND_R/100:.0f} m, "
      f"{len(near)-ngrd} props < {PROP_R/100:.0f} m), "
      f"{len(set(r['mesh'] for d, r in near))} distinct assets")

# Only emit props whose GLB actually came out of the extractor.
have = set()
rep = f"{SP}/terrain_manifest_uv_report.json"
if os.path.exists(rep):
    have = {r["name"] for r in json.load(open(rep)) if r.get("ok")}

out, skipped = [], 0
for d, r in near:
    if have and r["mesh"] not in have:
        skipped += 1
        continue
    q = rotator_to_quat(*r["rot"])
    out.append({"mesh": r["mesh"], "url": f"/terrain_meshes/{r['mesh']}.glb",
                "x": round(r["loc"][0], 1), "y": round(r["loc"][1], 1), "z": round(r["loc"][2], 1),
                "qx": round(q[0], 6), "qy": round(q[1], 6), "qz": round(q[2], 6), "qw": round(q[3], 6),
                "sx": round(r["scale"][0], 4), "sy": round(r["scale"][1], 4),
                "sz": round(r["scale"][2], 4)})
nprops = len(out)

# ------------------------------------------------------------- 1b. foliage
# Instance rotation arrives as a QUATERNION already (PerInstanceSMData stores a
# transform, not an FRotator), so it is passed through untouched rather than
# being round-tripped through Euler angles.
fol_all = selected_foliage(acts, ifa, bx, by)
fol_total = len(fol_all)
fol = capped_foliage(fol_all)
nfol = 0
fol_kinds = {}
for d, mesh, mpath, x, y, z, i in fol:
    if have and mesh not in have:
        continue
    out.append({"mesh": mesh, "url": f"/terrain_meshes/{mesh}.glb",
                "x": round(x, 1), "y": round(y, 1), "z": round(z, 1),
                "qx": round(i[3], 6), "qy": round(i[4], 6),
                "qz": round(i[5], 6), "qw": round(i[6], 6),
                "sx": round(i[7], 4), "sy": round(i[8], 4), "sz": round(i[9], 4)})
    fol_kinds[mesh] = fol_kinds.get(mesh, 0) + 1
    nfol += 1
print(f"  foliage: {nfol} instances emitted of {fol_total} within {FOLIAGE_R/100:.0f} m "
      f"({len(fol_kinds)} mesh types; nearest-first, caps {FOLIAGE_CAP} "
      f"decorative / {GROUND_FOLIAGE_CAP} ground; "
      f"2 mega-grass meshes excluded)")

# -------------------------------------------------- 2. landscape heightfield
# Emitted as ordinary props (see module docstring): the tile's own cooked
# loc/rot/scale IS the prop transform, and the clipped vertices stay in the
# QUAD units LandscapeMeshDto produced them in.
# TWO INDEXES, because the landscape is not all in one grid. terrain_index.json
# holds the three MainGrid proxies under Glass Tower. gndfm/terrain_index.json
# holds 939 more, and THEY ARE THE GROUND: a full LandscapeStreamingProxy set in
# the FarMountain_L0 runtime grid, which no earlier sweep enumerated because the
# grid census only matched names containing "Grid". At Stone Works its surface
# reads Z = 2826.0 at the base centre and at eight BP_PalMapObjectSpawner_* roots
# the game itself snapped to the ground (authored Z 2825.92 / 2830) - agreement
# to 0-4 cm - and it matches the 2814-2825 band of the 1,273 Grass_Lowpoly_sm
# instances painted in an 80x80 m box there. That is the floor the placed rock
# meshes sit on, and its absence is what "Stone Works is flooded" was.
#
# The same check at the other three bases (gnd/fm_verify_all.py), against the
# median Z of the foliage the game paints in an 80x80 m box on each base:
#     Lost Camp    foliage 12456   heightfield 12482.8   delta  +27 cm
#     Wooden Camp  foliage  7080   heightfield  7069.5   delta  -11 cm
#     Stone Works  foliage  2819   heightfield  2826.0   delta   +7 cm
#     Glass Tower  foliage -1841   NO FarMountain tile under the base - correct,
#                  its ground is the three MainGrid proxies already indexed.
# Foliage is painted ONTO terrain, so its height IS the surface height; agreeing
# with it to within a quarter of a metre is what makes this the ground and not
# a backdrop that happens to be nearby.
#
# REGENERATING gndfm/: the tiles are extracted by
#   LAND_NOGLB=1 gnd/run2.sh --landscape FarMountain_L0          (locations only)
#   PALX_OUT=$PWD/gndfm  gnd/run2.sh --landscape $(cat gnd/fm_cells.txt)
# where fm_cells.txt is the 24 FarMountain cells within 1.5 km of a base. That
# cache is ~800 MB of unclipped LOD0 tiles; only the clipped per-base results
# are served.
#
# TEXTURE. The tiles used to go out as POSITION+NORMAL only - no material, no
# image, no UVs - so TerrainLayer had nothing to draw them with and they fell
# through to its flat rock-green fallback. Under three of the four bases that
# fallback WAS the ground, and it read as a flat untextured green plane.
#
# A UE landscape has no single base-colour map to extract: the surface is
# layer-blended in the shader from per-component weightmaps and ~16 layer
# diffuses. palxground's --landbake evaluates that blend once, offline, into one
# base-colour image per proxy (see palxground/LandBake.cs, and the numbers it
# was checked against - the game's own cooked HLOD BaseColor bakes - in the
# header there). landtex_index.json maps proxy actor -> that image plus the
# tile's quad-space extent.
#
# The UV needs no new geometry data. LandscapeMeshDto's vertices are in QUAD
# units and a vertex's XY is exactly the index the weightmap grid is addressed
# by, so for a tile spanning vmin..vmax quads:
#     u = (x - vminX) / (gridW - 1),   gridW = vmaxX - vminX + 1
# and the bake was written on that same convention. Clipping only removes
# vertices, so the UV of every surviving vertex is unchanged.
LANDTEX = {}
_landtex_path = f"{OUT_MESH}/landtex_index.json"
if os.path.exists(_landtex_path):
    LANDTEX = json.load(open(_landtex_path))

nland = ntris = nland_tex = 0
LANDSCAPE_R = float(os.environ.get("LANDSCAPE_R", RADIUS))
land_recs = []
for idxfile, meshroot in ((f"{SP}/terrain_index.json", f"{SP}/terrain_meshes"),
                          (f"{SP}/gndfm/terrain_index.json", f"{SP}/gndfm/terrain_meshes")):
    if os.path.exists(idxfile):
        for r in json.load(open(idxfile)):
            r["_src"] = f"{meshroot}/{os.path.basename(r['glb'])}"
            land_recs.append(r)
if land_recs:
    for r in land_recs:
        lx, ly, lz = r["loc"]
        sx, sy, sz = r["scale"]
        yaw = math.radians(r.get("rot", [0, 0, 0])[1])
        c, s = math.cos(yaw), math.sin(yaw)
        src = r["_src"]
        if not os.path.exists(src):
            continue
        # Cheap reject before touching the file: the index carries the tile's own
        # vertex extent in quad units, so its world AABB is known without reading
        # a megabyte of vertices. 176 tiles x up to 1 M verts x 4 bases otherwise.
        if "vmin" in r and "vmax" in r:
            corners = [(X * sx, Y * sy) for X in (r["vmin"][0], r["vmax"][0])
                       for Y in (r["vmin"][1], r["vmax"][1])]
            wc = [(X * c - Y * s + lx, X * s + Y * c + ly) for X, Y in corners]
            if (min(p[0] for p in wc) > bx + LANDSCAPE_R or max(p[0] for p in wc) < bx - LANDSCAPE_R or
                    min(p[1] for p in wc) > by + LANDSCAPE_R or max(p[1] for p in wc) < by - LANDSCAPE_R):
                continue
        vs, idx = read_glb(src)
        if not vs:
            continue
        keep = []
        for i, (x, y, z) in enumerate(vs):
            X, Y = x * sx, y * sy
            wx, wy = X * c - Y * s + lx, X * s + Y * c + ly
            if math.hypot(wx - bx, wy - by) < LANDSCAPE_R:
                keep.append(i)
        if not keep:
            continue
        ks = set(keep)
        remap = {v: i for i, v in enumerate(keep)}
        tris = [idx[t:t + 3] for t in range(0, len(idx), 3)]
        tris = [t for t in tris if t[0] in ks and t[1] in ks and t[2] in ks]
        if not tris:
            continue
        nv = [vs[i] for i in keep]
        ni = [remap[i] for t in tris for i in t]
        nm = f"land_{b}__{r['actor']}"
        os.makedirs(OUT_MESH, exist_ok=True)
        lt = LANDTEX.get(r["actor"])
        fresh = not (KEEP_MESHES and os.path.exists(f"{OUT_MESH}/{nm}.glb"))
        if lt:
            if fresh:
                gx0, gy0 = lt["vmin"]
                gw, gh = lt["grid"]
                uvs = [((x - gx0) / (gw - 1), (y - gy0) / (gh - 1)) for x, y, z in nv]
                write_glb_tex(f"{OUT_MESH}/{nm}.glb", nv, uvs, ni, nm, lt["tex"])
            nland_tex += 1
        elif fresh:
            # No bake for this proxy: emit it as before rather than silently
            # dropping ground, and let the count below report it.
            write_glb(f"{OUT_MESH}/{nm}.glb", nv, ni, nm)
        q = rotator_to_quat(*r.get("rot", [0, 0, 0]))
        out.append({"mesh": nm, "url": f"/terrain_meshes/{nm}.glb",
                    "x": round(lx, 1), "y": round(ly, 1), "z": round(lz, 1),
                    "qx": round(q[0], 6), "qy": round(q[1], 6),
                    "qz": round(q[2], 6), "qw": round(q[3], 6),
                    "sx": sx, "sy": sy, "sz": sz})
        nland += 1
        ntris += len(ni) // 3
        print(f"  landscape {r['actor'][-12:]}  {len(nv):6d} v  {len(ni)//3:6d} t  "
              f"{os.path.getsize(f'{OUT_MESH}/{nm}.glb')//1024:5d} KB  "
              f"{('tex ' + lt['tex'].split('/')[-1][:34]) if lt else 'NO BAKE - flat fallback'}")


# ============================================================ 3. WATER
# Palworld's water is real authored geometry, but none of it survives a
# StaticMeshComponent-name filter, which is why it kept coming back "absent":
#   OCEAN      one BP_SimpleWater_C actor in the PERSISTENT level flagged
#              bWorldOceanPlane, whose root HISM holds 1,681 instances of a
#              10 m plane, each scaled 54x and stepped 54,000 cm, all at one
#              Z. That Z is the sea level and it is READ, not derived.
#   RIVERS     SplineMeshComponents from BP_UltimateRiverTool_C - the class
#              name does not contain "StaticMeshComponent".
#   PONDS      HISMs under BP_SimpleWater_C actors inside the cells.
#   WATERFALLS ordinary StaticMeshComponents that were already being placed,
#              but with an OverrideMaterial the old dump never recorded, and
#              with no base-colour texture, so they rendered flat green.
# build_water_index.py collects all of it (see that file for provenance) and
# records, per material, the cooked FLinearColor / opacity / roughness the
# MaterialInstanceConstant itself ships.
WATER_R = float(os.environ.get("WATER_R", GROUND_R))
widx_path = f"{SP}/water_index.json"
nwater = {}
if os.path.exists(widx_path):
    widx = json.load(open(widx_path))
    wmats = widx["materials"]

    def water_style(matname):
        """The cooked material's own values, passed straight to TerrainLayer."""
        m = wmats[matname]
        d = {"water": {"kind": m["kind"], "material": matname, "source": m["source"]}}
        for k in ("tint", "flat", "opacity", "transparent", "rough"):
            if k in m:
                d[k] = m[k]
        return d

    def emit(mesh, x, y, z, q, sc, matname):
        p = {"mesh": mesh, "url": f"/terrain_meshes/{mesh}.glb",
             "x": round(x, 1), "y": round(y, 1), "z": round(z, 1),
             "qx": round(q[0], 6), "qy": round(q[1], 6),
             "qz": round(q[2], 6), "qw": round(q[3], 6),
             "sx": round(sc[0], 4), "sy": round(sc[1], 4), "sz": round(sc[2], 4)}
        p.update(water_style(matname))
        out.append(p)
        k = wmats[matname]["kind"]
        nwater[k] = nwater.get(k, 0) + 1

    # ---- 3a. ocean tiles ---------------------------------------------------
    # A tile is 540 m across, so keep every tile whose SQUARE overlaps the
    # radius, not just those centred inside it - otherwise the sea stops short
    # of the land and leaves a ring of nothing.
    #
    # OCEAN_R is tied to GROUND_R on purpose: THE SEA MUST NOT OUT-REACH THE
    # LAND. Palworld's ocean really is a 41x41 grid of these tiles spanning
    # 22 km, and running it out to the visual horizon was tried - it removes the
    # patch's hard far edge, but because our land stops at GROUND_R the extra
    # sea appears ABOVE the terrain silhouette in every elevated timelapse
    # shot, and a flat unlit [0,1,0.6] surface there is exactly the "large flat
    # teal plane dominating the scene". Keeping the sea inside the land's reach
    # puts sky there instead. Raise OCEAN_R (and GROUND_R with it) if the land
    # ever runs further.
    #
    # The tiles are merged into ONE mesh regardless, the same way the river
    # segments already are: it is 9-12 draw calls saved and the geometry is
    # unchanged. All instances share one Z and one scale and carry no rotation,
    # so the merge is a pure translate+scale of the tile's own vertices.
    oc = widx["ocean"]
    half = 0.5 * abs(oc["gridX"][2])
    OCEAN_R = float(os.environ.get("OCEAN_R", GROUND_R))
    tiles = [t for t in oc["instances"]
             if abs(t[0] - bx) <= OCEAN_R + half and abs(t[1] - by) <= OCEAN_R + half]
    if tiles:
        src, sidx = read_glb_prim(f"{OUT_MESH}/{oc['mesh']}.glb")
        ov, oi = [], []
        for ix, iy, iz, sx, sy, sz in tiles:
            base_i = len(ov)
            for vx, vy, vz in src:
                ov.append((vx * sx + ix, vy * sy + iy, vz * sz + iz))
            oi.extend(base_i + i for i in sidx)
        nm = f"ocean_{b}"
        os.makedirs(OUT_MESH, exist_ok=True)
        if not (KEEP_MESHES and os.path.exists(f"{OUT_MESH}/{nm}.glb")):
            write_glb(f"{OUT_MESH}/{nm}.glb", ov, oi, nm)
        p = {"mesh": nm, "url": f"/terrain_meshes/{nm}.glb",
             "x": 0.0, "y": 0.0, "z": 0.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0,
             "sx": 1.0, "sy": 1.0, "sz": 1.0}
        p.update(water_style(oc["material"]))
        out.append(p)
        nwater["ocean"] = len(tiles)
        print(f"  ocean: {len(tiles)} tiles < {OCEAN_R/100:.0f} m -> {nm}.glb "
              f"({len(ov)} v, {len(oi)//3} t, "
              f"{os.path.getsize(f'{OUT_MESH}/{nm}.glb')//1024} KB), sea level "
              f"Z={oc['seaLevelZ']} cm")

    # ---- 3b. placed water: waterfalls, pond planes, the bend mesh ----------
    for r in widx["placed"]:
        lx, ly, lz = r["loc"]
        if math.hypot(lx - bx, ly - by) > PROP_R:
            continue
        cq = rotator_to_quat(*r["rot"])
        if not r["instances"]:
            emit(r["mesh"], lx, ly, lz, cq, r["scale"], r["material"])
            continue
        # An instanced component's PerInstanceSMData is in COMPONENT space, so
        # the component's own yaw and scale still apply on top of it.
        yaw = math.radians(r["rot"][1])
        c, s = math.cos(yaw), math.sin(yaw)
        for t in r["instances"]:
            px, py, pz = (t[0] * r["scale"][0], t[1] * r["scale"][1], t[2] * r["scale"][2])
            wx, wy = px * c - py * s + lx, px * s + py * c + ly
            emit(r["mesh"], wx, wy, pz + lz, cq,
                 (r["scale"][0] * t[7], r["scale"][1] * t[8], r["scale"][2] * t[9]),
                 r["material"])

    # ---- 3c. rivers --------------------------------------------------------
    # Replay Unreal's spline-mesh deformation exactly: the source plane's
    # forward (X) coordinate becomes the Hermite parameter along the segment's
    # own StartPos/StartTangent -> EndPos/EndTangent curve, and the remaining
    # two local axes are placed in the slice frame built from SplineUpDir.
    # (USplineMeshComponent::CalcSliceTransform, ForwardAxis = X, the default -
    # no segment in these cells overrides ForwardAxis, SplineBoundaryMin/Max,
    # roll or offset.) All 472 segments merge into ONE mesh per base.
    seg = [r for r in widx["rivers"]
           if min(math.hypot(r["startPos"][0] - bx, r["startPos"][1] - by),
                  math.hypot(r["endPos"][0] - bx, r["endPos"][1] - by)) < PROP_R]
    if seg:
        src, sidx = read_glb_prim(f"{SP}/mappal/public/terrain_meshes/water_SM_River_Plane.glb")
        mnx = min(v[0] for v in src)
        mxx = max(v[0] for v in src)
        rv, ri = [], []
        for r in seg:
            p0, t0 = r["startPos"], r["startTangent"]
            p1, t1 = r["endPos"], r["endTangent"]
            s0, s1 = r["startScale"], r["endScale"]
            base = len(rv)
            for vx, vy, vz in src:
                a = (vx - mnx) / (mxx - mnx)
                a2, a3 = a * a, a * a * a
                h00, h10 = 2 * a3 - 3 * a2 + 1, a3 - 2 * a2 + a
                h01, h11 = -2 * a3 + 3 * a2, a3 - a2
                d00, d10 = 6 * a2 - 6 * a, 3 * a2 - 4 * a + 1
                d01, d11 = -6 * a2 + 6 * a, 3 * a2 - 2 * a
                pos = [h00 * p0[i] + h10 * t0[i] + h01 * p1[i] + h11 * t1[i] for i in range(3)]
                dr = [d00 * p0[i] + d10 * t0[i] + d01 * p1[i] + d11 * t1[i] for i in range(3)]
                dl = math.sqrt(sum(v * v for v in dr)) or 1.0
                dr = [v / dl for v in dr]
                up = r["up"]
                xv = [up[1] * dr[2] - up[2] * dr[1], up[2] * dr[0] - up[0] * dr[2],
                      up[0] * dr[1] - up[1] * dr[0]]
                xl = math.sqrt(sum(v * v for v in xv)) or 1.0
                xv = [v / xl for v in xv]
                yv = [dr[1] * xv[2] - dr[2] * xv[1], dr[2] * xv[0] - dr[0] * xv[2],
                      dr[0] * xv[1] - dr[1] * xv[0]]
                yl = math.sqrt(sum(v * v for v in yv)) or 1.0
                yv = [v / yl for v in yv]
                kx = s0[0] + (s1[0] - s0[0]) * a
                ky = s0[1] + (s1[1] - s0[1]) * a
                rv.append(tuple(pos[i] + xv[i] * kx * vy + yv[i] * ky * vz for i in range(3)))
            ri.extend(base + i for i in sidx)
        nm = f"river_{b}"
        os.makedirs(OUT_MESH, exist_ok=True)
        if not (KEEP_MESHES and os.path.exists(f"{OUT_MESH}/{nm}.glb")):
            write_glb(f"{OUT_MESH}/{nm}.glb", rv, ri, nm)
        p = {"mesh": nm, "url": f"/terrain_meshes/{nm}.glb",
             "x": 0.0, "y": 0.0, "z": 0.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0,
             "sx": 1.0, "sy": 1.0, "sz": 1.0}
        p.update(water_style(seg[0]["material"]))
        out.append(p)
        nwater["river"] = nwater.get("river", 0) + len(seg)
        print(f"  river: {len(seg)} spline segments -> {nm}.glb "
              f"({len(rv)} v, {len(ri)//3} t, {os.path.getsize(f'{OUT_MESH}/{nm}.glb')//1024} KB)")
    print(f"  water: {sum(nwater.values())} surfaces " +
          ", ".join(f"{k}={v}" for k, v in sorted(nwater.items())) +
          f" (ocean merged to the horizon, rest < {PROP_R/100:.0f} m; "
          f"sea level Z={oc['seaLevelZ']} cm, extracted)")
else:
    print("  water: water_index.json missing - no water emitted")

# ================================= 4. CULL VEGETATION INSIDE THE FOOTPRINT
# TIME-AWARE, not a static erase. At frame 0 only the Palbox exists, so deleting
# the FINAL footprint's trees from the start would clear the forest before
# anything was built there — the reverse of the defect, and just as wrong.
#
# Each plant that stands inside the footprint is tagged with the instance_ids of
# the pieces standing on it, and TerrainLayer hides it only while one of those
# pieces is actually in the frame's live object set. So a tree disappears on the
# frame the foundation under it is placed, and no earlier. It also cannot outlive
# the base: at Lost Camp (5fed0024), whose base was deleted from the save
# outright, the live object set empties and every tagged plant comes back.
#
# The renderer needs no per-frame footprint maths and the timelapse harness needs
# no new call: the ids are matched against the object list setObjects() already
# pushes every frame. A prop with no `cullBy` behaves exactly as before, so an
# older terrain_*.json still renders unchanged.
_fp, _unsized = built_footprint(b)
_fpidx = footprint_index(_fp)
ncull = ncull_fol = 0
cull_kinds = {}
for p in out:
    if not is_plant(p["mesh"]):
        continue
    ids = footprint_cullers(_fpidx, p["x"], p["y"])
    if not ids:
        continue
    p["cullBy"] = ids
    ncull += 1
    cull_kinds[p["mesh"]] = cull_kinds.get(p["mesh"], 0) + 1
print(f"  footprint: {len(_fp)} placed pieces ({_unsized} with no registry box, "
      f"{_UNSIZED_BOX:.0f} cm fallback), margin {FOOTPRINT_MARGIN:.0f} cm -> "
      f"{ncull} vegetation instances tagged cullBy ({len(cull_kinds)} meshes; "
      f"top " + ", ".join(f"{k}x{v}" for k, v in
                          sorted(cull_kinds.items(), key=lambda kv: -kv[1])[:5]) + ")")

SUFFIX = os.environ.get("TERRAIN_SUFFIX", "")
json.dump({"base": b, "cellSize": 25600, "props": out},
          open(f"{SP}/mappal/public/union/terrain_{b}{SUFFIX}.json", "w"))
print(f"  -> terrain_{b}{SUFFIX}.json  {nprops} props + {nland} landscape tiles "
      f"({nland_tex} textured, {nland - nland_tex} without a bake; {ntris} landscape tris), "
      f"{skipped} props skipped for missing GLB "
      f"({os.path.getsize(f'{SP}/mappal/public/union/terrain_{b}{SUFFIX}.json')//1024} KB)")
