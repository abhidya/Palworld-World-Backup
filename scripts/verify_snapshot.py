#!/usr/bin/env python3
"""Verify a Palworld world snapshot. Standalone: no rig, no dependencies.

Run this on ANY machine after cloning, to prove the checkout is real save data
and not Git LFS pointer stubs.

    python scripts/verify_snapshot.py .

Exit code 0 = the snapshot is complete and every hash matches the manifest.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

CRITICAL = ("Level.sav", "LevelMeta.sav")
LFS_MARKER = b"git-lfs.github.com"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    world = root / "world" / "current"
    manifest_path = root / "metadata" / "snapshot.json"

    problems: list[str] = []
    print(f"repository : {root}")

    if not world.is_dir():
        print(f"FAIL: no world/current under {root}")
        return 1
    if not manifest_path.is_file():
        print("FAIL: metadata/snapshot.json missing; cannot verify integrity")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"world id   : {manifest.get('world_id')}")
    print(f"snapshot   : {manifest.get('snapshot_timestamp')}  tag={manifest.get('snapshot_tag')}")
    print(f"server     : {manifest.get('server_name')} v{manifest.get('server_version')}")
    print(f"boundary   : {manifest.get('snapshot_boundary')}")
    print()

    for name in CRITICAL:
        if not (world / name).is_file():
            problems.append(f"missing critical file: {name}")

    players = sorted((world / "Players").glob("*.sav")) if (world / "Players").is_dir() else []
    print(f"player saves: {len(players)}")
    for player in players:
        print(f"  - {player.name}  {player.stat().st_size:,} bytes")
    if not players:
        problems.append("no player saves present")

    # Unresolved LFS pointers are the #1 way a clone silently looks fine.
    pointers = []
    for path in world.rglob("*.sav"):
        try:
            if path.stat().st_size < 1024 and LFS_MARKER in path.read_bytes()[:200]:
                pointers.append(str(path.relative_to(root)))
        except OSError:
            # A file can vanish mid-scan if a snapshot is republishing the tree
            # concurrently. The manifest hash pass below is the authority.
            continue
    if pointers:
        problems.append(
            f"{len(pointers)} file(s) are unresolved Git LFS pointers "
            f"(run `git lfs install && git lfs pull`): {pointers[:5]}"
        )

    expected = manifest.get("all_file_hashes") or {}
    checked = mismatched = 0
    for relative, want in expected.items():
        path = world / relative.replace("\\", os.sep).replace("/", os.sep)
        if not path.is_file():
            problems.append(f"manifest lists {relative} but it is absent")
            continue
        checked += 1
        if sha256(path) != want:
            mismatched += 1
            problems.append(f"HASH MISMATCH: {relative}")
    print(f"\nhashes     : {checked} checked, {mismatched} mismatched "
          f"(manifest lists {len(expected)})")

    total = count = 0
    for path in world.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
                count += 1
        except OSError:
            continue
    print(f"total size : {total/1e6:.2f} MB across {count} files")

    if problems:
        print("\nFAILED:")
        for problem in problems:
            print(f"  ! {problem}")
        return 1
    print("\nOK: snapshot is complete and every hash matches the manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
