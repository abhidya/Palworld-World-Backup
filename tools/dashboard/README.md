# Palworld Dashboard — Diva Booties

A save-file dashboard for the **Diva Booties** dedicated server, built from
this repo. Lives at `tools/dashboard/` in
[Palworld-World-Backup](https://github.com/abhidya/Palworld-World-Backup).

## What it shows

| Tab | Feature |
|---|---|
| Overview | Trainer stats, top stockpiles, world facts, element distribution |
| Trends | Time series mined from the backup repo's git history + milestone/anomaly event feed + deltas |
| Bases | Per-base machines & stations (display names), workers + condition, storage |
| Inventory | Every item across all base chests + player backpacks, searchable, with locations |
| Recipes | **Mission planner** (need N of X → gather list, craft order, best base/machines/pals, ETA) + recipe search |
| Pals | All pals: elements, IVs, souls, stars, passives (hover = effect), active moves (hover = power) |
| Care | Tamagotchi view — sanity / belly / sickness of every base worker |
| Eggs | Incubators + stored eggs **with contents**: species, gender, IVs, passives, moves |
| Breeding | Exact 1.0 table: pair→child + passive-pool notes, target→pairs, multi-step planner from owned pals |
| Players | Per-player gear with durability, party, records; census of everyone who joined |
| Guide | Computed next steps: towers left, paldeck-completing breeds, unspent points, unhappy workers |

The GitHub Pages copy lives in the backup repo's `docs/` and refreshes on every
snapshot commit via a local git `pre-commit` hook (no GitHub Actions).

## Usage

Double-click **`refresh.bat`** after each backup commit, then open
**`palworld-dashboard.html`** (single self-contained file — send it to anyone).

`extract.py` reads this repo's `world/current` by default (resolved relative
to its own location, two levels up from `tools/dashboard/`); pass a different
world dir as the first argument. Same for `trends.py` and the repo root.

## How it works

- `extract.py` — decompresses the PlM1 (Oodle) `Level.sav` + player saves via
  `pyooz`, parses GVAS with `palworld-save-tools` (oMaN-Rod fork, 1.0-format
  decoders), and writes `data.js`.
- `dashboard.html` — the UI; loads `data.js`.
- `bundle.py` — inlines `data.js` → `palworld-dashboard.html` (shareable single file).

### Datasets (vendored)

- `palcalc_db.json`, `palcalc_breeding.json` — [PalCalc](https://github.com/tylercamp/palcalc) 1.0 pal DB + exact breeding table (299 pals)
- `items.json` — [PalworldSaveTools](https://github.com/deafdudecomputers/PalworldSaveTools) item id → name/type
- `recipes.json` — palworld.wiki.gg item data module (799 recipes)

### Python deps

```
py -3 -m pip install -r requirements.txt
```

or directly:

```
py -3 -m pip install git+https://github.com/oMaN-Rod/palworld-save-tools.git git+https://github.com/MRHRTZ/pyooz.git
```
