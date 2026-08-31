#!/usr/bin/env python3
"""Keep the Palworld server actually running, and say something when it is not.

Why this exists: on 2026-08-30 a game update rewrote PalServer.sh and the server
binary as mode 644 on the macOS bind mount. start.sh could not exec them and
fell through to "Ending Server". PID 1 is init.sh, so the container stayed Up
and Docker kept forwarding 8211/udp to nothing - which players see as a
connection timeout. It stayed that way for 34 hours because every layer that
noticed told nobody: the healthcheck only set a label, and the snapshot agent
printed FATAL every 60s into an unrotated 25 MB log.

So this module does three things, in escalating order:

  1. repair    put back an execute bit an update stripped; restart a container
               whose game binary has gone missing
  2. speak     rotate the log so failures stay findable, and alert on the
               transitions (went down / came back / repair not working)
  3. escalate  when deterministic repair has not worked, ask Claude to
               diagnose - READ ONLY, see investigate() - and put its findings
               in the alert instead of leaving a human to start from scratch

Imported by snapshot_from_mac.py, which launchd already runs every 60 seconds.
Runnable directly for a one-shot check:

    python3 scripts/server_guard.py            # check + repair, print what it did
    python3 scripts/server_guard.py --status   # report only, change nothing
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVER_DIR = Path(os.environ.get("PALWORLD_SERVER_DIR", "/Users/mannybhidya/PalworldServer"))
GAME_ROOT = SERVER_DIR / "palworld"
CONTAINER = os.environ.get("PALWORLD_CONTAINER", "palworld-server")
SERVER_BINARY = "PalServer-Linux-Shipping"
API_BASE = os.environ.get("PALWORLD_API_BASE", "http://127.0.0.1:8212/v1/api")

# SteamCMD rewrites these on every game update and they can land without the
# execute bit. Anything else in the tree that is ELF or has a shebang is checked
# too - see restore_exec_bits - so a future update adding a launcher is covered.
EXEC_PATHS = (
    "PalServer.sh",
    f"Pal/Binaries/Linux/{SERVER_BINARY}",
    "Engine/Binaries/Linux/libEOSSDK-Linux-Shipping.so",
)

STATE_PATH = SERVER_DIR / ".guard_state.json"
ALERT_LOG = SERVER_DIR / "alerts.log"
LOG_PATH = Path(os.environ.get("PALWORLD_SNAPSHOT_LOG", SERVER_DIR / "snapshot.log"))
LOG_MAX_BYTES = int(os.environ.get("PALWORLD_LOG_MAX_BYTES", 5 * 1024 * 1024))
LOG_KEEP = int(os.environ.get("PALWORLD_LOG_KEEP", 3))

RESTART_COOLDOWN_S = int(os.environ.get("PALWORLD_RESTART_COOLDOWN", "600"))
# Restarts that did not bring the server back before we stop retrying blindly
# and ask for help instead.
FAILED_REPAIRS_BEFORE_ESCALATION = int(os.environ.get("PALWORLD_ESCALATE_AFTER", "3"))
CLAUDE_BIN = os.environ.get("PALWORLD_CLAUDE_BIN", "/usr/local/bin/claude")
CLAUDE_TIMEOUT_S = int(os.environ.get("PALWORLD_CLAUDE_TIMEOUT", "300"))


# --------------------------------------------------------------------------- log


def rotate_log(path: Path = LOG_PATH, max_bytes: int = LOG_MAX_BYTES,
               keep: int = LOG_KEEP) -> bool:
    """Rotate by copy-truncate, because launchd owns the file descriptor.

    launchd opens StandardOutPath once and appends forever. Renaming the file
    would leave it writing to the unlinked inode and the 'new' log would stay
    empty - which is worse than no rotation, because it looks like it works.
    Copying then truncating in place keeps the inode and the fd valid.
    """
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return False
        for n in range(keep - 1, 0, -1):
            older, newer = path.with_suffix(path.suffix + f".{n}"), path.with_suffix(path.suffix + f".{n + 1}")
            if older.exists():
                older.replace(newer)
        shutil.copy2(path, path.with_suffix(path.suffix + ".1"))
        with path.open("r+") as fh:
            fh.truncate(0)
        return True
    except OSError as e:
        print(f"[guard] log rotation failed: {e}", file=sys.stderr)
        return False


# ------------------------------------------------------------------------- state


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, indent=1))
    except OSError as e:
        print(f"[guard] could not persist state: {e}", file=sys.stderr)


# ------------------------------------------------------------------------ alerts


def alert(subject: str, body: str = "") -> None:
    """Say something a human will actually encounter.

    Every alert lands in alerts.log (small, unlike snapshot.log, so it stays
    readable) and as a macOS notification. A Discord webhook is used when
    DISCORD_WEBHOOK_URL is set - the server container already speaks Discord, so
    that is the natural place to converge if you want phone alerts.
    """
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')}  {subject}"
    try:
        with ALERT_LOG.open("a") as fh:
            fh.write(line + ("\n" + body + "\n" if body else "\n"))
    except OSError:
        pass
    print(f"[guard] ALERT {subject}", file=sys.stderr)

    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification {json.dumps(body[:200] or subject)} '
             f'with title "Palworld" subtitle {json.dumps(subject)}'],
            capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass

    hook = os.environ.get("DISCORD_WEBHOOK_URL")
    if hook:
        payload = json.dumps({"content": f"**{subject}**\n{body[:1800]}"}).encode()
        req = urllib.request.Request(hook, data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except (urllib.error.URLError, OSError) as e:
            print(f"[guard] discord alert failed: {e}", file=sys.stderr)


# ------------------------------------------------------------------------ probes


def _docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def container_running() -> bool | None:
    try:
        out = _docker("inspect", "-f", "{{.State.Running}}", CONTAINER, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() == "true"


def _pgrep(pattern: str) -> bool | None:
    try:
        out = _docker("exec", CONTAINER, "pgrep", "-f", pattern, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(out.stdout.strip())


def server_process_running() -> bool | None:
    return _pgrep(SERVER_BINARY)


def update_in_progress() -> bool | None:
    """Is SteamCMD installing right now?

    An update legitimately shuts the server down and then downloads for minutes.
    Restarting the container in that window would kill the install mid-write, so
    the guard must hold off rather than treat a missing binary as a fault.
    """
    return _pgrep("steamcmd")


def api_reachable() -> bool:
    try:
        urllib.request.urlopen(f"{API_BASE}/info", timeout=5).close()
        return True
    except urllib.error.HTTPError:
        return True          # 401 means it answered; auth is not our business here
    except (urllib.error.URLError, OSError):
        return False


def restore_exec_bits() -> list[str]:
    """Put back execute bits an update stripped. Returns what it repaired."""
    repaired = []
    candidates = [GAME_ROOT / rel for rel in EXEC_PATHS]
    # Also catch launchers a future update might add, without walking the whole
    # 5 GB install: only the directories that hold executables today.
    for d in (GAME_ROOT, GAME_ROOT / "Pal/Binaries/Linux", GAME_ROOT / "Engine/Binaries/Linux"):
        try:
            candidates.extend(p for p in d.iterdir() if p.is_file() and p.suffix in ("", ".sh"))
        except OSError:
            continue
    for path in dict.fromkeys(candidates):
        try:
            if not path.is_file():
                continue
            mode = path.stat().st_mode
            if mode & 0o111 == 0o111:
                continue
            with path.open("rb") as fh:
                magic = fh.read(4)
            if magic[:4] != b"\x7fELF" and magic[:2] != b"#!":
                continue
            path.chmod(mode | 0o111)
            repaired.append(str(path.relative_to(GAME_ROOT)))
        except OSError:
            continue
    return repaired


# ----------------------------------------------------------------------- escalate


def investigate() -> str:
    """Ask Claude to diagnose when deterministic repair has not worked.

    READ ONLY on purpose. It gets no write tools, so it cannot change the server,
    the saves or the config - it reads logs and state and reports. Handing an
    autonomous agent write access to a live game server to 'fix itself' trades a
    known outage for an unknown blast radius, which is a bad trade at 3am. The
    output goes into the alert so a human starts from a diagnosis instead of a
    blank terminal.
    """
    if not Path(CLAUDE_BIN).exists():
        return "(claude CLI not installed; no automated diagnosis)"
    prompt = (
        f"The Palworld dedicated server in docker container '{CONTAINER}' is down and "
        f"automated repair (chmod +x on the game binaries, docker restart) has not "
        f"brought it back after several attempts.\n\n"
        f"Diagnose the root cause. Investigate with read-only commands only:\n"
        f"  docker logs --tail 200 {CONTAINER}\n"
        f"  docker inspect {CONTAINER}\n"
        f"  docker exec {CONTAINER} ps aux\n"
        f"  ls -la {GAME_ROOT} {GAME_ROOT}/Pal/Binaries/Linux\n"
        f"  tail -100 {LOG_PATH}\n\n"
        f"Do NOT modify, restart, delete or chmod anything. Report in under 200 words: "
        f"what is broken, the evidence, and the exact command a human should run to fix it."
    )
    try:
        out = subprocess.run(
            [CLAUDE_BIN, "-p", prompt,
             "--allowedTools", "Bash(docker logs:*),Bash(docker inspect:*),"
                               "Bash(docker exec:*),Bash(ls:*),Bash(tail:*),Read"],
            capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_S)
        return (out.stdout or out.stderr or "(no output)").strip()
    except subprocess.TimeoutExpired:
        return f"(diagnosis timed out after {CLAUDE_TIMEOUT_S}s)"
    except (OSError, subprocess.SubprocessError) as e:
        return f"(diagnosis failed: {e})"


# -------------------------------------------------------------------------- main


def ensure_server_running(repair: bool = True) -> dict:
    """Check, repair, and alert on transitions. Returns what it observed."""
    rotate_log()
    state = _load_state()
    was_down = bool(state.get("down"))
    report: dict = {"container": None, "server": None, "action": None}

    running = container_running()
    report["container"] = running
    if running is None:
        print("[guard] cannot reach docker; skipping", file=sys.stderr)
        return report
    if not running:
        # Compose's restart policy owns a stopped container, and a stopped
        # container may be a deliberate maintenance window.
        return report

    if repair:
        repaired = restore_exec_bits()
        if repaired:
            report["repaired"] = repaired
            print(f"[guard] restored execute bit on: {', '.join(repaired)}")
            alert("execute bits restored after game update", "\n".join(repaired))

    alive = server_process_running()
    report["server"] = alive
    if alive is not False:
        if was_down:
            alert("server is back up", f"{SERVER_BINARY} running; API reachable="
                                       f"{api_reachable()}")
        _save_state({"down": False, "failed_repairs": 0})
        return report

    if update_in_progress():
        # Legitimately absent: SteamCMD shut the server down to install. Leave it.
        report["action"] = "waiting-for-update"
        print("[guard] update in progress; not restarting")
        _save_state({**state, "down": True, "reason": "update"})
        return report

    if not repair:
        report["action"] = "would-restart"
        return report

    now = time.time()
    last = float(state.get("last_restart", 0))
    failures = int(state.get("failed_repairs", 0))
    if now - last < RESTART_COOLDOWN_S:
        report["action"] = "cooling-down"
        print(f"[guard] {SERVER_BINARY} down, restarted recently; waiting", file=sys.stderr)
        return report

    if not was_down:
        alert("server is DOWN", f"{SERVER_BINARY} not running inside {CONTAINER}; restarting")

    failures += 1
    print(f"[guard] {SERVER_BINARY} not running; restarting {CONTAINER} "
          f"(attempt {failures})", file=sys.stderr)
    try:
        rc = _docker("restart", CONTAINER, timeout=120)
        report["action"] = "restarted" if rc.returncode == 0 else "restart-failed"
        if rc.returncode != 0:
            print(f"[guard] restart FAILED: {rc.stderr.strip()}", file=sys.stderr)
    except (OSError, subprocess.SubprocessError) as e:
        report["action"] = "restart-failed"
        print(f"[guard] restart failed: {e}", file=sys.stderr)

    state = {"down": True, "last_restart": now, "failed_repairs": failures}
    if failures >= FAILED_REPAIRS_BEFORE_ESCALATION and not state.get("escalated"):
        state["escalated"] = True
        alert(f"server still down after {failures} repair attempts - diagnosing",
              investigate())
    _save_state(state)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true", help="report only, change nothing")
    args = ap.parse_args()
    report = ensure_server_running(repair=not args.status)
    print(json.dumps({**report, "api": api_reachable()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
