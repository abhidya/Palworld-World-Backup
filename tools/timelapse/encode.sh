#!/bin/bash
# Encode each site's rendered frames into the mp4 the dashboard plays.
# Sites come from scripts/timelapse_sites.py (PALTL_BASES/PALTL_SKIP apply).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/sites.sh"
paltl_sites
SP="${PALTL_WORK:-$(pwd)}"
OUT="$SP/video"; mkdir -p "$OUT"
for b in $PALTL_SITES; do
  n="$(find "$SP/frames/$b" -maxdepth 1 -type f -name 'f_*.png' 2>/dev/null | wc -l | tr -d ' ')"
  (( n >= 2 )) || { echo "cannot encode $b: only $n frames" >&2; exit 1; }
  ffmpeg -y -loglevel error -framerate 30 -i "$SP/frames/$b/f_%04d.png" \
    -vf "scale=1600:1000:flags=lanczos,format=yuv420p" \
    -c:v libx264 -preset slow -crf 20 -movflags +faststart \
    "$OUT/$b.mp4"
  [[ -s "$OUT/$b.mp4" ]] || { echo "empty encoded video: $OUT/$b.mp4" >&2; exit 1; }
  echo "$b -> $(du -h "$OUT/$b.mp4" | cut -f1) ($n frames)"
done
