#!/usr/bin/env bash
# Recreate the render pipeline's external dependencies. Idempotent - safe to
# re-run, and it never discards local work in an existing checkout.
#
# Nothing here may be a symlink into the scratch volume. A checkout has to be
# able to rebuild its own dependencies; an absolute path into someone's
# external disk is not a dependency, it is a machine that has to still exist.
#
#   PALTL_WORK=/path/to/scratch tools/timelapse/bootstrap.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Pinned: the extractors are written against this CUE4Parse API. Bump
# deliberately and rebuild, rather than drifting with upstream main.
CUE4PARSE_URL="https://github.com/FabianFG/CUE4Parse.git"
CUE4PARSE_REF="9893d83ba7fed6c9486cfeb758632879d794ec32"

# The renderer app. Lives in its own repo; refresh.sh reaches it via
# MAPPAL_ROOT, which defaults to $PALTL_WORK/mappal.
MAPPAL_URL="https://github.com/abhidya/mappal-palworld.git"
MAPPAL_BRANCH="fix/timelapse-camera-water-builders"

# ---- 1. CUE4Parse ---------------------------------------------------------
# extractors/*/*.csproj reference ../cue4parse/... by relative path, so this
# must be a real directory inside the repo, not a link to one.
dest="$REPO/tools/timelapse/extractors/cue4parse"
if [ -L "$dest" ]; then
  echo "[bootstrap] removing legacy symlink: $dest -> $(readlink "$dest")"
  rm "$dest"
fi
if [ ! -d "$dest/.git" ]; then
  echo "[bootstrap] cloning CUE4Parse -> $dest"
  git clone --quiet --filter=blob:none "$CUE4PARSE_URL" "$dest"
fi
if [ "$(git -C "$dest" rev-parse HEAD)" != "$CUE4PARSE_REF" ]; then
  git -C "$dest" fetch --quiet origin
  git -C "$dest" checkout --quiet --detach "$CUE4PARSE_REF"
fi
echo "[bootstrap] cue4parse  $(git -C "$dest" rev-parse --short HEAD) (pinned)"

# ---- 2. mappal ------------------------------------------------------------
# Only needed on a host that renders. A snapshot-only host can stop here.
if [ -z "${PALTL_WORK:-}" ]; then
  echo "[bootstrap] PALTL_WORK unset - skipping mappal (snapshot-only host)"
  exit 0
fi
mkdir -p "$PALTL_WORK"
MAPPAL_ROOT="${MAPPAL_ROOT:-$PALTL_WORK/mappal}"
if [ ! -d "$MAPPAL_ROOT/.git" ]; then
  echo "[bootstrap] cloning mappal -> $MAPPAL_ROOT"
  git clone --quiet --branch "$MAPPAL_BRANCH" "$MAPPAL_URL" "$MAPPAL_ROOT"
else
  # Never reset: this checkout carries in-flight render work.
  echo "[bootstrap] mappal present on $(git -C "$MAPPAL_ROOT" rev-parse --abbrev-ref HEAD)"
  n="$(git -C "$MAPPAL_ROOT" rev-list --count HEAD --not --remotes 2>/dev/null || echo 0)"
  [ "$n" != "0" ] && echo "[bootstrap] WARNING: $n mappal commits are not on any remote - push them"
fi

# vite and the render toolchain resolve from mappal's own node_modules; nothing
# in this repo needs a node_modules of its own.
if [ ! -x "$MAPPAL_ROOT/node_modules/.bin/vite" ]; then
  echo "[bootstrap] npm install in $MAPPAL_ROOT"
  (cd "$MAPPAL_ROOT" && npm install --no-audit --no-fund)
fi
echo "[bootstrap] mappal     $(git -C "$MAPPAL_ROOT" rev-parse --short HEAD)"
echo "[bootstrap] done"
