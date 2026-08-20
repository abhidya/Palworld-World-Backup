"""Regenerate the GitHub Pages dashboard (docs/) from the current world save.

Called by the pre-commit hook so every snapshot commit ships fresh dashboard
data — no GitHub Actions involved. Safe to run by hand:

    python3 scripts/update_dashboard.py

Toolchain lives in tools/dashboard/ (this repo). Its Python deps live in a
dedicated venv OUTSIDE the repo (pyooz needs a prebuilt wheel; see
tools/dashboard/requirements.txt for the original spec):

    /Users/mannybhidya/PalworldServer/dashboard-venv
"""
import os, shutil, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO, "tools", "dashboard")
DOCS = os.path.join(REPO, "docs")
VENV_PY = "/Users/mannybhidya/PalworldServer/dashboard-venv/bin/python"
PY = VENV_PY if os.path.exists(VENV_PY) else sys.executable


def run(script, *args):
    r = subprocess.run([PY, os.path.join(TOOLS, script), *args],
                       capture_output=True, cwd=TOOLS)
    if r.returncode != 0:
        sys.stderr.write(f"[dashboard] {script} failed:\n" + r.stderr.decode(errors="replace")[-800:] + "\n")
        return False
    return True


def main():
    if not os.path.isdir(TOOLS):
        sys.stderr.write("[dashboard] toolchain missing at %s — skipping\n" % TOOLS)
        return 0
    # Throttle: snapshots land every ~60s but a regen costs ~40s of CPU.
    # A dashboard at most 10 minutes stale is fine. --force overrides.
    import time
    marker = os.path.join(DOCS, "data.js")
    if "--force" not in sys.argv and os.path.exists(marker) \
            and time.time() - os.path.getmtime(marker) < 600:
        return 0
    # One regen at a time. Snapshot commits can arrive faster than a regen
    # finishes; skipping is fine — the next commit refreshes the dashboard.
    lock = os.path.join(TOOLS, ".update.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        try:
            pid = int(open(lock).read().strip() or 0)
            os.kill(pid, 0)  # raises if that process is gone
            sys.stderr.write("[dashboard] regen already running (pid %d) — skipping\n" % pid)
            return 0
        except (ProcessLookupError, ValueError):
            os.remove(lock)  # stale lock from a dead run
            return main()
    import atexit
    atexit.register(lambda: os.path.exists(lock) and os.remove(lock))
    ok = run("extract.py", os.path.join(REPO, "world", "current"))
    ok = run("trends.py", REPO) and ok
    os.makedirs(DOCS, exist_ok=True)
    for f in ("data.js", "trends.js"):
        src = os.path.join(TOOLS, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(DOCS, f))
    shutil.copy2(os.path.join(TOOLS, "dashboard.html"), os.path.join(DOCS, "index.html"))
    print("[dashboard] docs/ updated", "(with warnings)" if not ok else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
