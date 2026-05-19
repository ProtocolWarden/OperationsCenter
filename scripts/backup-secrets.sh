#!/usr/bin/env bash
# backup-secrets.sh — copy live gitignored OC config files to ~/sync/platform/config/
#
# Run this after changing any local config so the backup stays current.
# Mirrors the structure expected by setup-secrets.sh for restore.
#
# Usage:
#   scripts/backup-secrets.sh
#   PLATFORM_SECRETS_DIR=/other/path scripts/backup-secrets.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${PLATFORM_SECRETS_DIR:-$HOME/sync/platform/config}"

mkdir -p "$DEST"

# Flat files: repo path → flat backup filename
declare -A flat_files=(
    ["config/operations_center.local.yaml"]="oc__config__operations_center.local.yaml"
    ["config/plane_task_template.local.md"]="oc__config__plane_task_template.local.md"
    [".env.operations-center.local"]="oc__.env.operations-center.local"
)

for repo_path in "${!flat_files[@]}"; do
    flat_name="${flat_files[$repo_path]}"
    src="$REPO_ROOT/$repo_path"
    dst="$DEST/$flat_name"
    if [ -f "$src" ]; then
        cp "$src" "$dst"
        echo "backed up: $repo_path → $dst"
    else
        echo "skipped (not found): $repo_path"
    fi
done

# Tree: config/managed_repos/local/ → oc_managed_repos/local/
MANAGED_SRC="$REPO_ROOT/config/managed_repos/local"
MANAGED_DST="$DEST/oc_managed_repos/local"
if [ -d "$MANAGED_SRC" ]; then
    mkdir -p "$MANAGED_DST"
    cp -r "$MANAGED_SRC/." "$MANAGED_DST/"
    echo "backed up: config/managed_repos/local/ → $MANAGED_DST/"
else
    echo "skipped (not found): config/managed_repos/local/"
fi

echo "done — OC secrets backed up to $DEST"
