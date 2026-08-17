#!/usr/bin/env bash
# =============================================================================
# check-doc-links.sh — relative markdown link checker
#
# Verifies that every relative `.md` link in the documentation resolves to a
# file that exists. Catches the two ways doc links rot:
#   1. a document moves and its inbound links are not updated;
#   2. a link is written to a document that is never actually authored.
#
# The second is the common one here: at the time this script was added, 7 of
# the repo's broken links pointed at files that had never existed in git
# history at all.
#
# Skips, deliberately:
#   * external URLs and mailto:
#   * pure anchors (#section)
#   * template placeholders containing `<` — e.g. `<repo_id>_contract.md` in
#     docs/architecture/managed-repos/, which are illustrative, not links.
#
# Usage:
#   scripts/check-doc-links.sh            # report, exit 1 if any are broken
#   scripts/check-doc-links.sh --quiet    # exit status only
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

bad=0
total=0
report=""

while IFS= read -r f; do
  d=$(dirname "$f")
  while IFS= read -r target; do
    case "$target" in
      http*|mailto:*|"") continue ;;
      *'<'*) continue ;;                    # template placeholder, not a link
    esac
    total=$((total + 1))
    if [ ! -e "$d/$target" ]; then
      report="${report}  ${f} -> ${target}"$'\n'
      bad=$((bad + 1))
    fi
  done < <(grep -oE '\]\([^)]+\)' "$f" 2>/dev/null \
           | sed 's/^](//; s/)$//; s/#.*$//' \
           | grep -E '\.md$')
done < <(find docs .console -name '*.md' -not -path '*/.git/*' 2>/dev/null; ls *.md 2>/dev/null)

if [ "$QUIET" -eq 0 ]; then
  if [ "$bad" -gt 0 ]; then
    echo "Broken documentation links:"
    printf '%s' "$report"
    echo
  fi
  echo "checked ${total} relative .md links; broken: ${bad}"
fi

[ "$bad" -eq 0 ]
