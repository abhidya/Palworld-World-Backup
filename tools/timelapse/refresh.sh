#!/bin/bash
# Regenerate the base timelapses end to end and publish them to the command
# center. Idempotent and safe to run from cron/launchd.
#
#   tools/timelapse/refresh.sh [--force]
#
# snapshot_from_mac.py fires this after every snapshot it commits, detached, so
# an hourly snapshot never waits on a render. A full render is ~7,000 frames and
# hours of wall clock, so two guards keep that safe: the staleness check below
# (MIN_NEW_SNAPSHOTS world commits since the last render) and a lock, so a
# render still running when the next snapshot lands is left alone.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && cd .. && pwd)"
source "$HERE/sites.sh"
WORK="${PALTL_WORK:?set PALTL_WORK to a scratch dir holding the extracted assets}"
MAPPAL_ROOT="${MAPPAL_ROOT:-$WORK/mappal}"
MIN_NEW_SNAPSHOTS="${MIN_NEW_SNAPSHOTS:-24}"
STAMP="$WORK/.last_timelapse_commit"
LOCK="$WORK/.timelapse_refresh.lock"

# One render at a time. mkdir is atomic on every filesystem this runs on;
# a stale lock from a killed run is reported rather than silently ignored.
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "[timelapse] a refresh is already running (lock: $LOCK) - skipping"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT
export PALTL_WORK="$WORK"
export PALTL_REPO="$REPO"
export MAPPAL_ROOT
export PYTHONPATH="$WORK${PALTL_SITE_PACKAGES:+:$PALTL_SITE_PACKAGES}${PYTHONPATH:+:$PYTHONPATH}"

cd "$REPO"
head_now="$(git rev-parse HEAD)"
if [[ "${1:-}" != "--force" && -f "$STAMP" ]]; then
  since="$(cat "$STAMP")"
  n="$(git rev-list --count "$since..HEAD" -- world/current 2>/dev/null || echo 999)"
  if (( n < MIN_NEW_SNAPSHOTS )); then
    echo "[timelapse] only $n new world commits since last render (need $MIN_NEW_SNAPSHOTS) - skipping"
    exit 0
  fi
  echo "[timelapse] $n new world commits since last render - regenerating"
fi

# 1. rebuild the history indexes from every Level.sav we hold
python3 "$REPO/tools/timelapse/scripts/build_index.py" "$WORK/commits.txt" "$WORK/build_index.json"
python3 "$REPO/tools/timelapse/scripts/pal_index.py"
python3 "$REPO/tools/timelapse/scripts/build_union.py" "$WORK"
python3 "$REPO/tools/timelapse/scripts/build_actor_scenes.py"
python3 "$REPO/tools/timelapse/scripts/demolitions.py"
python3 "$REPO/tools/timelapse/scripts/build_endoflife.py"
python3 "$REPO/tools/timelapse/scripts/build_wildpals_draw.py"
python3 "$MAPPAL_ROOT/tools/timelapse/build_colosseum.py"

# 2. rebuild near terrain plus the 8 km far-field HLOD ring for every output.
#    The site list only exists once step 1 has written the render manifest.
paltl_sites
for base in $PALTL_SITES; do
  KEEP_MESHES=1 python3 "$REPO/tools/timelapse/scripts/build_terrain.py" "$base"
done

# 3. Start a fresh dev server only after all public assets exist. Vite snapshots
#    its public file tree at startup, so reusing an older server can silently
#    turn missing textures into the SPA HTML fallback.
PORT="${PORT:-4174}"
export PORT
VITE_LOG="$WORK/vite-timelapse.log"
"$MAPPAL_ROOT/node_modules/.bin/vite" --host 127.0.0.1 --port "$PORT" --strictPort \
  >"$VITE_LOG" 2>&1 &
VITE_PID=$!
cleanup_vite() { kill "$VITE_PID" 2>/dev/null || true; }
trap cleanup_vite EXIT
for _ in {1..120}; do
  curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1 && break
  kill -0 "$VITE_PID" 2>/dev/null || { cat "$VITE_LOG" >&2; exit 1; }
  sleep 0.5
done
curl -fsS "http://127.0.0.1:$PORT/" >/dev/null || {
  echo "MapPal dev server did not become ready" >&2
  cat "$VITE_LOG" >&2
  exit 1
}

# 4. render every base, one placed piece per frame, then encode
( cd "$WORK" && bash "$REPO/tools/timelapse/final_render_all.sh" )
cleanup_vite
trap - EXIT

# 5. publish into docs/ for GitHub Pages (mp4 must NOT be git-lfs tracked:
#    Pages serves LFS pointer files verbatim and playback breaks)
python3 "$REPO/scripts/update_timelapse.py" "$WORK/video" "$WORK/build_index.json" \
  "$MAPPAL_ROOT/public/union/manifest.json"

git add docs/timelapse/
if git diff --cached --quiet; then
  echo "[timelapse] no change to publish"
else
  git commit -q -m "timelapse: automated refresh ($(date -u +%Y-%m-%dT%H:%MZ))"
  git push -q origin main
  echo "[timelapse] published"
fi
echo "$head_now" > "$STAMP"
