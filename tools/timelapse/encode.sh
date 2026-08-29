#!/bin/bash
SP="${PALTL_WORK:-$(pwd)}"
OUT=$SP/video; mkdir -p $OUT
for b in 07f13218 de44d9f4 16fca097 5fed0024 c0105eum; do
  n=$(ls $SP/frames/$b/*.png 2>/dev/null | wc -l)
  [ "$n" -lt 2 ] && { echo "skip $b (no frames)"; continue; }
  ffmpeg -y -loglevel error -framerate 30 -i $SP/frames/$b/f_%04d.png \
    -vf "scale=1600:1000:flags=lanczos,format=yuv420p" \
    -c:v libx264 -preset slow -crf 20 -movflags +faststart \
    $OUT/$b.mp4 && echo "$b -> $(du -h $OUT/$b.mp4 | cut -f1) ($n frames)"
done
