"""Build ts -> (RealDateTimeTicks, GameDateTimeTicks) for every save snapshot that
is the first-seen timestamp of at least one base object.  Same source enumeration
as union_full.py so the timestamps line up exactly with times_<base>.json."""
import json, os, glob, subprocess, sys, time
from multiprocessing import Pool
from gametime import game_time
SP = os.path.dirname(os.path.abspath(__file__)); REPO = "/Users/mannybhidya/Palworld"
rows = json.load(open(f"{SP}/build_index.json"))
want = sorted({r["first"] for r in rows} | {r["last"] for r in rows})
src = {}
for l in open(f"{SP}/commits.txt"):
    c, t = l.split(); src.setdefault(int(t), ("git", c))
# nasbk3 was missing here while union_full.py globbed it, so 6 snapshots that
# times_<base>.json refers to had no game clock at all and two bases silently
# lost in-game pacing. Keep this list identical to union_full.py's.
for pat in ("nas/**/Level.sav", "nasbk/**/Level.sav", "nasbk2/**/Level.sav", "nasbk3/**/Level.sav"):
    for p in glob.glob(f"{SP}/{pat}", recursive=True):
        src.setdefault(int(os.path.getmtime(p)), ("file", p))
todo = [(t, src[t]) for t in want if t in src]
print(f"snapshots to read: {len(todo)} (of {len(want)} wanted)", flush=True)

def work(job):
    ts, (kind, ref) = job
    try:
        if kind == "git":
            raw = subprocess.run(["git", "-C", REPO, "lfs", "smudge"],
                input=subprocess.run(["git", "-C", REPO, "show", f"{ref}:world/current/Level.sav"],
                                     capture_output=True).stdout, capture_output=True).stdout
        else:
            raw = open(ref, "rb").read()
        if len(raw) < 100000: return (ts, None)
        rt, gt = game_time(raw)
        return (ts, (rt, gt))
    except Exception:
        return (ts, None)

if __name__ == "__main__":
    out = {}; t0 = time.time(); done = 0
    with Pool(6) as pool:
        for ts, v in pool.imap_unordered(work, todo, chunksize=4):
            done += 1
            if v and v[1]: out[ts] = v
            if done % 100 == 0: print(f"  {done}/{len(todo)} ok={len(out)} {time.time()-t0:.0f}s", flush=True)
    json.dump({str(k): v for k, v in sorted(out.items())}, open(f"{SP}/gametime_index.json.tmp", "w"))
    os.replace(f"{SP}/gametime_index.json.tmp", f"{SP}/gametime_index.json")
    print(f"DONE {len(out)}/{len(todo)} -> gametime_index.json in {time.time()-t0:.0f}s", flush=True)
