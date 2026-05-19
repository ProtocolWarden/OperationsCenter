#!/usr/bin/env bash
# setup-secrets.sh — restore gitignored OC config files from ~/sync/platform/config/
#
# Run this on a fresh clone or new machine to restore local configs from the SS backup.
# Uses symlinks for flat files (same pattern as PlatformDeployment/scripts/setup-secrets.sh).
# Uses a direct copy for the managed_repos/ tree (symlinks don't work for directories).
#
# Usage:
#   scripts/setup-secrets.sh
#   PLATFORM_SECRETS_DIR=/other/path scripts/setup-secrets.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${PLATFORM_SECRETS_DIR:-$HOME/sync/platform/config}"

if [ ! -d "$SRC" ]; then
    echo "error: secrets dir not found: $SRC"
    echo "  set PLATFORM_SECRETS_DIR or ensure ~/sync/platform/config/ exists"
    exit 1
fi

# Flat files: flat backup filename → repo target path
declare -A flat_files=(
    ["oc__config__operations_center.local.yaml"]="config/operations_center.local.yaml"
    ["oc__config__plane_task_template.local.md"]="config/plane_task_template.local.md"
    ["oc__.env.operations-center.local"]=".env.operations-center.local"
)

for flat_name in "${!flat_files[@]}"; do
    repo_path="${flat_files[$flat_name]}"
    secret_file="$SRC/$flat_name"
    target="$REPO_ROOT/$repo_path"

    if [ ! -f "$secret_file" ]; then
        echo "skipped (not in backup): $repo_path"
        continue
    fi

    mkdir -p "$(dirname "$target")"

    if [ -L "$target" ]; then
        rm "$target"
    elif [ -f "$target" ]; then
        echo "warning: $repo_path already exists as a regular file — skipping (remove manually to replace with symlink)"
        continue
    fi

    ln -s "$secret_file" "$target"
    echo "linked: $repo_path → $secret_file"
done

# Tree: oc_managed_repos/local/ → config/managed_repos/local/
MANAGED_SRC="$SRC/oc_managed_repos/local"
MANAGED_DST="$REPO_ROOT/config/managed_repos/local"
if [ -d "$MANAGED_SRC" ]; then
    mkdir -p "$MANAGED_DST"
    cp -r "$MANAGED_SRC/." "$MANAGED_DST/"
    echo "copied: oc_managed_repos/local/ → config/managed_repos/local/"
else
    echo "skipped (not in backup): oc_managed_repos/local/"
fi

echo "done — OC secrets restored from $SRC"
echo ""
echo "Verify:"
echo "  config/operations_center.local.yaml  — $([ -e "$REPO_ROOT/config/operations_center.local.yaml" ] && echo 'ok' || echo 'MISSING')"
echo "  .env.operations-center.local         — $([ -e "$REPO_ROOT/.env.operations-center.local" ] && echo 'ok' || echo 'MISSING')"
echo "  config/managed_repos/local/          — $([ -d "$REPO_ROOT/config/managed_repos/local" ] && echo 'ok' || echo 'MISSING')"
