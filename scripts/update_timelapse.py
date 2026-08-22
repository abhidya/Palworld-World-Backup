"""Publish rendered base timelapses into docs/ for GitHub Pages.

Frames are rendered outside the repo (see tools/timelapse/README if present);
this only copies the encoded videos and writes the manifest the dashboard's
Timelapse tab reads at runtime. Idempotent — safe to re-run.

    python3 scripts/update_timelapse.py <video-dir> <index.json>

Videos must NOT go through git-lfs: GitHub Pages serves LFS pointer files
verbatim rather than resolving them, which would break playback.
"""
import json, os, shutil, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_TL = os.path.join(REPO, "docs", "timelapse")
NAMES = {'07f13218': 'Glass Tower', '16fca097': 'Wooden Camp',
         'de44d9f4': 'Stone Works', '5fed0024': 'Lost Camp'}
LOOT = 'CommonDropItem3D'


def main():
    vid_dir = sys.argv[1] if len(sys.argv) > 1 else None
    index = sys.argv[2] if len(sys.argv) > 2 else None
    if not vid_dir or not os.path.isdir(vid_dir):
        sys.exit("usage: update_timelapse.py <video-dir> <build_index.json>")
    os.makedirs(DOCS_TL, exist_ok=True)

    rows = json.load(open(index)) if index and os.path.exists(index) else []
    per = {}
    for r in rows:
        if r.get('type') == LOOT:
            continue
        per.setdefault(r['base'], []).append(r)

    man = {}
    for fn in sorted(os.listdir(vid_dir)):
        if not fn.endswith(".mp4"):
            continue
        bid = fn[:-4]
        shutil.copy2(os.path.join(vid_dir, fn), os.path.join(DOCS_TL, fn))
        entry = {"name": NAMES.get(bid, bid)}
        rs = per.get(bid)
        if rs:
            t0 = min(r['first'] for r in rs)
            t1 = max(r['last'] for r in rs)
            entry.update({
                "structures": len(rs),
                "steps": len({r['first'] for r in rs}),
                "t0": t0, "t1": t1,
                "built": sum(1 for r in rs if r['first'] > t0 + 300),
                "removed": sum(1 for r in rs if r['last'] < t1 - 600),
            })
        man[bid] = entry
        print(f"  {entry['name']:13s} {os.path.getsize(os.path.join(DOCS_TL, fn))//1024:6d} KB")

    with open(os.path.join(DOCS_TL, "manifest.json"), "w") as f:
        json.dump(man, f, indent=1)
    print(f"[timelapse] {len(man)} videos -> docs/timelapse/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
