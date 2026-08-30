# Shared site resolution for the timelapse shell stages.
#
#   source "$(dirname "$0")/sites.sh"; paltl_sites   # -> $PALTL_SITES
#
# Delegates to scripts/timelapse_sites.py so shell and Python agree on which
# sites exist, in which order, and which PALTL_BASES/PALTL_SKIP selects.
paltl_sites() {
  local repo="${PALTL_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
  PALTL_SITES="$(python3 "$repo/scripts/timelapse_sites.py" --ids)" || return 1
  [[ -n "$PALTL_SITES" ]] || {
    echo "no sites selected (PALTL_BASES/PALTL_SKIP exclude everything)" >&2
    return 1
  }
  export PALTL_SITES
}
