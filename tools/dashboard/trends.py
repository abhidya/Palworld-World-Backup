"""Build trends.js from the backup repo's git history.

Walks every commit that changed world/current/Level.sav, materializes that
snapshot into a temp dir (via git lfs smudge), runs `extract.py --summary`,
and caches results by commit sha so only new snapshots are parsed.

Usage:  py -3 trends.py [path-to-repo-root]
Default repo root: two levels up from this script (tools/dashboard/../..).
"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(HERE))
CACHE = os.path.join(HERE, "trends-cache.json")
PY = sys.executable

cache = {}
if os.path.exists(CACHE):
    cache = json.load(open(CACHE, encoding="utf-8"))

def git(*args, binary=False):
    r = subprocess.run(["git", "-C", REPO, *args], capture_output=True)
    if r.returncode != 0:
        raise Exception(f"git {' '.join(args)}: {r.stderr.decode()[:300]}")
    return r.stdout if binary else r.stdout.decode()

def smudge(sha, path, dest):
    """Materialize an LFS file from a commit into dest."""
    blob = git("show", f"{sha}:{path}", binary=True)
    if blob.startswith(b"version https://git-lfs"):
        r = subprocess.run(["git", "-C", REPO, "lfs", "smudge", path],
                           input=blob, capture_output=True)
        if r.returncode != 0:
            raise Exception("lfs smudge failed: " + r.stderr.decode()[:200])
        blob = r.stdout
    with open(dest, "wb") as f:
        f.write(blob)

log = git("log", "--reverse", "--format=%H|%cI|%s", "--", "world/current/Level.sav")
commits = [l.split("|", 2) for l in log.strip().splitlines() if l]
print(f"{len(commits)} snapshots in history")

points = []
for sha, when, subject in commits:
    if sha in cache:
        points.append(cache[sha])
        continue
    print("parsing", sha[:8], when, subject[:60])
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "Players"), exist_ok=True)
        smudge(sha, "world/current/Level.sav", os.path.join(td, "Level.sav"))
        ls = git("ls-tree", "--name-only", sha, "world/current/Players/")
        for pf in ls.strip().splitlines():
            smudge(sha, pf, os.path.join(td, "Players", os.path.basename(pf)))
        r = subprocess.run([PY, os.path.join(HERE, "extract.py"), td, "--summary"],
                           capture_output=True)
        if r.returncode != 0:
            print("  ! extract failed:", r.stderr.decode()[-300:])
            continue
        summ = json.loads(r.stdout.decode("utf-8"))
    summ["sha"] = sha[:10]
    summ["ts"] = when
    summ["reason"] = subject.replace("snapshot: ", "")
    # keep only top items to limit size
    top = dict(sorted(summ["items"].items(), key=lambda x: -x[1])[:80])
    summ["items"] = top
    cache[sha] = summ
    points.append(summ)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"))

with open(os.path.join(HERE, "trends.js"), "w", encoding="utf-8") as f:
    f.write("window.PALTRENDS = ")
    json.dump(points, f, ensure_ascii=False, separators=(",", ":"))
    f.write(";\n")
print("wrote trends.js with", len(points), "points",
      round(os.path.getsize(os.path.join(HERE, "trends.js")) / 1e6, 2), "MB")
