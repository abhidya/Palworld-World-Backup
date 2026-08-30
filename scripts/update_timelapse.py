"""Publish rendered base timelapses into docs/ for GitHub Pages.

Frames are rendered outside the repo (see tools/timelapse/README if present);
this only copies the encoded videos and writes the manifest the dashboard's
Timelapse tab reads at runtime. Idempotent — safe to re-run.

    python3 scripts/update_timelapse.py <video-dir> <index.json> [render-manifest.json]

Videos must NOT go through git-lfs: GitHub Pages serves LFS pointer files
verbatim rather than resolving them, which would break playback.
"""
import json, os, shutil, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_TL = os.path.join(REPO, "docs", "timelapse")
LOOT = 'CommonDropItem3D'


def main():
    vid_dir = sys.argv[1] if len(sys.argv) > 1 else None
    index = sys.argv[2] if len(sys.argv) > 2 else None
    render_manifest_path = sys.argv[3] if len(sys.argv) > 3 else None
    if not vid_dir or not os.path.isdir(vid_dir):
        sys.exit("usage: update_timelapse.py <video-dir> <build_index.json> [render-manifest.json]")
    os.makedirs(DOCS_TL, exist_ok=True)

    rows = json.load(open(index)) if index and os.path.exists(index) else []
    render_manifest = (json.load(open(render_manifest_path))
                       if render_manifest_path and os.path.exists(render_manifest_path) else {})
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
        render_entry = render_manifest.get(bid, {})
        # Names come from the render manifest, which build_union.py derives from
        # the saves. A video with no manifest entry is published under its id
        # rather than a name invented here.
        entry = {"name": render_entry.get("name", bid)}
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
        elif render_entry:
            # Synthetic designs have no save-history rows. Publish the explicit
            # render-input counts instead of presenting missing data as zero.
            entry.update({
                "structures": render_entry.get("objects", 0),
                "steps": render_entry.get("steps", 0),
                "t0": render_entry.get("t0"),
                "t1": render_entry.get("t1"),
                "built": 0,
                "removed": 0,
                "synthetic": bool(render_entry.get("synthetic")),
            })
        man[bid] = entry
        print(f"  {entry['name']:13s} {os.path.getsize(os.path.join(DOCS_TL, fn))//1024:6d} KB")

    with open(os.path.join(DOCS_TL, "manifest.json"), "w") as f:
        json.dump(man, f, indent=1)
    print(f"[timelapse] {len(man)} videos -> docs/timelapse/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
