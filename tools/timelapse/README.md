# Base construction timelapses

Renders every Diva Booties base being built, one placed piece per frame, from
the full snapshot history, and publishes the videos to the command center's
**Timelapse** tab.

Live: https://abhidya.github.io/Palworld-World-Backup/#Timelapse

## What you are looking at

3,761 frames across four bases. Each piece is placed by the player the save
actually recorded against it, wearing the gear their save records for that point
in history, in a pose baked from the game's own build animation. The world
around them - ground, cliffs, trees, sea, rivers, waterfalls and wild-Pal spawn
points - is extracted from the game files, not approximated.

| base | pieces | frames |
|---|---|---|
| Glass Tower `07f13218` | 2,189 | 1,490 |
| Stone Works `de44d9f4` | 1,007 | 1,074 |
| Wooden Camp `16fca097` | 982 | 879 |
| Lost Camp `5fed0024` | 90 | 318 |

## Recorded vs rendered

This distinction is load-bearing and is repeated in the code. **Recorded** in the
save: who built each piece (`build_player_uid`), equipped gear, the in-game clock
(`GameDateTimeTicks`), and all geometry. **Rendered choices**, which the save does
NOT contain:

- **Build order** is reconstructed. The save has no placement timestamps at all -
  every field was checked. `buildorder.js` derives a plausible order from geometry
  (support, storey, locality), so *what* was built is real and *the sequence* is inferred.
- **Avatar position.** `LastTransform` gives a snapshot-granularity last-known
  position, not where someone stood to place a given piece. "Stand on the piece
  just built, face the next" is a rendering rule.
- **Demolition attribution.** The save records that a piece existed and later did
  not. It records no destruction, no demolisher, and no time within the gap.
- **Wild Pals** are spawn-table positions from the game pak - where spawners
  stand and what they may roll - not sightings of individual Pals.

Timestamps come from snapshot boundaries, so any gap is an unknown *window*, not
a moment. Lost Camp's disappearance is labelled that way: present 2026-08-11
16:31:40, gone by 2026-08-16 04:03:04.

## Running it

```bash
export PALTL_WORK=/path/to/scratch     # holds extracted assets + frames
tools/timelapse/refresh.sh             # guarded: skips unless enough new history
tools/timelapse/refresh.sh --force     # ignore the guard
```

`refresh.sh` rebuilds the history indexes, renders every site, encodes, and
publishes to `docs/timelapse/`.

`snapshot_from_mac.py` fires it, detached, after every snapshot it commits - so
the videos follow the saves without anyone remembering to run this. A full
render is hours against hourly snapshots, so two guards make that safe:
`MIN_NEW_SNAPSHOTS` (default 24 world commits since the last render) and a lock
directory, so a render still going when the next snapshot lands is left alone. A
host with no `PALTL_WORK` skips the render and just takes snapshots.

### Which sites get rendered

Nothing hardcodes a base id. `scripts/timelapse_sites.py` reads the render
manifest that `build_union.py` derives from the saves, and every stage - render,
encode, publish - asks it. Smallest site first, so a broken run surfaces cheap.

```bash
python3 scripts/timelapse_sites.py            # what this world has
PALTL_SKIP=c0105eum  tools/timelapse/refresh.sh   # drop a site
PALTL_BASES="5fed0024 16fca097" ...               # only these, in this order
```

Useful env: `PPF` (pieces per frame; 1 = every placement gets a frame),
`BUILDOUT_HOUR`, `DAYNIGHT`, `ACTORS`, `STANCE`, `WILDPVP`, `GROUND_R`,
`PROP_R`, `FOLIAGE_R`, `OCEAN_R`, `LANDSCAPE_R`.

> `MAXFRAMES` silently *raises* `PPF`, which batches pieces and makes the order
> look like chunked BFS. Use it for smoke tests only, and judge ordering at `PPF=1`.

## Assets

`extractors/` holds the CUE4Parse tools (macOS - no Windows needed). They read
your own Palworld install; **no game assets are stored in this repo**, since they
are Pocketpair's copyrighted content. Set `PALX_PAKS` at your install's
`Pal/Content/Paks` and `PALX_USMAP` at a `Mappings.usmap`.

`Mappings.usmap` is **mandatory**. Palworld cooks with `PKG_UnversionedProperties`,
so without it extraction returns silent empties - a zero result means the mappings
were not applied, not that the asset is absent. Build with
`-p:CUE4PARSE_SKIP_NATIVE=true`; player animations are `FUECompressedAnimData`
and decode in pure C#, so the native ACL library is not needed.

## Things that cost days to learn

- **Ground is placed static meshes, not one Landscape.** The ground under three of
  the four bases is a `LandscapeStreamingProxy` set in the **`FarMountain_L0`**
  runtime grid. Earlier sweeps missed it by enumerating only grids matching
  `*Grid*`; the pak also has `Foliage_L0`, `CloseRange_L0`, `oilrig_L0` and ~40
  HLOD grids. Landscape has no base-colour map - it is layer-blended in the
  shader, so `LandBake.cs` composites the real weightmaps against the layer
  diffuses (validated at 0.96-0.99 correlation against the game's own cooked bake).
- **The ocean lives in the persistent level**, not in any World Partition cell:
  one `BP_SimpleWater_C` actor, 1,681 `S_WaterMesh` tiles, sea level **Z -2102.615**.
  Rivers are 472 `SplineMeshComponent`s. A `class contains "StaticMeshComponent"`
  filter misses all of it.
- **Exported GLBs carry no skin weights.** Poses are CPU-skinned and baked into
  vertex positions, so the renderer's load path is unchanged.
- **The sun is clamped** to <=0.1 h/frame, forward-only, with a hard abort above one
  cycle per 4 s. Unclamped, snapshot gaps imply a median 1.52 in-game days per
  frame. **Do not remove that guard** - it is a photosensitive-seizure risk, not a
  style preference.
- **`CommonDropItem3D` is ground loot on a despawn timer, not construction.** It has
  corrupted three separate analyses here: it inflated build stats from 819/279 to
  2322/1785, and faked a "frantic teardown" at Lost Camp that was 93 dropped bags
  while the built count sat flat at 63.
