#!/usr/bin/env bash
# Apply (or verify) the branch protection in branch-protection.json.
#
# Branch protection lives only in the forge's database. It survives a volume
# restore, but nothing else — not a fresh instance, not a repo re-created by
# hand. Without it `main` accepts any push, and the whole point of the cutover
# (audit + reviewer-verdict required, admins included) silently evaporates
# while everything still LOOKS fine.
#
#   apply-branch-protection.sh           apply the rules
#   apply-branch-protection.sh --check   compare live vs file, change nothing
set -euo pipefail

BASE_URL="${FORGEJO_BASE_URL:-http://localhost:3000}"
REPO="${FORGEJO_REPO:-Operations_Center_Admin/OperationsCenter}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULES="$HERE/branch-protection.json"

if [ -z "${FORGEJO_API_TOKEN:-}" ]; then
  echo "FORGEJO_API_TOKEN is not set (it lives in .env.operations-center.local)" >&2
  exit 2
fi

API="$BASE_URL/api/v1/repos/$REPO/branch_protections"
AUTH="Authorization: token $FORGEJO_API_TOKEN"

if [ "${1:-}" = "--check" ]; then
  LIVE_JSON="$(curl -sf -H "$AUTH" "$API")" || { echo "cannot reach $API" >&2; exit 1; }
  # Passed through the environment, NOT a second stdin redirect: two redirects
  # on one command means the last wins, so the heredoc script would be replaced
  # by the JSON and python would try to execute it.
  LIVE_JSON="$LIVE_JSON" python3 - "$RULES" <<'PY'
import json, os, sys
want = {r["branch_name"]: r for r in json.load(open(sys.argv[1]))}
live = {r["branch_name"]: r for r in json.loads(os.environ["LIVE_JSON"])}
rc = 0
for branch, w in want.items():
    got = live.get(branch)
    if got is None:
        print(f"MISSING: no protection rule for '{branch}'"); rc = 1; continue
    for key, expected in w.items():
        if key in ("branch_name", "rule_name"):
            continue
        actual = got.get(key)
        if isinstance(expected, list):
            same = sorted(actual or []) == sorted(expected)
        else:
            same = actual == expected
        if not same:
            print(f"DRIFT: {branch}.{key}: live={actual!r} expected={expected!r}"); rc = 1
print("branch protection matches branch-protection.json" if rc == 0 else "branch protection HAS DRIFTED")
sys.exit(rc)
PY
  exit $?
fi

python3 -c "import json,sys; [print(json.dumps(r)) for r in json.load(open('$RULES'))]" |
while IFS= read -r rule; do
  branch="$(printf '%s' "$rule" | python3 -c 'import json,sys; print(json.load(sys.stdin)["branch_name"])')"
  if curl -sf -o /dev/null -H "$AUTH" "$API/$branch"; then
    echo "updating rule for '$branch'"
    curl -sf -X PATCH -H "$AUTH" -H "Content-Type: application/json" \
      --data "$rule" "$API/$branch" > /dev/null
  else
    echo "creating rule for '$branch'"
    curl -sf -X POST -H "$AUTH" -H "Content-Type: application/json" \
      --data "$rule" "$API" > /dev/null
  fi
done
echo "done; verifying"
exec "$0" --check
