#!/usr/bin/env bash
# ContextGuard — Claude Code PreToolUse adapter
# Implements: pre_action, pre_write, pre_spawn
#
# Receives JSON on stdin: {"tool_name": "...", "tool_input": {...}}
# Exit 0 = allow, exit 2 = block (stderr surfaced to operator)
# JSON output: {"decision": "block", "reason": "..."} also supported

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
CONFIG_FILE="${REPO_ROOT}/.context/config.yaml"

# --- Session marker (created on first tool call; stop.sh uses it to detect fresh checkpoints) ---
_SESSION_HASH="$(echo "$REPO_ROOT" | cksum | cut -d' ' -f1)"
SESSION_MARKER="/tmp/clp_session_${_SESSION_HASH}"
[[ -f "$SESSION_MARKER" ]] || touch "$SESSION_MARKER" 2>/dev/null || true

# --- Read hook input ---
INPUT="$(cat)"
TOOL_NAME="$(echo "$INPUT" | jq -r '.tool_name // ""')"
TOOL_INPUT="$(echo "$INPUT" | jq -r '.tool_input // {}')"

# --- Load config (with defaults) ---
REQUIRE_CAPSULE=false
ENFORCE_LEASE=true
CAPSULE_PATH=".context/active/"
CHECKPOINT_PATH=".context/checkpoints/"
HANDOFF_PATH=".context/handoffs/"

if [[ -f "${CONFIG_FILE}" ]] && command -v python3 &>/dev/null; then
  REQUIRE_CAPSULE=$(python3 -c "
import sys
try:
    import yaml
    with open('${CONFIG_FILE}') as f:
        c = yaml.safe_load(f)
    print(str(c.get('guard', {}).get('require_capsule', False)).lower())
except Exception:
    print('false')
" 2>/dev/null || echo "false")

  ENFORCE_LEASE=$(python3 -c "
import sys
try:
    import yaml
    with open('${CONFIG_FILE}') as f:
        c = yaml.safe_load(f)
    print(str(c.get('guard', {}).get('enforce_lease', True)).lower())
except Exception:
    print('true')
" 2>/dev/null || echo "true")

  CAPSULE_PATH=$(python3 -c "
try:
    import yaml
    with open('${CONFIG_FILE}') as f:
        c = yaml.safe_load(f)
    print(c.get('guard', {}).get('capsule_path', '.context/active/'))
except Exception:
    print('.context/active/')
" 2>/dev/null || echo ".context/active/")

  HANDOFF_PATH=$(python3 -c "
try:
    import yaml
    with open('${CONFIG_FILE}') as f:
        c = yaml.safe_load(f)
    print(c.get('guard', {}).get('handoff_path', '.context/handoffs/'))
except Exception:
    print('.context/handoffs/')
" 2>/dev/null || echo ".context/handoffs/")
fi

# --- Helper: find active capsule ---
find_active_capsule() {
  local capsule_dir="${REPO_ROOT}/${CAPSULE_PATH}"
  if [[ -d "$capsule_dir" ]]; then
    find "$capsule_dir" -name "*.yaml" -not -name ".gitkeep" | head -1
  fi
}

# --- Helper: get field from YAML file ---
yaml_field() {
  local file="$1"
  local field="$2"
  python3 -c "
try:
    import yaml
    with open('${file}') as f:
        d = yaml.safe_load(f)
    val = d.get('${field}', '')
    print(val if val is not None else '')
except Exception:
    print('')
" 2>/dev/null || echo ""
}

# --- Helper: block with reason ---
block() {
  local reason="$1"
  echo "{\"decision\": \"block\", \"reason\": \"ContextGuard: ${reason}\"}"
  exit 2
}

# --- Helper: warn (non-blocking, writes to stderr) ---
warn() {
  echo "ContextGuard warning: $1" >&2
}

# --- Check: require_capsule (+ malformed YAML detection) ---
if [[ "$REQUIRE_CAPSULE" == "true" ]]; then
  ACTIVE_CAPSULE="$(find_active_capsule)"
  if [[ -z "$ACTIVE_CAPSULE" ]]; then
    block "No active capsule found in ${CAPSULE_PATH}. Create or load an InvestigationCapsule before proceeding."
  else
    # Validate capsule is parseable YAML with required identity fields
    CAPSULE_VALID=$(python3 -c "
try:
    import yaml
    with open('${ACTIVE_CAPSULE}') as f:
        d = yaml.safe_load(f)
    required = ['capsule_id', 'schema_version', 'status']
    missing = [k for k in required if not d.get(k)]
    print('ok' if not missing else 'missing:' + ','.join(missing))
except Exception as e:
    print('malformed:' + str(e)[:80])
" 2>/dev/null || echo "unreadable")
    if [[ "$CAPSULE_VALID" != "ok" ]]; then
      block "Active capsule is invalid (${CAPSULE_VALID}). Fix or remove ${ACTIVE_CAPSULE} before proceeding."
    fi
  fi
fi

# --- Check: lease expiry ---
if [[ "$ENFORCE_LEASE" == "true" ]]; then
  HANDOFF_DIR="${REPO_ROOT}/${HANDOFF_PATH}"
  if [[ -d "$HANDOFF_DIR" ]]; then
    ACTIVE_HANDOFF="$(find "$HANDOFF_DIR" -name "*.yaml" -not -name ".gitkeep" | head -1)"
    if [[ -n "$ACTIVE_HANDOFF" ]]; then
      EXPIRES_AT="$(yaml_field "$ACTIVE_HANDOFF" "expires_at")"
      if [[ -n "$EXPIRES_AT" ]]; then
        NOW_EPOCH=$(date -u +%s)
        EXPIRES_EPOCH=$(date -u -d "$EXPIRES_AT" +%s 2>/dev/null || date -u -jf "%Y-%m-%dT%H:%M:%SZ" "$EXPIRES_AT" +%s 2>/dev/null || echo "0")
        if [[ "$EXPIRES_EPOCH" -gt 0 && "$NOW_EPOCH" -gt "$EXPIRES_EPOCH" ]]; then
          block "Lease expired at ${EXPIRES_AT}. Write a LoopCheckpoint and escalate before continuing."
        fi
      fi
    fi
  fi
fi

# --- Check: pre_write — forbidden paths ---
if [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" ]]; then
  TARGET_PATH="$(echo "$TOOL_INPUT" | jq -r '.file_path // .path // ""')"

  if [[ -n "$TARGET_PATH" ]]; then
    HANDOFF_DIR="${REPO_ROOT}/${HANDOFF_PATH}"
    if [[ -d "$HANDOFF_DIR" ]]; then
      ACTIVE_HANDOFF="$(find "$HANDOFF_DIR" -name "*.yaml" -not -name ".gitkeep" | head -1)"
      if [[ -n "$ACTIVE_HANDOFF" ]]; then
        FORBIDDEN=$(python3 -c "
try:
    import yaml
    with open('${ACTIVE_HANDOFF}') as f:
        d = yaml.safe_load(f)
    forbidden = d.get('worker_scope', {}).get('forbidden_paths', []) or []
    for p in forbidden:
        print(p)
except Exception:
    pass
" 2>/dev/null || true)

        while IFS= read -r forbidden_path; do
          if [[ -n "$forbidden_path" && "$TARGET_PATH" == "$forbidden_path"* ]]; then
            block "Path '${TARGET_PATH}' is forbidden by active worker scope (matches '${forbidden_path}')."
          fi
        done <<< "$FORBIDDEN"

        # Enforce allowed_paths whitelist — if non-empty, path must match at least one entry
        ALLOWED=$(python3 -c "
try:
    import yaml
    with open('${ACTIVE_HANDOFF}') as f:
        d = yaml.safe_load(f)
    allowed = d.get('worker_scope', {}).get('allowed_paths', []) or []
    for p in allowed:
        print(p)
except Exception:
    pass
" 2>/dev/null || true)

        if [[ -n "$ALLOWED" ]]; then
          PATH_ALLOWED=false
          while IFS= read -r allowed_path; do
            if [[ -n "$allowed_path" && "$TARGET_PATH" == "$allowed_path"* ]]; then
              PATH_ALLOWED=true
              break
            fi
          done <<< "$ALLOWED"
          if [[ "$PATH_ALLOWED" == "false" ]]; then
            block "Path '${TARGET_PATH}' is outside worker scope allowed_paths. Permitted prefixes: $(echo "$ALLOWED" | tr '\n' ' ')"
          fi
        fi

        MUTATION_POLICY=$(python3 -c "
try:
    import yaml
    with open('${ACTIVE_HANDOFF}') as f:
        d = yaml.safe_load(f)
    print(d.get('worker_scope', {}).get('mutation_policy', 'write_allowed'))
except Exception:
    print('write_allowed')
" 2>/dev/null || echo "write_allowed")

        if [[ "$MUTATION_POLICY" == "read_only" ]]; then
          block "Worker scope is read_only. Write operations are not permitted."
        fi
      fi
    fi
  fi
fi

# --- Check: pre_spawn — subagent budget ---
if [[ "$TOOL_NAME" == "Agent" ]]; then
  HANDOFF_DIR="${REPO_ROOT}/${HANDOFF_PATH}"
  if [[ -d "$HANDOFF_DIR" ]]; then
    ACTIVE_HANDOFF="$(find "$HANDOFF_DIR" -name "*.yaml" -not -name ".gitkeep" | head -1)"
    if [[ -n "$ACTIVE_HANDOFF" ]]; then
      MAX_SUBAGENTS=$(python3 -c "
try:
    import yaml
    with open('${ACTIVE_HANDOFF}') as f:
        d = yaml.safe_load(f)
    print(d.get('lease', {}).get('max_subagents', -1))
except Exception:
    print(-1)
" 2>/dev/null || echo "-1")

      if [[ "$MAX_SUBAGENTS" == "0" ]]; then
        block "Active lease prohibits subagent spawning (max_subagents: 0)."
      fi
    fi
  fi

  # Check context_risk.high_parallelism from latest checkpoint
  CHECKPOINT_DIR="${REPO_ROOT}/${CHECKPOINT_PATH}"
  if [[ -d "$CHECKPOINT_DIR" ]]; then
    LATEST_CHECKPOINT="$(find "$CHECKPOINT_DIR" -name "*.yaml" -not -name ".gitkeep" | sort | tail -1)"
    if [[ -n "$LATEST_CHECKPOINT" ]]; then
      HIGH_PARALLELISM=$(python3 -c "
try:
    import yaml
    with open('${LATEST_CHECKPOINT}') as f:
        d = yaml.safe_load(f)
    risk = d.get('orchestrator', {}).get('context_risk', {})
    print(str(risk.get('high_parallelism', False)).lower())
except Exception:
    print('false')
" 2>/dev/null || echo "false")

      if [[ "$HIGH_PARALLELISM" == "true" ]]; then
        block "context_risk.high_parallelism is true. Deny additional worker spawning until resolved."
      fi

      SUBAGENT_HEAVY=$(python3 -c "
try:
    import yaml
    with open('${LATEST_CHECKPOINT}') as f:
        d = yaml.safe_load(f)
    risk = d.get('orchestrator', {}).get('context_risk', {})
    print(str(risk.get('subagent_heavy', False)).lower())
except Exception:
    print('false')
" 2>/dev/null || echo "false")

      if [[ "$SUBAGENT_HEAVY" == "true" ]]; then
        warn "context_risk.subagent_heavy is true. Reduce subagent budget and avoid Explore escalation."
      fi
    fi
  fi
fi

# --- context_risk flags from latest checkpoint ---
CHECKPOINT_DIR="${REPO_ROOT}/${CHECKPOINT_PATH}"
if [[ -d "$CHECKPOINT_DIR" ]]; then
  LATEST_CHECKPOINT="$(find "$CHECKPOINT_DIR" -name "*.yaml" -not -name ".gitkeep" | sort | tail -1)"
  if [[ -n "$LATEST_CHECKPOINT" ]]; then
    _RISK=$(python3 -c "
try:
    import yaml
    with open('${LATEST_CHECKPOINT}') as f:
        d = yaml.safe_load(f)
    risk = d.get('orchestrator', {}).get('context_risk', {})
    import json; print(json.dumps(risk))
except Exception:
    print('{}')
" 2>/dev/null || echo "{}")

    # long_lived_session — warn: compact before continuing
    if echo "$_RISK" | python3 -c "import sys,json; r=json.load(sys.stdin); exit(0 if r.get('long_lived_session') else 1)" 2>/dev/null; then
      warn "context_risk.long_lived_session is true. Compact context before continuing."
    fi

    # checkpoint_stale — block: require refresh before dispatch
    if echo "$_RISK" | python3 -c "import sys,json; r=json.load(sys.stdin); exit(0 if r.get('checkpoint_stale') else 1)" 2>/dev/null; then
      block "context_risk.checkpoint_stale is true. Write a fresh LoopCheckpoint before dispatching."
    fi

    # reload_scope_too_large — warn on expensive read operations
    if [[ "$TOOL_NAME" == "Read" || "$TOOL_NAME" == "Bash" || "$TOOL_NAME" == "Glob" ]]; then
      if echo "$_RISK" | python3 -c "import sys,json; r=json.load(sys.stdin); exit(0 if r.get('reload_scope_too_large') else 1)" 2>/dev/null; then
        warn "context_risk.reload_scope_too_large is true. Prune warm/cold context before broad reads."
      fi
    fi
  fi
fi

# All checks passed
exit 0
