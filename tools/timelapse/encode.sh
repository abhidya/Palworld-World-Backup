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
  # GitHub hard-rejects any file over 100 MB and mp4s cannot use LFS (Pages
  # serves pointer files verbatim). Re-encode oversized videos at rising CRF
  # until they fit with margin; a softer picture beats an unpushable one.
  MAX_MP4_BYTES="${MAX_MP4_BYTES:-95000000}"
  for crf in 24 28 32 36; do
    sz=$(stat -f%z "$OUT/$b.mp4" 2>/dev/null || stat -c%s "$OUT/$b.mp4")
    (( sz <= MAX_MP4_BYTES )) && break
    echo "$b: $((sz/1000000)) MB > $((MAX_MP4_BYTES/1000000)) MB cap - re-encoding at crf $crf"
    ffmpeg -y -loglevel error -framerate 30 -i "$SP/frames/$b/f_%04d.png" \
      -vf "scale=1600:1000:flags=lanczos,format=yuv420p" \
      -c:v libx264 -preset slow -crf $crf -movflags +faststart \
      "$OUT/$b.mp4"
  done
  echo "$b -> $(du -h "$OUT/$b.mp4" | cut -f1) ($n frames)"
done
