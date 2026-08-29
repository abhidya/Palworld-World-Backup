#!/bin/bash
# Regenerate the base timelapses end to end and publish them to the command
# center. Idempotent and safe to run from cron/launchd.
#
#   tools/timelapse/refresh.sh [--force]
#
# It is deliberately NOT wired into snapshot_from_mac.py: a full render is
# ~3,800 frames and ~2.5 h, while snapshots run hourly. The staleness guard
# below is what makes it safe to schedule.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${PALTL_WORK:?set PALTL_WORK to a scratch dir holding the extracted assets}"
MAPPAL_ROOT="${MAPPAL_ROOT:-$WORK/mappal}"
MIN_NEW_SNAPSHOTS="${MIN_NEW_SNAPSHOTS:-24}"
STAMP="$WORK/.last_timelapse_commit"
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

# 2. rebuild near terrain plus the 8 km far-field HLOD ring for every output
for base in 5fed0024 c0105eum 16fca097 de44d9f4 07f13218; do
  KEEP_MESHES=1 python3 "$REPO/tools/timelapse/scripts/build_terrain.py" "$base"
done

# 3. render every base, one placed piece per frame, then encode
( cd "$WORK" && bash "$REPO/tools/timelapse/final_render_all.sh" )

# 4. publish into docs/ for GitHub Pages (mp4 must NOT be git-lfs tracked:
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
