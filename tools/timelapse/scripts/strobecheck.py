#!/usr/bin/env python3
"""Prove a render does not strobe.

Two independent measurements of the same thing:
  1. INTENT  - the per-frame change in the lighting hour actually handed to
     setDaylight, read from the render's own plan.json (litHour). This is what
     timelapse.mjs clamped, so it is where a clamp bug would show up.
  2. RESULT  - the per-frame change in mean picture luminance (ffmpeg
     signalstats YAVG) across the rendered PNGs. This is what an eye receives,
     and it catches anything the hour-space measurement would miss.

Photosensitive-seizure guidance is about large luminance swings repeated at a
few hertz. A timelapse day/night cycle is safe when it reads as a slow drift:
one full cycle spread over hundreds of frames, and no fast oscillation.

Usage: strobecheck.py <frames_dir> [fps]
"""
import json, os, subprocess, sys, statistics

d = sys.argv[1]
fps = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0


def circ(a, b):
    """signed shortest distance from b to a on a 24 h clock"""
    x = (a - b) % 24.0
    return x - 24.0 if x > 12.0 else x


print(f"== {d}")
plan_p = os.path.join(d, "plan.json")
if os.path.exists(plan_p):
    plan = json.load(open(plan_p))
    lit = [p.get("litHour") for p in plan]
    ds = [abs(circ(lit[i], lit[i - 1])) for i in range(1, len(lit))
          if lit[i] is not None and lit[i - 1] is not None]
    if ds:
        s = sorted(ds)
        mx = s[-1]
        med = s[len(s) // 2]
        p99 = s[int(len(s) * 0.99)]
        cyc = (24.0 / mx) if mx > 0 else float("inf")
        print(f"  lighting hour delta/frame: median {med:.4f}  p99 {p99:.4f}  max {mx:.4f} h")
        print(f"  fastest full day/night cycle: {cyc:.0f} frames = {cyc/fps:.1f} s"
              f"   {'OK' if cyc >= 239.9 else '*** TOO FAST ***'}")
    # how much real in-game time is being skipped, for honesty
    gd = [p.get("gameDay") for p in plan if p.get("gameDay") is not None]
    if gd:
        print(f"  in-game days covered: {min(gd)} .. {max(gd)} ({max(gd)-min(gd)} days)")
else:
    print("  (no plan.json)")

pngs = sorted(f for f in os.listdir(d) if f.startswith("f_") and f.endswith(".png"))
if not pngs:
    print("  (no frames)")
    sys.exit(0)
first = int(pngs[0][2:6])
out = subprocess.run(
    ["ffmpeg", "-hide_banner", "-loglevel", "info", "-start_number", str(first),
     "-i", os.path.join(d, "f_%04d.png"),
     "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
     "-f", "null", "-"],
    capture_output=True, text=True).stderr
y = [float(l.split("=")[-1]) for l in out.splitlines() if "YAVG" in l]
if len(y) < 2:
    print("  (luminance probe produced nothing)")
    sys.exit(0)
dy = [abs(y[i] - y[i - 1]) for i in range(1, len(y))]
s = sorted(dy)
# 0-255 scale. Express the swing as a percentage of full range.
print(f"  frames measured: {len(y)}   mean luminance {statistics.mean(y):.1f}/255"
      f"  range {min(y):.1f}..{max(y):.1f}")
print(f"  frame-to-frame luminance swing: median {s[len(s)//2]:.3f}  p99 {s[int(len(s)*0.99)]:.3f}"
      f"  max {s[-1]:.3f}  ({100*s[-1]/255:.2f}% of full scale)")
# Oscillation only matters at amplitude: a 0.1/255 wobble from the camera
# rotating past a bright wall is not flicker, a 20/255 reversal is. So count
# reversals only among steps whose swing is perceptible (>=2% of full scale),
# which is the pattern photosensitivity guidance is actually about.
BIG = 0.02 * 255
sign = [1 if y[i] > y[i - 1] else (-1 if y[i] < y[i - 1] else 0) for i in range(1, len(y))]
flips = sum(1 for i in range(1, len(sign)) if sign[i] and sign[i - 1] and sign[i] != sign[i - 1])
big = [i for i in range(len(dy)) if dy[i] >= BIG]
bigflips = sum(1 for k in range(1, len(big))
               if big[k] - big[k - 1] <= 3 and sign[big[k]] != sign[big[k - 1]])
print(f"  luminance direction reversals (any size): {flips} over {len(sign)} steps"
      f"  ({flips/len(sign)*fps:.2f}/s) - dominated by camera motion, see next line")
print(f"  PERCEPTIBLE swings (>={BIG:.1f}/255 = 2% of scale): {len(big)} of {len(dy)} steps;"
      f"  fast reversals among them: {bigflips}"
      f"   {'OK' if bigflips == 0 else '*** FLICKER ***'}")
# The headline number, matching how the DAYNIGHT=0 stopgap was measured:
# large frame-to-frame luminance transitions per second of finished video.
print(f"  LARGE TRANSITIONS PER SECOND: {len(big)/len(dy)*fps:.2f}"
      f"   (target: near zero, as with the day/night-off stopgap)")
