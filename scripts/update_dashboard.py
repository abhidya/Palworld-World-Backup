"""Regenerate the GitHub Pages dashboard (docs/) from the current world save.

Called by the pre-commit hook so every snapshot commit ships fresh dashboard
data — no GitHub Actions involved. Safe to run by hand:

    py -3 scripts/update_dashboard.py

Requires the toolchain in D:\\palworld-dashboard (extract.py / trends.py).
"""
import os, shutil, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = r"D:\palworld-dashboard"
DOCS = os.path.join(REPO, "docs")
PY = sys.executable


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
