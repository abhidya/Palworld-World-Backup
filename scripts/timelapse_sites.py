#!/usr/bin/env python3
"""The one place that answers "which sites does this world have?".

Every stage of the timelapse pipeline used to carry its own copy of the five
base ids, in a different order, in four languages.  Adding a site meant editing
five files and finding out at encode time which one was missed, and pointing the
pipeline at a different save was a rewrite rather than a config change.

The render manifest is already derived from the saves by build_union.py (plus
whatever synthetic sites build_colosseum.py-style generators add), so it is the
natural registry: it carries the id, the display name, the object count and the
synthetic flag for exactly the sites that have render inputs.  This module reads
it and nothing else infers a site list.

    from timelapse_sites import sites, ids
    for s in sites():
        print(s.id, s.name, s.objects, s.synthetic)

    python3 scripts/timelapse_sites.py --ids     # newline-separated, for shell
    python3 scripts/timelapse_sites.py --json    # the resolved registry

Selection, both honoured everywhere the registry is used:

    PALTL_BASES="a b"   render/encode/publish only these, in this order
    PALTL_SKIP="c d"    drop these (e.g. a site deliberately left unrendered)

Default order is smallest first, so a broken run surfaces on the cheap site
instead of two hours into the expensive one.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Site:
    """One rendered site. Fields mirror the render manifest, which is generated
    from the saves - nothing here is a hand-maintained fact about this world."""
    id: str
    name: str
    objects: int = 0
    steps: int = 0
    t0: int | None = None
    t1: int | None = None
    synthetic: bool = False
    note: str = ""


def manifest_path(mappal_root: str | os.PathLike[str] | None = None) -> Path:
    """Locate the render manifest. MAPPAL_ROOT wins, then PALTL_WORK/mappal."""
    if mappal_root:
        return Path(mappal_root) / "public" / "union" / "manifest.json"
    if os.environ.get("MAPPAL_ROOT"):
        return Path(os.environ["MAPPAL_ROOT"]) / "public" / "union" / "manifest.json"
    if os.environ.get("PALTL_WORK"):
        return Path(os.environ["PALTL_WORK"]) / "mappal" / "public" / "union" / "manifest.json"
    raise SystemExit(
        "cannot locate the render manifest: set MAPPAL_ROOT (or PALTL_WORK) "
        "to the checkout whose public/union/manifest.json describes this world"
    )


def _selection(env: dict[str, str]) -> tuple[list[str] | None, set[str]]:
    keep = env.get("PALTL_BASES", "").split()
    skip = set(env.get("PALTL_SKIP", "").split())
    return (keep or None), skip


def sites(mappal_root: str | os.PathLike[str] | None = None,
          env: dict[str, str] | None = None) -> list[Site]:
    """Every site with render inputs, after PALTL_BASES/PALTL_SKIP, smallest first."""
    env = os.environ if env is None else env
    path = manifest_path(mappal_root)
    if not path.exists():
        raise SystemExit(f"render manifest not found: {path}\n"
                         "run tools/timelapse/refresh.sh's index stage first")
    raw = json.loads(path.read_text())
    found = {bid: Site(id=bid,
                       name=entry.get("name", bid),
                       objects=int(entry.get("objects", 0)),
                       steps=int(entry.get("steps", 0)),
                       t0=entry.get("t0"),
                       t1=entry.get("t1"),
                       synthetic=bool(entry.get("synthetic")),
                       note=entry.get("note", ""))
             for bid, entry in raw.items()}

    keep, skip = _selection(env)
    if keep:
        unknown = [b for b in keep if b not in found]
        if unknown:
            raise SystemExit(f"PALTL_BASES names sites with no render inputs: {' '.join(unknown)}\n"
                             f"known: {' '.join(sorted(found))}")
        ordered = [found[b] for b in keep]
    else:
        ordered = sorted(found.values(), key=lambda s: (s.objects, s.id))
    return [s for s in ordered if s.id not in skip]


def ids(mappal_root: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None) -> list[str]:
    return [s.id for s in sites(mappal_root, env)]


def by_id(mappal_root: str | os.PathLike[str] | None = None,
          env: dict[str, str] | None = None) -> dict[str, Site]:
    return {s.id: s for s in sites(mappal_root, env)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ids", action="store_true", help="newline-separated ids (for shell)")
    ap.add_argument("--json", action="store_true", help="the resolved registry")
    ap.add_argument("--mappal-root", default=None)
    args = ap.parse_args()

    resolved = sites(args.mappal_root)
    if args.json:
        json.dump([asdict(s) for s in resolved], sys.stdout, indent=1)
        print()
    elif args.ids:
        print("\n".join(s.id for s in resolved))
    else:
        for s in resolved:
            tag = " (synthetic)" if s.synthetic else ""
            print(f"{s.id}  {s.name:20s} {s.objects:6d} objects  {s.steps:4d} steps{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
