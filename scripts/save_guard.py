#!/usr/bin/env python3
"""Notice when the world loses content, instead of finding out by logging in.

On 2026-08-29 the live world lost all 3 base camps and 3,664 of its 8,103 map
objects. Nothing caught it. The container healthcheck was green the whole time,
because a server happily hosting an empty world is a healthy server. The file
did not vanish or truncate to zero either - it went from 2.1 MB to 1.78 MB,
which no size threshold would flag. It was found two days later by a human
logging in and seeing bare ground.

Size and liveness cannot see content loss. This decodes the save and compares
what is inside it against the last known-good counts:

  BaseCampSaveData          bases outright disappearing is the signature failure
  MapObjectSaveData         every placed structure; the 45% drop that happened
  CharacterSaveParameterMap players and pals
  GroupSaveDataMap          guilds - losing these detaches bases from owners

On regression it alerts and STOPS advancing the known-good marker, so the good
reference is never overwritten by the bad state. It deliberately does not
auto-restore: rolling a live world back is a decision with real data loss on
the other side of it, and the 2026-08-29 recovery only cost minutes once
somebody knew. Knowing is the part that was missing.

    python3 scripts/save_guard.py            # check, alert on regression
    python3 scripts/save_guard.py --status   # print counts, change nothing
    python3 scripts/save_guard.py --accept   # adopt current counts as baseline
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from server_guard import alert  # noqa: E402

SERVER_DIR = Path(os.environ.get("PALWORLD_SERVER_DIR", "/Users/mannybhidya/PalworldServer"))
WORLD_ID = os.environ.get("PALWORLD_WORLD_ID", "64EE4B2C4C81F4912BF109850820D9BA")
WORLD_DIR = SERVER_DIR / "palworld" / "Pal" / "Saved" / "SaveGames" / "0" / WORLD_ID
STATE_PATH = SERVER_DIR / ".save_guard.json"
GOOD_DIR = SERVER_DIR / "last-known-good"

# palworld_save_tools and its ooz codec only exist in the dashboard venv, and
# launchd runs the caller under /usr/bin/python3 - so decoding is shelled out,
# the same way snapshot_from_mac.py handles every other .sav operation.
VENV_PY = Path(os.environ.get(
    "PALWORLD_VENV_PY", SERVER_DIR / "dashboard-venv" / "bin" / "python"))

# Decoding costs ~4s and inflates to ~36 MB. The caller runs every 60s, so only
# look when the save actually changed, and not more than this often.
CHECK_INTERVAL_S = int(os.environ.get("PALWORLD_SAVE_CHECK_INTERVAL", "600"))
DECODE_TIMEOUT_S = int(os.environ.get("PALWORLD_DECODE_TIMEOUT", "120"))

# Counts that must never fall. Bases and guilds are absolute - losing one is
# always a fault. Object and character counts breathe a little as loot despawns
# and pals wander, so they get a tolerance band rather than a hard floor.
ABSOLUTE = ("BaseCampSaveData", "GroupSaveDataMap")
TOLERANCE = {
    "MapObjectSaveData": float(os.environ.get("PALWORLD_TOL_OBJECTS", "0.05")),
    "CharacterSaveParameterMap": float(os.environ.get("PALWORLD_TOL_CHARS", "0.10")),
}

_COUNTER = r'''
import json, sys
from palworld_save_tools.gvas import GvasFile
from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES
from palworld_save_tools.palsav import decompress_sav_to_gvas

gvas, _ = decompress_sav_to_gvas(open(sys.argv[1], "rb").read())
g = GvasFile.read(gvas, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES, allow_nan=True)
w = g.properties["worldSaveData"]["value"]

def count(key):
    v = w.get(key)
    if v is None:
        return 0                      # absent and empty are the same fault here
    val = v.get("value", v)
    if isinstance(val, dict) and "values" in val:
        val = val["values"]
    try:
        return len(val)
    except TypeError:
        return -1                     # unexpected shape; never treat as healthy

keys = ("BaseCampSaveData", "MapObjectSaveData", "GroupSaveDataMap",
        "CharacterSaveParameterMap", "ItemContainerSaveData")
print("COUNTS " + json.dumps({k: count(k) for k in keys}))
'''


def counts(path: Path) -> dict[str, int] | None:
    """Decode a Level.sav and count what is in it. None if it cannot be read."""
    if not VENV_PY.exists():
        print(f"[save-guard] {VENV_PY} missing; cannot decode saves", file=sys.stderr)
        return None
    try:
        out = subprocess.run([str(VENV_PY), "-c", _COUNTER, str(path)],
                             capture_output=True, text=True, timeout=DECODE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[save-guard] decode failed: {e}", file=sys.stderr)
        return None
    for line in out.stdout.splitlines():
        if line.startswith("COUNTS "):
            return json.loads(line[len("COUNTS "):])
    # A save that cannot be parsed is itself a finding, not a reason to be quiet.
    print(f"[save-guard] could not parse save: {out.stderr.strip()[:300]}", file=sys.stderr)
    return None


def regressions(baseline: dict[str, int], now: dict[str, int]) -> list[str]:
    """What got materially worse. Empty list means the world is intact."""
    bad = []
    for key in ABSOLUTE:
        was, is_ = baseline.get(key, 0), now.get(key, 0)
        if is_ < was:
            bad.append(f"{key}: {was} -> {is_}")
    for key, tol in TOLERANCE.items():
        was, is_ = baseline.get(key, 0), now.get(key, 0)
        if was and is_ < was * (1 - tol):
            pct = (was - is_) / was * 100
            bad.append(f"{key}: {was} -> {is_} ({pct:.0f}% lost)")
    if any(v == -1 for v in now.values()):
        bad.append("save decoded to an unexpected shape")
    return bad


def _preserve_good(now: dict[str, int]) -> None:
    """Keep a copy of the world while it is verified intact.

    Recovery on 2026-08-29 worked only because a daily backup happened to
    predate the loss - a 15 hour window. This narrows that to one check
    interval, and is only ever refreshed from a save that just passed.
    """
    try:
        tmp = GOOD_DIR.with_name(GOOD_DIR.name + ".new")
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(WORLD_DIR, tmp)
        (tmp / "COUNTS.json").write_text(json.dumps(
            {"counts": now, "captured": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, indent=1))
        shutil.rmtree(GOOD_DIR, ignore_errors=True)
        tmp.replace(GOOD_DIR)
    except OSError as e:
        print(f"[save-guard] could not refresh known-good copy: {e}", file=sys.stderr)


def check(force: bool = False, accept: bool = False) -> dict:
    level = WORLD_DIR / "Level.sav"
    if not level.exists():
        return {"skipped": "no Level.sav"}

    state = {}
    try:
        state = json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        pass

    mtime = int(level.stat().st_mtime)
    now_ts = time.time()
    if not force:
        if mtime == state.get("last_mtime"):
            return {"skipped": "save unchanged"}
        if now_ts - float(state.get("last_check", 0)) < CHECK_INTERVAL_S:
            return {"skipped": "checked recently"}

    now = counts(level)
    if now is None:
        return {"skipped": "decode failed"}

    baseline = state.get("baseline")
    if accept or not baseline:
        _preserve_good(now)
        _save({"baseline": now, "last_check": now_ts, "last_mtime": mtime,
               "alerted": False})
        print(f"[save-guard] baseline set: {json.dumps(now)}")
        return {"baseline": now, "action": "baseline-set"}

    bad = regressions(baseline, now)
    if not bad:
        # Healthy: adopt growth as the new baseline and refresh the good copy.
        merged = {k: max(baseline.get(k, 0), v) for k, v in now.items()}
        _preserve_good(now)
        _save({"baseline": merged, "last_check": now_ts, "last_mtime": mtime,
               "alerted": False})
        return {"counts": now, "action": "ok"}

    # Regression: hold the known-good copy and the baseline exactly where they
    # are, so nothing here overwrites the last verified state.
    detail = ("\n".join(bad) +
              f"\n\nbaseline: {json.dumps(baseline)}"
              f"\ncurrent:  {json.dumps(now)}"
              f"\n\nlast known-good world: {GOOD_DIR}"
              f"\nThe server is still running and will keep saving over the live "
              f"world. Stop it before restoring:\n"
              f"  docker stop palworld-server")
    if not state.get("alerted"):
        alert("WORLD CONTENT LOSS detected", detail)
    _save({**state, "last_check": now_ts, "last_mtime": mtime, "alerted": True,
           "regression": bad})
    return {"counts": now, "action": "regression", "regressions": bad}


def _save(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, indent=1))
    except OSError as e:
        print(f"[save-guard] could not persist state: {e}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true", help="print counts, change nothing")
    ap.add_argument("--accept", action="store_true", help="adopt current counts as baseline")
    args = ap.parse_args()
    if args.status:
        print(json.dumps(counts(WORLD_DIR / "Level.sav"), indent=1))
        return 0
    print(json.dumps(check(force=True, accept=args.accept), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
