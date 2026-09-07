#!/usr/bin/env bash
# Report when a newer palworld-server-docker image has been published.
#
# Read-only by design: it compares the local image digest against the registry
# and stops there. It never pulls, never restarts, and never touches the running
# container - applying an image update recreates the container, which is a
# deliberate act with a snapshot taken first. See README, "Server updates".
#
#   scripts/check_container_image.sh            # print status, exit 1 if stale
#   scripts/check_container_image.sh --notify   # also raise a macOS notification
#
# Exit: 0 up to date | 1 stale | 2 could not determine
set -uo pipefail

REF="${PALWORLD_IMAGE:-thijsvanloef/palworld-server-docker:latest}"

# RepoDigests holds the multi-arch index digest, which is what imagetools
# reports for the tag - so these are directly comparable.
local_digest="$(docker image inspect "$REF" --format '{{index .RepoDigests 0}}' 2>/dev/null | cut -d@ -f2)"
remote_digest="$(docker buildx imagetools inspect "$REF" --format '{{.Manifest.Digest}}' 2>/dev/null)"

if [ -z "$local_digest" ] || [ -z "$remote_digest" ]; then
  echo "[image-check] could not resolve digests (local='${local_digest:-}' remote='${remote_digest:-}')" >&2
  exit 2
fi

if [ "$local_digest" = "$remote_digest" ]; then
  echo "[image-check] up to date - ${REF} ${local_digest:0:26}"
  exit 0
fi

echo "[image-check] STALE - local ${local_digest:0:26} != registry ${remote_digest:0:26}"
echo "[image-check] apply with: python3 scripts/snapshot_from_mac.py --force && cd ~/PalworldServer && docker compose pull && docker compose up -d"

if [ "${1:-}" = "--notify" ]; then
  osascript -e 'display notification "A newer server image is published. See README - Server updates." with title "Palworld: container image is stale"' 2>/dev/null || true
fi
exit 1
