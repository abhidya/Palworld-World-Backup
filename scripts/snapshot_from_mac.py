#!/usr/bin/env python3
"""Take a snapshot of the live Diva Booties world (running in Docker on this
Mac) and commit+push it to the Palworld-World-Backup repo.

This is the Mac equivalent of the Windows rig's `rig.ps1 palworld
backup-status` snapshot flow: same metadata/snapshot.json schema, same
secret-scan-before-commit safety net, same critical-file hashing.

    python3 scripts/snapshot_from_mac.py

Reads ADMIN_PASSWORD, SERVER_PASSWORD from ../PalworldServer/.env (never
committed). Never pass secrets on the command line.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVER_DIR = Path("/Users/mannybhidya/PalworldServer")
ENV_FILE = SERVER_DIR / ".env"
SAVE_ROOT = SERVER_DIR / "palworld" / "Pal" / "Saved" / "SaveGames" / "0"
LIVE_CONFIG_DIR = SERVER_DIR / "palworld" / "Pal" / "Saved" / "Config" / "LinuxServer"
WORLD_ID = "64EE4B2C4C81F4912BF109850820D9BA"
API_BASE = "http://127.0.0.1:8212/v1/api"
PLACEHOLDER = "<REDACTED:supplied-locally>"
REDACTED = f'"{PLACEHOLDER}"'
SECRET_KEYS = ("AdminPassword", "ServerPassword", "RCONPassword", "BanListURL")
# WorldOption.sav keeps its own copy of the credentials the .ini holds, so the
# repo copy gets the same placeholder treatment.
SAVE_SECRET_KEYS = ("AdminPassword", "ServerPassword", "BanListURL")

# palworld_save_tools/ooz only exist in the dashboard venv, and launchd runs
# this script under /usr/bin/python3, so every .sav operation is shelled out to
# that interpreter. The library logs to stdout, so the helper never writes
# results there - they go to files, or (for secrets) come in over stdin.
SAVE_TOOLS_PYTHON = SERVER_DIR / "dashboard-venv" / "bin" / "python"
SAV_HELPER = r'''
import json
import sys

from palworld_save_tools.gvas import GvasFile
from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES
from palworld_save_tools.palsav import decompress_sav_to_gvas, compress_gvas_to_sav

mode = sys.argv[1]

if mode == "sanitize":
    src, dest, placeholder, keys = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]
    raw = open(src, "rb").read()
    if raw[8:11] != b"PlZ":
        sys.exit("refusing to re-encode %r save %s" % (raw[8:11], src))
    gvas_bytes, _ = decompress_sav_to_gvas(raw)
    gvas = GvasFile.read(gvas_bytes, PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES)
    settings = gvas.properties["OptionWorldData"]["value"]["Settings"]["value"]
    missing = [k for k in keys if k not in settings]
    if missing:
        sys.exit("settings keys absent from save: " + ",".join(missing))
    for key in keys:
        settings[key]["value"] = placeholder
    # compress_gvas_to_sav() picks its compressor from save_type, and this build
    # reads 0x31 as Oodle (whose compress() is unavailable) even for a PlZ file.
    # 0x32, double zlib, is the PlZ mode its writer supports and the game reads.
    blob = compress_gvas_to_sav(gvas.write(PALWORLD_CUSTOM_PROPERTIES), 0x32)
    # Prove the re-encode is readable and actually redacted before it lands.
    check = GvasFile.read(
        decompress_sav_to_gvas(blob)[0], PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES
    )
    written = check.properties["OptionWorldData"]["value"]["Settings"]["value"]
    if any(written[k]["value"] != placeholder for k in keys):
        sys.exit("re-encoded save did not keep the placeholders")
    with open(dest, "wb") as fh:
        fh.write(blob)
    sys.exit(0)

if mode == "scan":
    result, paths = sys.argv[2], sys.argv[3:]
    probes = []
    for token in sys.stdin.read().split():
        secret = bytes.fromhex(token)
        probes += [secret, secret.decode("utf-8", "replace").encode("utf-16-le")]
    leaks, errors = [], []
    for path in paths:
        try:
            plain, _ = decompress_sav_to_gvas(open(path, "rb").read())
        except Exception as e:  # a save we cannot read is the caller's problem
            errors.append([path, repr(e)[:200]])
            continue
        if any(p in plain for p in probes):
            leaks.append(path)
    with open(result, "w") as fh:
        json.dump({"leaks": leaks, "errors": errors}, fh)
    sys.exit(0)

sys.exit("unknown helper mode " + mode)
'''


def load_env() -> dict[str, str]:
    if not ENV_FILE.is_file():
        sys.exit(f"FATAL: {ENV_FILE} not found - can't read ADMIN_PASSWORD")
    out = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def api_get(path: str, admin_password: str):
    req = urllib.request.Request(f"{API_BASE}{path}")
    token = base64.b64encode(f"admin:{admin_password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def api_post(path: str, admin_password: str):
    req = urllib.request.Request(f"{API_BASE}{path}", data=b"{}", method="POST")
    token = base64.b64encode(f"admin:{admin_password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def sha256(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            d.update(block)
    return d.hexdigest()


def run_sav_helper(args: list[str], stdin: str = "") -> None:
    """Run SAV_HELPER under the save-tools interpreter. Raises RuntimeError on
    failure; stdout is discarded because the library logs onto it."""
    if not SAVE_TOOLS_PYTHON.is_file():
        raise RuntimeError(f"save-tools interpreter missing: {SAVE_TOOLS_PYTHON}")
    proc = subprocess.run(
        [str(SAVE_TOOLS_PYTHON), "-c", SAV_HELPER, *args],
        input=stdin, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-400:] or f"helper exit {proc.returncode}")


def sanitize_world_option(src: Path, dest: Path) -> None:
    """Write the repo's copy of WorldOption.sav with the credential fields
    replaced by the same placeholder the .ini uses. `src` is the live save and
    is only ever read - the sanitized re-encode lands on `dest`."""
    tmp = dest.parent / f".{dest.name}.sanitizing"
    try:
        run_sav_helper(["sanitize", str(src), str(tmp), PLACEHOLDER, *SAVE_SECRET_KEYS])
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def scan_saves(paths: list[Path], secret_values: set[str]) -> tuple[list[str], list[str]]:
    """Decompress .sav files and look for real secret values in the plaintext.
    Secrets are handed over on stdin, never on the command line. Returns
    (saves that leaked, notes about saves that could not be decoded)."""
    if not paths or not secret_values:
        return [], []
    with tempfile.TemporaryDirectory() as tmpdir:
        result = Path(tmpdir) / "scan.json"
        probes = "\n".join(v.encode().hex() for v in sorted(secret_values))
        run_sav_helper(["scan", str(result), *[str(p) for p in paths]], stdin=probes)
        data = json.loads(result.read_text())
    return data["leaks"], [f"{p}: {e}" for p, e in data["errors"]]


def sanitize_ini(text: str, secret_values: set[str]) -> str:
    for key in SECRET_KEYS:
        text = re.sub(rf'{key}="[^"]*"', f"{key}={REDACTED}", text)
    for value in secret_values:
        if value:
            text = text.replace(value, "<REDACTED:supplied-locally>")
    return text


def reason_for(players_now: int, players_prev: int | None) -> str:
    if players_prev is None:
        return f"periodic during active play ({players_now} players seen)" if players_now else "periodic snapshot"
    if players_now > players_prev:
        return f"player logged on ({players_prev} -> {players_now})"
    if players_now < players_prev:
        return f"player logged off ({players_prev} -> {players_now})"
    if players_now:
        return f"periodic during active play ({players_now} players seen)"
    return "periodic idle (server up, 0 players)"


def main() -> int:
    env = load_env()
    admin_password = env.get("ADMIN_PASSWORD", "")
    server_password = env.get("SERVER_PASSWORD", "")
    if not admin_password:
        sys.exit("FATAL: ADMIN_PASSWORD missing from .env")

    live_world = SAVE_ROOT / WORLD_ID
    if not live_world.is_dir():
        sys.exit(f"FATAL: live world dir not found: {live_world}")

    try:
        info = api_get("/info", admin_password)
        players = api_get("/players", admin_password)
        players_now = len(players.get("players", []))
        server_version = info.get("version", "unknown")
        server_name = info.get("servername", "Diva Booties")
        worldguid = info.get("worldguid", WORLD_ID)
    except urllib.error.URLError as e:
        sys.exit(f"FATAL: REST API unreachable ({e}); is the container up?")

    if worldguid != WORLD_ID:
        sys.exit(f"FATAL: live worldguid {worldguid} != expected {WORLD_ID}; refusing to snapshot")

    manifest_path = REPO / "metadata" / "snapshot.json"
    prev = json.loads(manifest_path.read_text()) if manifest_path.is_file() else None
    players_prev = prev.get("players_at_snapshot") if prev else None

    # The launchd job polls every 60s so join/leave is caught within a
    # minute, but a forced save + commit every poll bloats the repo
    # (~1.4k commits and ~3GB of LFS objects per day). Only snapshot when
    # the player count changed or the last snapshot has gone stale.
    ACTIVE_CEILING = 60 * 60       # active play: snapshot hourly (logoff catches session end)
    IDLE_CEILING = 24 * 60 * 60    # idle server: daily heartbeat proves the pipeline works
    age = None
    if prev and players_prev is not None and players_now == players_prev:
        try:
            from datetime import datetime
            prev_ts = datetime.strptime(prev["snapshot_timestamp"], "%Y-%m-%dT%H:%M:%S%z")
            age = time.time() - prev_ts.timestamp()
        except (KeyError, ValueError):
            pass  # unreadable manifest timestamp -> snapshot to be safe
        if age is not None:
            if players_now and age < ACTIVE_CEILING:
                print(f"No player change, last snapshot {age/60:.1f} min ago; skipping.")
                return 0
            if not players_now and age < IDLE_CEILING:
                print(f"Server idle, last snapshot {age/3600:.1f} h ago; skipping.")
                return 0

    reason = reason_for(players_now, players_prev)
    if age is not None and not players_now and age >= IDLE_CEILING:
        reason = f"idle ceiling: server up but unplayed for {age/3600:.1f} h"

    try:
        api_post("/save", admin_password)
        time.sleep(2)  # let the flush land on disk before we copy
    except urllib.error.URLError as e:
        print(f"WARNING: could not force-save via REST API ({e}); snapshotting on-disk state as-is", file=sys.stderr)

    dest_world = REPO / "world" / "current"
    dest_world.mkdir(parents=True, exist_ok=True)
    for name in ("Level.sav", "LevelMeta.sav"):
        shutil.copy2(live_world / name, dest_world / name)
    # WorldOption.sav carries AdminPassword/ServerPassword/BanListURL in
    # plaintext, so the repo gets a redacted re-encode rather than a copy. If
    # that fails we abort: better no snapshot than an unsanitized one.
    try:
        sanitize_world_option(live_world / "WorldOption.sav", dest_world / "WorldOption.sav")
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        sys.exit(f"FATAL: could not sanitize WorldOption.sav ({e}); refusing to snapshot")
    dest_players = dest_world / "Players"
    dest_players.mkdir(exist_ok=True)
    live_players = live_world / "Players"
    if live_players.is_dir():
        for f in live_players.glob("*.sav"):
            shutil.copy2(f, dest_players / f.name)

    ini_src = LIVE_CONFIG_DIR / "PalWorldSettings.ini"
    if ini_src.is_file():
        sanitized = sanitize_ini(ini_src.read_text(), {admin_password, server_password})
        (REPO / "server-config" / "PalWorldSettings.ini").write_text(sanitized)
    for name in ("Engine.ini", "GameUserSettings.ini"):
        src = LIVE_CONFIG_DIR / name
        if src.is_file():
            shutil.copy2(src, REPO / "server-config" / name)

    critical = {}
    for name in ("Level.sav", "LevelMeta.sav", "WorldOption.sav"):
        critical[name] = sha256(dest_world / name)
    player_hashes = {}
    all_hashes = dict(critical)
    total_bytes = sum((dest_world / n).stat().st_size for n in critical)
    file_count = len(critical)
    for f in sorted(dest_players.glob("*.sav")):
        h = sha256(f)
        rel = f"Players\\{f.name}"
        player_hashes[rel] = h
        all_hashes[rel] = h
        total_bytes += f.stat().st_size
        file_count += 1

    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    tag = f"snapshot/{time.strftime('%Y%m%d-%H%M%S')}"
    manifest = {
        "snapshot_timestamp": now,
        "reason": reason,
        "world_id": WORLD_ID,
        "world_id_proof": f"live server /v1/api/info worldguid={worldguid}",
        "source_path": str(live_world),
        "palserver_exe": "docker: thijsvanloef/palworld-server-docker (container palworld-server)",
        "server_version": server_version,
        "server_name": server_name,
        "server_running": True,
        "players_at_snapshot": players_now,
        "snapshot_boundary": f"server running ({players_now} players); REST /v1/api/save accepted",
        "file_count": file_count,
        "total_bytes": total_bytes,
        "critical_file_hashes": critical,
        "player_save_hashes": player_hashes,
        "all_file_hashes": all_hashes,
        "config_redacted_keys": ["AdminPassword", "BanListURL", "ServerPassword"],
        "git_commit": None,
        "git_commit_note": f"see tag {tag} and rig state palworld-backup.json",
        "snapshot_tag": tag,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    # Safety net: refuse to commit if any real secret value leaked anywhere in the staged tree.
    # .sav files are compressed GVAS, so they are decoded and scanned in one
    # batched helper run; a save that will not decode cannot be proven clean,
    # so it blocks the commit instead of slipping through unread.
    secret_values = {v for v in (admin_password, server_password) if v}
    saves = []
    for path in (REPO / "world", REPO / "server-config", manifest_path):
        for f in ([path] if path.is_file() else path.rglob("*")):
            if not f.is_file():
                continue
            if f.suffix == ".sav":
                saves.append(f)
                continue
            text = f.read_text(errors="ignore")
            for secret in secret_values:
                if secret in text:
                    sys.exit(f"FATAL: secret leaked into {f}; aborting commit")

    try:
        leaked, undecodable = scan_saves(saves, secret_values)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as e:
        sys.exit(f"FATAL: could not scan saves for secrets ({e}); aborting commit")
    for note in undecodable:
        print(f"WARNING: could not decode save for scanning: {note}", file=sys.stderr)
    if undecodable:
        sys.exit("FATAL: undecodable save(s) cannot be proven secret-free; aborting commit")
    if leaked:
        sys.exit(f"FATAL: secret leaked into {leaked[0]}; aborting commit")

    subprocess.run(["git", "-C", str(REPO), "add", "world/current", "server-config", "metadata/snapshot.json"], check=True)
    diff = subprocess.run(["git", "-C", str(REPO), "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        print("No changes to snapshot; skipping commit.")
        return 0

    subprocess.run(["git", "-C", str(REPO), "commit", "-m", f"snapshot: {reason}"], check=True)
    subprocess.run(["git", "-C", str(REPO), "tag", tag], check=True)
    subprocess.run(["git", "-C", str(REPO), "push", "origin", "main"], check=True)
    subprocess.run(["git", "-C", str(REPO), "push", "origin", tag], check=True)
    print(f"Snapshot committed and pushed: {tag} ({reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
