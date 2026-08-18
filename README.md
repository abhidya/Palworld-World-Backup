# Palworld World Backup

Versioned, offsite backup of the authoritative **Diva Booties** Palworld
dedicated-server world. Every commit is a verified, self-consistent snapshot of
the live world save.

> **This repository is the disaster-recovery copy of a live world.**
> Read `RESTORE.md` before writing anything back to the server.

## Layout

| Path | What it is |
|---|---|
| `world/current/` | The complete authoritative world save tree. This is the restore set. |
| `world/current/Level.sav` | The world itself (terrain deltas, bases, pals, guilds). |
| `world/current/LevelMeta.sav` | World metadata. |
| `world/current/WorldOption.sav` | World option snapshot written at world creation. |
| `world/current/Players/*.sav` | One file per player character. |
| `world/current/backup/world/*` | Palworld's own rotating engine backups, included by owner decision. |
| `server-config/PalWorldSettings.ini` | **Sanitised** server settings. Secrets replaced with placeholders. |
| `server-config/Engine.ini`, `GameUserSettings.ini` | Reproducibility of the server environment. |
| `metadata/snapshot.json` | Manifest for the current commit: hashes, counts, provenance, boundary. |
| `scripts/` | Restore and portable-editing helpers. |

Version history **is** the git history — there is no timestamped duplicate
directory per backup. Use `git log` to see snapshots and
`git checkout <sha> -- world/current` (into a scratch clone) to retrieve one.

## Secrets

`AdminPassword`, `ServerPassword`, `RCONPassword`, `PublicIP` and `BanListURL`
are stripped from the committed config and replaced with
`<REDACTED:supplied-locally>`. The real values live only in the live server's
`PalWorldSettings.ini` on the host. A snapshot **aborts** rather than commit if
any known secret value is detected anywhere in the staged tree.

To reconstruct a working server config, copy `server-config/PalWorldSettings.ini`
into place and re-enter the redacted values by hand.

## Git LFS

All `*.sav` files are stored in Git LFS. You **must** have `git lfs` installed
before cloning, or you will get pointer text files instead of world data:

```bash
git lfs install
git clone https://github.com/abhidya/Palworld-World-Backup.git
```

Verify a clone is real save data, not pointers:

```bash
python scripts/verify_snapshot.py .
```

## Dashboard (GitHub Pages)

`docs/` hosts a static dashboard built from every snapshot — bases, inventory,
recipes/mission planner, pal condition, eggs (with contents), breeding
calculator, trends and milestones mined from this repo's git history.

- Live: enable **Settings → Pages → Deploy from a branch → `main` / `/docs`**.
- It refreshes automatically: a local `pre-commit` hook regenerates
  `docs/data.js` + `docs/trends.js` on every snapshot commit (no CI involved).
  Re-install the hook after a fresh clone by copying it from
  `scripts/update_dashboard.py` docs, or run that script manually.
- Toolchain lives in `D:\palworld-dashboard` on the rig.
