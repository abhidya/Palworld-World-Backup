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

Rendered timelapses are the **exception**: `docs/timelapse/*.mp4` must stay out
of LFS. GitHub Pages does not run the LFS smudge filter, so an LFS-tracked video
reaches the browser as its ~130-byte pointer file and the player breaks. Size is
controlled at encode time instead — `tools/timelapse/encode.sh` re-encodes at
rising CRF until each file fits under `MAX_MP4_BYTES`.

The ceiling is set on the host rather than in the script, so the render job can
be tuned without editing the pipeline: `MAX_MP4_BYTES=40000000` is exported by
the `com.mannybhidya.palworld-snapshot` launchd job and inherited all the way
down to `encode.sh`. 40 MB sits under GitHub's 50 MB push-warning threshold, and
one CRF step (24) measured ~28 MB for the largest site — so it lands in a single
re-encode rather than escalating to the soft end of the ladder. Delete the
variable to fall back to the 95 MB default.

> **Known debt, deliberately not paid:** mp4s occupy ~856 MB across 32 blob
> versions in history (`.git` is 3.4 GB — 2.4 GB of that is the `.sav` LFS store
> and is the point of the repo). Removing them means `git filter-repo` and a
> force-push of the *disaster-recovery copy of a live world*, to reclaim ~25% of
> a repo that is nowhere near a limit and is failing nothing. The ceiling above
> caps the growth that actually mattered; the rewrite stays available later, at
> a moment when it is not being traded against backup integrity.
> Note `git lfs migrate` is **not** an option — see above, Pages cannot serve it.

Verify a clone is real save data, not pointers:

```bash
python scripts/verify_snapshot.py .
```

## Server updates

Two independent things update, on different mechanisms. Conflating them wastes a
restart and misses the one that actually matters.

| Axis | What it is | How it updates |
|---|---|---|
| **Game build** | Palworld dedicated-server build (SteamCMD manifest) | Automatic, daily, player-gated |
| **Container image** | `thijsvanloef/palworld-server-docker` | Manual — `pull` + `up -d` |

### Game build — automatic, already player-gated

Driven entirely by the image's own updater, configured in the host `.env` (never
committed):

```
UPDATE_ON_BOOT=true
AUTO_UPDATE_ENABLED=true
AUTO_UPDATE_CRON_EXPRESSION=0 5 * * *
AUTO_UPDATE_WARN_MINUTES=5
```

The cron fires at 05:00 local. `countdown_message()` in the image's
`helper_functions.sh` skips the countdown outright when nobody is online; with
players connected it broadcasts a 5-minute warning, re-checks the player count
every minute, and cuts out early once the last player leaves. `shutdown_server()`
then **saves first and refuses to shut down if the save fails**.

Confirmed unattended: v1.0.3.101283 → v1.0.4.102642 on 2026-09-07 at 05:00 PDT.

```
2026-09-07 12:00:14Z  An Update Is Available. Latest Version: 1125678324530723107
2026-09-07 12:02:11Z  Game version is v1.0.4.102642
```

**Do not add update polling or restart logic to `scripts/snapshot_from_mac.py`.**
It races the cron and restarts without the pre-shutdown save. Detecting updates
by grepping `docker logs --tail=N` is also unsound in both directions: the
boot-time string scrolls out of the window (missed update), and while it is still
in the window every 60 s poll fires another restart (restart loop). The snapshot
job's only job is to snapshot.

### Container image — manual

`docker compose restart` re-runs the entrypoint, so it *does* pick up a pending
game build — but it **never** changes the image. A newer image appears in the
boot log as `New version available: <tag>` and needs an explicit pull, which
recreates the container:

```bash
python3 scripts/snapshot_from_mac.py --force               # safety net first
cd ~/PalworldServer && docker compose pull && docker compose up -d
```

**Run compose from `~/PalworldServer`, never from this repo.** The live project
is `~/PalworldServer/docker-compose.yml` (compose project `palworldserver`, with
its `.env` beside it). `server-config/docker-compose.yml` here is a *tracked
copy* for disaster recovery — byte-identical, but compose invoked from
`server-config/` aborts on the missing `.env`, and a `docker compose restart`
that never ran still exits quietly enough to look like it worked.

A weekly launchd job (`com.mannybhidya.palworld-image-check`) compares the local
image digest against the registry and raises a notification when they diverge —
it is read-only and never pulls or restarts. Run it by hand any time:

```bash
scripts/check_container_image.sh        # exit 0 current, 1 stale, 2 undetermined
```

Recreating the container is safe because the game install lives in the external
`palworld-game` volume and saves are bind-mounted — see the comments in
`server-config/docker-compose.yml` for why that split exists. Confirm afterwards
that the banner flipped:

```
The server is up to date!
The container is up to date!
```

### Checking versions

```bash
# live game build, via the REST API
python3 -c "import sys; sys.path.insert(0,'scripts'); import snapshot_from_mac as s; \
  print(s.api_get('/info', s.load_env()['ADMIN_PASSWORD'])['version'])"

# game build + pending image, from the boot log
docker logs palworld-server 2>&1 \
  | grep -aE "Game version is|An Update Is Available|New version available" | tail
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
