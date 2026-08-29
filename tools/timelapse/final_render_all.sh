#!/bin/bash
# FINAL render: every piece on its own frame (PPF=1), day/night on, no cap.
# Order: smallest base first so problems surface early.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SP="${PALTL_WORK:-$(pwd)}"
APP="${MAPPAL_ROOT:-$SP/mappal}"
export PALTL_WORK="$SP"
cd "$SP"
export BUILDOUT_HOUR=8      # declared stylistic choice: show the reconstruction in daylight
export HEADLESS=0            # final acceptance runs stay visible for review
unset MAXFRAMES             # MAXFRAMES silently raises PPF and re-chunks the order
export PPF=1

# A Vite SPA returns index.html for missing public assets, which used to let a
# long render finish with invisible geometry. Refuse to start unless every
# required generated asset family and every base input is present.
for dir in meshes terrain_meshes pal_meshes player_meshes_posed equipment_meshes_posed; do
  if [[ -z "$(find "$APP/public/$dir" -type f -name '*.glb' -print -quit 2>/dev/null)" ]]; then
    echo "missing generated GLBs: $APP/public/$dir" >&2
    exit 1
  fi
done
for tex in T_Water_01_N T_Water_N_2 T_Water_WaveIntense_N; do
  [[ -s "$APP/public/water_tex/$tex.png" ]] || {
    echo "missing water texture: $APP/public/water_tex/$tex.png" >&2
    exit 1
  }
done
for b in 5fed0024 c0105eum 16fca097 de44d9f4 07f13218; do
  for input in "union_$b.json" "times_$b.json" "terrain_$b.json"; do
    [[ -s "$APP/public/union/$input" ]] || {
      echo "missing render input: $APP/public/union/$input" >&2
      exit 1
    }
  done
done
curl -fsS "http://127.0.0.1:${PORT:-5174}/" >/dev/null || {
  echo "MapPal dev server is not ready on port ${PORT:-5174}" >&2
  exit 1
}

for b in 5fed0024 c0105eum 16fca097 de44d9f4 07f13218; do
  echo "=== $(date +%H:%M:%S) render $b ==="
  node "$HERE/timelapse.mjs" "$b" 240 2.0
  n="$(find "$SP/frames/$b" -maxdepth 1 -type f -name 'f_*.png' | wc -l | tr -d ' ')"
  (( n >= 2 )) || { echo "render $b produced only $n frames" >&2; exit 1; }
  echo "=== $(date +%H:%M:%S) done $b: $n frames ==="
done
echo "ALL RENDERED $(date +%H:%M:%S)"
bash "$HERE/encode.sh"
echo "ALL ENCODED $(date +%H:%M:%S)"
