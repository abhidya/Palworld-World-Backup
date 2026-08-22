#!/bin/bash
# FINAL render: every piece on its own frame (PPF=1), day/night on, no cap.
# Order: smallest base first so problems surface early.
SP="${PALTL_WORK:?set PALTL_WORK to your timelapse work directory}"
cd $SP
export BUILDOUT_HOUR=8      # declared stylistic choice: show the reconstruction in daylight
unset MAXFRAMES             # MAXFRAMES silently raises PPF and re-chunks the order
export PPF=1
for b in 5fed0024 16fca097 de44d9f4 07f13218; do
  echo "=== $(date +%H:%M:%S) render $b ==="
  rm -rf $SP/frames/$b
  node timelapse.mjs $b 240 2.0 || echo "!! $b FAILED"
  echo "=== $(date +%H:%M:%S) done $b: $(ls $SP/frames/$b/*.png 2>/dev/null | wc -l) frames ==="
done
echo "ALL RENDERED $(date +%H:%M:%S)"
bash $SP/encode.sh
echo "ALL ENCODED $(date +%H:%M:%S)"
