#!/usr/bin/env bash
# operations-center — OperationsCenter CLI

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
CONFIG_PATH="${OPERATIONS_CENTER_CONFIG:-${ROOT_DIR}/config/operations_center.local.yaml}"
ENV_PATH="${OPERATIONS_CENTER_ENV_FILE:-${ROOT_DIR}/.env.operations-center.local}"
BOOTSTRAP_STAMP="${VENV_DIR}/.operations-center-bootstrap"
LOG_DIR="${ROOT_DIR}/logs/local"
WATCH_DIR="${LOG_DIR}/watch-all"
REPORT_DIR="${ROOT_DIR}/tools/report/execution_plane"
PLANE_MANAGER="${ROOT_DIR}/deployment/plane/manage.sh"
JANITOR_MAX_AGE_DAYS="${OPERATIONS_CENTER_RETENTION_DAYS:-1}"
WATCHDOG_LOOP_LOCK="${LOG_DIR}/watchdog_loop.lock"

# Ensure the ContextLifecycle `cl` CLI is resolvable for watchers that shell out
# to it (pr_review_watcher ledger capture; spec_hygiene's LedgerMaintainTask
# promote/observe). Under systemd the unit PATH and the `bash -lc` login profile
# do not reliably include it, so `cl` resolves to nothing and the (best-effort)
# shell-outs silently no-op. Resolve it here and prepend the dir holding a
# working `cl` to PATH — inherited by the setsid `bash -lc` watchers. Reach the
# wrapper by its REAL path via $CL_HOME/bin (or the sibling checkout); do NOT
# symlink the wrapper, whose BASH_SOURCE self-location then mis-resolves its venv
# and recurses via its `command -v cl` fallback (a 30s hang). Best-effort: if no
# `cl` is found, the shell-outs degrade gracefully.
for _cl_dir in "${CL_HOME:-}/bin" "${ROOT_DIR}/../ContextLifecycle/bin"; do
  if [[ "${_cl_dir}" != "/bin" && -x "${_cl_dir}/cl" ]]; then
    case ":${PATH}:" in
      *":${_cl_dir}:"*) ;;                       # already on PATH
      *) PATH="${_cl_dir}:${PATH}"; export PATH ;;
    esac
    break
  fi
done
unset _cl_dir

# Anchor the fleet's ContextLifecycle sessions at the PlatformManifest manifest.
# OC's CLAUDE.md / ContextGuard (`.claude/hooks/pre_tool_use.sh`) BLOCK any Claude
# Code session whose CL_ANCHOR is unset — so an agent dispatched into the OC repo
# returns a prose refusal ("CL_ANCHOR is not set…") instead of a JSON plan, and
# every self-modify task fails as backend_error (non-JSON from agent). The fleet's
# systemd/login env carries no CL_ANCHOR, so set it here; the watchers and the
# agents they spawn (os.environ.copy()) inherit it. `cl_dispatch_wrap` activates
# but no-ops gracefully without an active session (SessionNotStarted is caught).
# Respect an operator-set CL_ANCHOR; else resolve the sibling PlatformManifest.
if [[ -z "${CL_ANCHOR:-}" ]]; then
  for _anchor in "${CL_HOME:-}/../PlatformManifest" "${ROOT_DIR}/../PlatformManifest"; do
    if [[ -d "${_anchor}/.context" ]]; then
      CL_ANCHOR="$(cd "${_anchor}" && pwd)"
      export CL_ANCHOR
      break
    fi
  done
  unset _anchor
fi

ensure_venv() {
  ensure_pip_conf
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    python3 -m venv "${VENV_DIR}"
  fi
  if [[ ! -f "${BOOTSTRAP_STAMP}" || "${ROOT_DIR}/pyproject.toml" -nt "${BOOTSTRAP_STAMP}" ]]; then
    "${VENV_DIR}/bin/python" -m pip install --upgrade pip
    # uv (not plain pip): honors pyproject [tool.uv] override-dependencies — the
    # capabilities-plane repograph pin (e0b205e) that plain pip silently drops.
    uv pip install --python "${VENV_DIR}/bin/python" -e '.[dev]'
    touch "${BOOTSTRAP_STAMP}"
  fi
  ensure_executor_backends
}

# The execute backends (team_executor, dag_executor) are sibling CHECKOUTS, not
# declared OC dependencies — `uv pip install -e .[dev]` never installs them and a
# `uv sync` / venv-recreate actively DROPS them. When that happens the executor
# can't load its backend ("team_executor not installed: No module named
# 'team_executor'") and EVERY goal task fails at execute → the whole lane stalls
# with no obvious cause. Self-heal: whenever the backends aren't importable, (re)install
# them editable. Runs every launch but the import check is ~free and the install only
# fires when actually missing, so a mid-life drop recovers on the next fleet start
# rather than blocking autonomy until a human notices.
ensure_executor_backends() {
  if "${VENV_DIR}/bin/python" -c "import team_executor, dag_executor" 2>/dev/null; then
    return 0
  fi
  echo "operations-center.sh: executor backends missing — (re)installing siblings" >&2
  local _sib
  for _sib in TeamExecutor DAGExecutor; do
    if [[ -f "${ROOT_DIR}/../${_sib}/pyproject.toml" ]]; then
      uv pip install --python "${VENV_DIR}/bin/python" -e "${ROOT_DIR}/../${_sib}" \
        || echo "operations-center.sh: WARNING failed to install ${_sib} — execute backend unavailable" >&2
    else
      echo "operations-center.sh: WARNING sibling checkout ${_sib} not found at ${ROOT_DIR}/../${_sib}" >&2
    fi
  done
}

# Ensure the user-level pip config requires a virtualenv for all pip installs.
# This prevents any bootstrapping process from accidentally depositing editable
# installs in the global site-packages.
# Safe to run repeatedly; skips if require-virtualenv is already set.
ensure_pip_conf() {
  local pip_conf="${XDG_CONFIG_HOME:-${HOME}/.config}/pip/pip.conf"
  mkdir -p "$(dirname "${pip_conf}")"
  if ! grep -q "require-virtualenv" "${pip_conf}" 2>/dev/null; then
    printf '[global]\nrequire-virtualenv = true\n' >> "${pip_conf}"
    echo "pip.conf: require-virtualenv = true added to ${pip_conf}"
  fi
}

load_env_file() {
  if [[ -f "${ENV_PATH}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_PATH}"
    set +a
  fi
}

maybe_open_browser() {
  if [[ "${OPERATIONS_CENTER_PLANE_OPEN_BROWSER:-}" != "1" ]]; then
    return 0
  fi
  if [[ -z "${OPERATIONS_CENTER_PLANE_URL:-}" ]]; then
    return 0
  fi
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "${OPERATIONS_CENTER_PLANE_URL}" >/dev/null 2>&1 || true
  fi
}

timestamp() {
  date +"%Y%m%dT%H%M%S"
}

run_with_log() {
  local name="$1"
  shift
  mkdir -p "${LOG_DIR}"
  local log_path="${LOG_DIR}/$(timestamp)_${name}.log"
  echo "Writing log: ${log_path}"
  "$@" 2>&1 | tee "${log_path}"
}

run_janitor() {
  local max_age_minutes=$((JANITOR_MAX_AGE_DAYS * 24 * 60))
  [[ "${max_age_minutes}" -lt 0 ]] && max_age_minutes=1440

  mkdir -p "${LOG_DIR}" "${WATCH_DIR}" "${REPORT_DIR}"

  while IFS= read -r path; do
    rm -f "${path}"
  done < <(find "${LOG_DIR}" -type f ! -name "*.pid" -mmin +"${max_age_minutes}" -print)

  while IFS= read -r path; do
    rm -f "${path}"
  done < <(find "${WATCH_DIR}" -type f -name "*.pid" -mmin +"${max_age_minutes}" -print)

  while IFS= read -r path; do
    rm -rf "${path}"
  done < <(find "${REPORT_DIR}" -mindepth 1 -maxdepth 1 -type d -mmin +"${max_age_minutes}" -print)

  find "${LOG_DIR}" -depth -type d -empty -delete >/dev/null 2>&1 || true

  # Clean up stale task branches left in local repo clones by the executor.
  # Branches matching "task/*" or "cp/*" whose worktrees no longer exist are removed.
  _cleanup_stale_task_branches
}

_cleanup_stale_task_branches() {
  local branch deleted=0
  # Only act on repos that are locally cloned (have a .git directory).
  for repo_dir in "${ROOT_DIR}"/workspace/*/; do
    [[ -d "${repo_dir}/.git" ]] || continue
    while IFS= read -r branch; do
      branch="${branch#  }"  # strip leading whitespace
      # Skip the current branch and remote-tracking refs.
      [[ "${branch}" == "* "* ]] && continue
      [[ "${branch}" == remotes/* ]] && continue
      # Only prune branches that look like task branches.
      if [[ "${branch}" =~ ^(task/|cp/|plane/) ]]; then
        git -C "${repo_dir}" branch -D "${branch}" >/dev/null 2>&1 && ((deleted++)) || true
      fi
    done < <(git -C "${repo_dir}" branch 2>/dev/null | grep -E '^\s*(task/|cp/|plane/)' || true)
  done
  [[ "${deleted}" -gt 0 ]] && echo "Janitor: removed ${deleted} stale task branch(es)" || true
}

usage() {
  cat <<EOF
Usage:
  scripts/operations-center.sh setup
  scripts/operations-center.sh start
  scripts/operations-center.sh stop
  scripts/operations-center.sh run-next
  scripts/operations-center.sh watch-all
  scripts/operations-center.sh watch-all-stop
  scripts/operations-center.sh watch-all-status
  scripts/operations-center.sh intake [--once]
  scripts/operations-center.sh status
  scripts/operations-center.sh watchdog
  scripts/operations-center.sh dev-status
  scripts/operations-center.sh watch --role goal
  scripts/operations-center.sh watch --role review
  scripts/operations-center.sh watch-stop --role goal
  scripts/operations-center.sh run --task-id TASK-123
  scripts/operations-center.sh plane-doctor [--task-id TASK-123]
  scripts/operations-center.sh dependency-check [--create-plane-tasks]
  scripts/operations-center.sh janitor
  scripts/operations-center.sh plane-up
  scripts/operations-center.sh plane-down
  scripts/operations-center.sh plane-status
  scripts/operations-center.sh dev-up
  scripts/operations-center.sh dev-down
  scripts/operations-center.sh dev-down-safe
  scripts/operations-center.sh dev-restart
  scripts/operations-center.sh providers-status
  scripts/operations-center.sh doctor
  scripts/operations-center.sh test
  scripts/operations-center.sh worker --task-id TASK-123
  scripts/operations-center.sh smoke --task-id TASK-123 --comment-only
  scripts/operations-center.sh observe-repo [--repo /abs/path]
  scripts/operations-center.sh generate-insights [--repo /abs/path]
  scripts/operations-center.sh decide-proposals [--repo /abs/path]
  scripts/operations-center.sh propose-from-candidates [--repo /abs/path] [--dry-run]
  scripts/operations-center.sh autonomy-cycle --config FILE [--repo PATH] [--execute] [--all-families]
  scripts/operations-center.sh analyze-artifacts [--repo NAME] [--limit N] [--json]
  scripts/operations-center.sh tune-autonomy [--window N] [--apply]
  scripts/operations-center.sh promote-backlog [--family FAMILY] [--execute]
  scripts/operations-center.sh worker-backend-status [--json]
  scripts/operations-center.sh worker-backend-probe [--json] [--timeout N]
  scripts/operations-center.sh lineage [TASK-ID] [--json]
  scripts/operations-center.sh watchdog-loop-acquire
  scripts/operations-center.sh watchdog-loop-release
  scripts/operations-center.sh watchdog-loop-status
  scripts/operations-center.sh loop-start
  scripts/operations-center.sh loop-stop
  scripts/operations-center.sh loop-status
  scripts/operations-center.sh loop-log

Environment:
  OPERATIONS_CENTER_CONFIG   Override config path (default: ${CONFIG_PATH})
  OPERATIONS_CENTER_ENV_FILE Override env file path (default: ${ENV_PATH})
EOF
}

# ── Platform Watchdog Loop ownership lock ────────────────────────────────────
# Prevents two Claude Code sessions from running the same platform loop at once.
# Lock file: logs/local/watchdog_loop.lock (JSON: pid, timestamp, hostname,
# repo_root, purpose). Uses PID liveness — stale locks are auto-reclaimed.

acquire_watchdog_loop_lock() {
  mkdir -p "${LOG_DIR}"
  if [[ -f "${WATCHDOG_LOOP_LOCK}" ]]; then
    local lock_pid
    lock_pid=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('pid',''))" \
               "${WATCHDOG_LOOP_LOCK}" 2>/dev/null || echo "")
    if [[ -n "${lock_pid}" ]] && kill -0 "${lock_pid}" 2>/dev/null; then
      echo "ERROR: watchdog loop lock held by live PID ${lock_pid} — aborting." >&2
      cat "${WATCHDOG_LOOP_LOCK}" >&2
      return 1
    else
      echo "Reclaiming stale watchdog loop lock (PID ${lock_pid:-unknown} is dead)."
    fi
  fi
  local shell_pid=${PPID}
  python3 - "${WATCHDOG_LOOP_LOCK}" "${ROOT_DIR}" "${shell_pid}" <<'PY'
import json, socket, sys
from datetime import datetime
path, repo_root, pid = sys.argv[1], sys.argv[2], int(sys.argv[3])
payload = {
    "pid":       pid,
    "timestamp": datetime.now().isoformat(timespec="seconds"),
    "hostname":  socket.gethostname(),
    "repo_root": repo_root,
    "purpose":   "OC Platform Watchdog Loop",
}
with open(path, "w") as f:
    json.dump(payload, f, indent=2)
print(f"watchdog loop lock acquired: pid={pid} lock={path}")
PY
}

release_watchdog_loop_lock() {
  if [[ -f "${WATCHDOG_LOOP_LOCK}" ]]; then
    rm -f "${WATCHDOG_LOOP_LOCK}"
    echo "watchdog loop lock released: ${WATCHDOG_LOOP_LOCK}"
  else
    echo "watchdog loop lock not held"
  fi
}

check_watchdog_loop_lock() {
  if [[ ! -f "${WATCHDOG_LOOP_LOCK}" ]]; then
    echo "watchdog loop: unlocked"
    return 0
  fi
  local lock_pid
  lock_pid=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('pid',''))" \
             "${WATCHDOG_LOOP_LOCK}" 2>/dev/null || echo "")
  if [[ -n "${lock_pid}" ]] && kill -0 "${lock_pid}" 2>/dev/null; then
    echo "watchdog loop: LOCKED (live PID ${lock_pid})"
    cat "${WATCHDOG_LOOP_LOCK}"
    return 1
  else
    echo "watchdog loop: stale lock (PID ${lock_pid:-unknown} is dead — safe to reclaim)"
    cat "${WATCHDOG_LOOP_LOCK}"
    return 2
  fi
}

LOOP_CONTROLLER_LOCK="${LOG_DIR}/loop_controller.lock"
LOOP_STOP_FLAG="${LOG_DIR}/loop_stop.flag"
LOOP_LOG="${LOG_DIR}/loop_controller.log"

loop_start() {
  rm -f "${LOOP_STOP_FLAG}"
  nohup python3 "${ROOT_DIR}/tools/loop/controller.py" > /dev/null 2>&1 &
  sleep 1
  python3 "${ROOT_DIR}/tools/loop/controller.py" --status
  echo "Log: ${LOOP_LOG}"
}

loop_stop() {
  python3 "${ROOT_DIR}/tools/loop/controller.py" --stop
}

loop_status() {
  python3 "${ROOT_DIR}/tools/loop/controller.py" --status
}

loop_log() {
  tail -f "${LOOP_LOG}"
}

watch_pid_file() {
  local role="$1"
  echo "${WATCH_DIR}/${role}.pid"
}

watch_log_file() {
  local role="$1"
  echo "${WATCH_DIR}/$(timestamp)_${role}.log"
}

watch_status_file() {
  local role="$1"
  echo "${WATCH_DIR}/${role}.status.json"
}

start_watch_role() {
  local role="$1"
  local poll_interval=20
  case "${role}" in
    goal) poll_interval="${OPERATIONS_CENTER_WATCH_INTERVAL_GOAL_SECONDS:-${OPERATIONS_CENTER_GOAL_POLL_SECONDS:-30}}" ;;
    test) poll_interval="${OPERATIONS_CENTER_WATCH_INTERVAL_TEST_SECONDS:-${OPERATIONS_CENTER_TEST_POLL_SECONDS:-60}}" ;;
    improve) poll_interval="${OPERATIONS_CENTER_WATCH_INTERVAL_IMPROVE_SECONDS:-${OPERATIONS_CENTER_IMPROVE_POLL_SECONDS:-60}}" ;;
    propose) poll_interval="${OPERATIONS_CENTER_WATCH_INTERVAL_PROPOSE_SECONDS:-${OPERATIONS_CENTER_PROPOSE_POLL_SECONDS:-120}}" ;;
    review) poll_interval="${OPERATIONS_CENTER_WATCH_INTERVAL_REVIEW_SECONDS:-60}" ;;
    spec) poll_interval="${OPERATIONS_CENTER_WATCH_INTERVAL_SPEC_SECONDS:-120}" ;;
    intake) ;; # handled separately below
    *)
      echo "start_watch_role: unknown role '${role}' (valid: goal, test, improve, propose, review, spec, intake)" >&2
      return 1
      ;;
  esac
  local pid_file
  pid_file="$(watch_pid_file "${role}")"
  if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" >/dev/null 2>&1; then
    echo "watch-${role} already running with PID $(cat "${pid_file}")"
    return 0
  fi
  rm -f "${pid_file}"
  mkdir -p "${WATCH_DIR}"
  local log_file
  log_file="$(watch_log_file "${role}")"
  # The outer bash wrapper restarts the watcher automatically on any non-zero exit.
  # Deliberate stops are communicated via PID file deletion — stop_watch_role removes
  # the PID file before killing the Python child, so the wrapper exits cleanly on the
  # next check rather than looping.  Any Python exit (including code 0) is treated as
  # a crash and triggers a restart with a 30-second backoff.
  if [[ "${role}" == "intake" ]]; then
    setsid /bin/bash -lc "
      cd '${ROOT_DIR}'
      set -a
      source '${ENV_PATH}'
      set +a
      _child_pid=''
      trap 'kill \$_child_pid 2>/dev/null; exit 0' TERM INT
      while true; do
        set -a
        source '${ENV_PATH}' 2>/dev/null || true
        set +a
        '${VENV_DIR}/bin/python' -m operations_center.entrypoints.intake.main \
          --config '${CONFIG_PATH}' \
          --status-dir '${WATCH_DIR}' &
        _child_pid=\$!
        wait \$_child_pid
        _exit=\$?
        [[ ! -f '${pid_file}' ]] && exit 0
        echo \"{\\\"event\\\":\\\"watcher_restart\\\",\\\"role\\\":\\\"${role}\\\",\\\"exit_code\\\":\$_exit}\"
        sleep 30
      done
    " >>"${log_file}" 2>&1 < /dev/null &
  elif [[ "${role}" == "review" ]]; then
    setsid /bin/bash -lc "
      cd '${ROOT_DIR}'
      set -a
      source '${ENV_PATH}'
      set +a
      _child_pid=''
      trap 'kill \$_child_pid 2>/dev/null; exit 0' TERM INT
      while true; do
        set -a
        source '${ENV_PATH}' 2>/dev/null || true
        set +a
        '${VENV_DIR}/bin/python' -m operations_center.entrypoints.reviewer.main \
          --config '${CONFIG_PATH}' \
          --watch \
          --poll-interval-seconds '${poll_interval}' \
          --status-dir '${WATCH_DIR}' &
        _child_pid=\$!
        wait \$_child_pid
        _exit=\$?
        [[ ! -f '${pid_file}' ]] && exit 0
        echo \"{\\\"event\\\":\\\"watcher_restart\\\",\\\"role\\\":\\\"${role}\\\",\\\"exit_code\\\":\$_exit}\"
        sleep 30
      done
    " >>"${log_file}" 2>&1 < /dev/null &
  elif [[ "${role}" == "spec" ]]; then
    # ADR 0007 Phase F: spec_director retired. The `spec` role now supervises
    # three sibling watchers — spec_hygiene (board hygiene + active.json
    # projection), spec_trigger (drop-file / queue-drain detection that
    # emits spec-author Plane tasks), and a board_worker for the spec-author
    # role (executes spec-author tasks through the backend pipeline). See
    # docs/architecture/adr/0007-spec-director-refactor.md.
    setsid /bin/bash -lc "
      cd '${ROOT_DIR}'
      set -a
      source '${ENV_PATH}'
      set +a
      _hyg_pid=''
      _trig_pid=''
      _bw_pid=''
      trap 'kill \$_hyg_pid \$_trig_pid \$_bw_pid 2>/dev/null; exit 0' TERM INT
      while true; do
        set -a
        source '${ENV_PATH}' 2>/dev/null || true
        set +a
        '${VENV_DIR}/bin/python' -u -m operations_center.entrypoints.spec_hygiene.main \
          --config '${CONFIG_PATH}' \
          --status-dir '${WATCH_DIR}' &
        _hyg_pid=\$!
        '${VENV_DIR}/bin/python' -u -m operations_center.entrypoints.spec_trigger.main \
          --config '${CONFIG_PATH}' \
          --status-dir '${WATCH_DIR}' &
        _trig_pid=\$!
        '${VENV_DIR}/bin/python' -m operations_center.entrypoints.board_worker.main \
          --config '${CONFIG_PATH}' \
          --role 'spec-author' \
          --poll-interval-seconds '60' \
          --status-dir '${WATCH_DIR}' &
        _bw_pid=\$!
        # If any sibling exits, kill the others and restart the group.
        wait -n \$_hyg_pid \$_trig_pid \$_bw_pid
        _exit=\$?
        kill \$_hyg_pid \$_trig_pid \$_bw_pid 2>/dev/null || true
        wait \$_hyg_pid 2>/dev/null || true
        wait \$_trig_pid 2>/dev/null || true
        wait \$_bw_pid 2>/dev/null || true
        [[ ! -f '${pid_file}' ]] && exit 0
        echo \"{\\\"event\\\":\\\"watcher_restart\\\",\\\"role\\\":\\\"${role}\\\",\\\"exit_code\\\":\$_exit}\"
        sleep 30
      done
    " >>"${log_file}" 2>&1 < /dev/null &
  elif [[ "${role}" == "propose" ]]; then
    setsid /bin/bash -lc "
      cd '${ROOT_DIR}'
      set -a
      source '${ENV_PATH}'
      set +a
      _child_pid=''
      _hb_pid=''
      trap 'kill \$_hb_pid 2>/dev/null; kill \$_child_pid 2>/dev/null; exit 0' TERM INT
      while [[ -f '${pid_file}' ]]; do
        printf '{\"role\":\"propose\",\"at\":\"%s\",\"status\":\"idle\"}\n' \
          \$(date -u +%Y-%m-%dT%H:%M:%S+00:00) \
          > '${WATCH_DIR}/heartbeat_propose.json'
        sleep 60
      done &
      _hb_pid=\$!
      while true; do
        set -a
        source '${ENV_PATH}' 2>/dev/null || true
        set +a
        '${VENV_DIR}/bin/python' -m operations_center.entrypoints.pipeline_trigger.main \
          --config '${CONFIG_PATH}' \
          --execute &
        _child_pid=\$!
        wait \$_child_pid
        _exit=\$?
        [[ ! -f '${pid_file}' ]] && break
        echo \"{\\\"event\\\":\\\"watcher_restart\\\",\\\"role\\\":\\\"propose\\\",\\\"exit_code\\\":\$_exit}\"
        sleep 30
      done
      kill \$_hb_pid 2>/dev/null
    " >>"${log_file}" 2>&1 < /dev/null &
  else
    # goal, test, improve — Plane-polling board workers
    setsid /bin/bash -lc "
      cd '${ROOT_DIR}'
      set -a
      source '${ENV_PATH}'
      set +a
      _child_pid=''
      trap 'kill \$_child_pid 2>/dev/null; exit 0' TERM INT
      while true; do
        set -a
        source '${ENV_PATH}' 2>/dev/null || true
        set +a
        '${VENV_DIR}/bin/python' -m operations_center.entrypoints.board_worker.main \
          --config '${CONFIG_PATH}' \
          --role '${role}' \
          --poll-interval-seconds '${poll_interval}' \
          --status-dir '${WATCH_DIR}' &
        _child_pid=\$!
        wait \$_child_pid
        _exit=\$?
        [[ ! -f '${pid_file}' ]] && exit 0
        echo \"{\\\"event\\\":\\\"watcher_restart\\\",\\\"role\\\":\\\"${role}\\\",\\\"exit_code\\\":\$_exit}\"
        sleep 30
      done
    " >>"${log_file}" 2>&1 < /dev/null &
  fi
  local pid=$!
  echo "${pid}" > "${pid_file}"
  echo "watch-${role} started (auto-restart enabled): pid=${pid} poll_interval=${poll_interval}s log=${log_file}"
}

stop_watch_role() {
  local role="$1"
  local pid_file
  pid_file="$(watch_pid_file "${role}")"

  # Always kill any stray watcher processes for this role, regardless of the
  # PID file.  Multiple watcher processes for the same role can accumulate if
  # previous stops failed or the PID file was overwritten without terminating
  # the old process (e.g. after a crash or SIGKILL during restart).
  local stray_count=0
  while IFS= read -r stray_pid; do
    if [[ -n "${stray_pid}" ]] && kill -0 "${stray_pid}" >/dev/null 2>&1; then
      kill "${stray_pid}" >/dev/null 2>&1 || true
      stray_count=$((stray_count + 1))
    fi
  done < <(pgrep -f "worker.main.*--role ${role}" 2>/dev/null || true)
  if [[ "${stray_count}" -gt 0 ]]; then
    echo "watch-${role} stopped: killed ${stray_count} process(es) matching --role ${role}"
  fi

  if [[ ! -f "${pid_file}" ]]; then
    [[ "${stray_count}" -eq 0 ]] && echo "watch-${role} is not running"
    return 0
  fi
  rm -f "${pid_file}"
}

start_watchdog() {
  local pid_file="${WATCH_DIR}/watchdog.pid"
  if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" >/dev/null 2>&1; then
    echo "watchdog already running with PID $(cat "${pid_file}")"
    return 0
  fi
  rm -f "${pid_file}"
  mkdir -p "${WATCH_DIR}"
  local log_file="${WATCH_DIR}/$(timestamp)_watchdog.log"
  # Runs every hour; revives any watcher whose PID file exists but PID is dead.
  # Exits when its own PID file is removed (by stop_watchdog / dev-down).
  setsid /bin/bash -lc "
    cd '${ROOT_DIR}'
    set -a
    source '${ENV_PATH}'
    set +a
    _ROLES='goal test improve propose review spec intake'
    while [[ -f '${pid_file}' ]]; do
      printf '{\"role\":\"watchdog\",\"at\":\"%s\",\"status\":\"idle\"}\n' \
        \$(date -u +%Y-%m-%dT%H:%M:%S+00:00) \
        > '${WATCH_DIR}/heartbeat_watchdog.json'
      # Periodic probe-and-clear (~hourly): retract worker-backend cooldowns whose
      # limit lifted before the estimated reset, so status surfaces self-heal even
      # when the board is idle. No-op when nothing is cooling.
      '${ROOT_DIR}/scripts/operations-center.sh' worker-backend-probe --timeout 30 >/dev/null 2>&1 || true
      # Sandbox base-branch preflight (~hourly): ensure each repo's
      # sandbox_base_branch exists on origin (heal from default if missing), so a
      # queue of tasks doesn't stall serially discovering it deep in execution.
      '${VENV_DIR}/bin/operations-center-verify-sandbox-branches' --config '${CONFIG_PATH}' --heal 2>&1 || true
      # Convergence stall breaker (~hourly): when a candidate family's Blocked
      # tasks fail identically (env/transport), escalate to the operator ledger
      # and suppress re-proposal so the propose->fail->re-propose loop converges.
      '${VENV_DIR}/bin/operations-center-detect-convergence-stall' --config '${CONFIG_PATH}' --apply 2>&1 || true
      _slept=0
      while [[ \$_slept -lt 3600 && -f '${pid_file}' ]]; do
        sleep 300
        _slept=\$((_slept + 300))
        printf '{\"role\":\"watchdog\",\"at\":\"%s\",\"status\":\"idle\"}\n' \
          \$(date -u +%Y-%m-%dT%H:%M:%S+00:00) \
          > '${WATCH_DIR}/heartbeat_watchdog.json'
      done
      [[ ! -f '${pid_file}' ]] && break
      for _r in \$_ROLES; do
        _pf='${WATCH_DIR}'/\"\$_r\".pid
        [[ ! -f \"\$_pf\" ]] && continue
        _p=\$(cat \"\$_pf\" 2>/dev/null)
        [[ -z \"\$_p\" ]] && continue
        if ! kill -0 \"\$_p\" >/dev/null 2>&1; then
          echo \"{\\\"event\\\":\\\"watchdog_revive\\\",\\\"role\\\":\\\"\$_r\\\",\\\"stale_pid\\\":\\\"\$_p\\\"}\"
          '${ROOT_DIR}/scripts/operations-center.sh' watch --role \"\$_r\"
        fi
      done
      # Liveness-SUCCESS enforcement (determinism surface 10): the PID revive
      # above only catches DEAD watchers. controller_liveness catches a watcher
      # that is LIVE but not succeeding (e.g. spec_hygiene crash-looping) — the
      # blind spot HeartbeatStallTask can't cover for its own host — and SIGTERMs
      # its supervisor so the revive restarts it. Pairs: pid-role:heartbeat-role.
      for _pair in spec:spec_hygiene review:review goal:goal test:test improve:improve; do
        _pidrole=\${_pair%%:*}; _hbrole=\${_pair##*:}
        '${VENV_DIR}/bin/python' -m operations_center.entrypoints.controller_liveness \
          --role \"\$_hbrole\" --status-dir '${WATCH_DIR}' \
          --enforce --pid-file '${WATCH_DIR}'/\"\$_pidrole\".pid >/dev/null 2>&1 || true
      done
    done
    echo '{\"event\":\"watchdog_exit\"}'
  " >>"${log_file}" 2>&1 < /dev/null &
  echo $! > "${pid_file}"
  echo "watchdog started: pid=$! log=${log_file}"
}

stop_watchdog() {
  local pid_file="${WATCH_DIR}/watchdog.pid"
  if [[ ! -f "${pid_file}" ]]; then
    echo "watchdog is not running"
    return 0
  fi
  local pid
  pid=$(cat "${pid_file}")
  rm -f "${pid_file}"
  if kill -0 "${pid}" >/dev/null 2>&1; then
    kill "${pid}" >/dev/null 2>&1 || true
    echo "watchdog stopped (pid ${pid})"
  else
    echo "watchdog was not running (stale pid ${pid})"
  fi
}

status_watchdog() {
  local pid_file="${WATCH_DIR}/watchdog.pid"
  if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" >/dev/null 2>&1; then
    echo "watchdog: running (pid $(cat "${pid_file}"))"
  else
    echo "watchdog: stopped"
  fi
}

status_watch_role() {
  local role="$1"
  local pid_file
  local status_file
  pid_file="$(watch_pid_file "${role}")"
  status_file="$(watch_status_file "${role}")"
  if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" >/dev/null 2>&1; then
    if [[ -f "${status_file}" ]]; then
      python3 - "${role}" "${pid_file}" "${status_file}" <<'PY'
import json, sys
role, pid_file, status_file = sys.argv[1:]
pid = open(pid_file).read().strip()
data = json.load(open(status_file))
counters = data.get("counters", {})
print(
    f"watch-{role}: running (pid {pid}) | "
    f"cycle={data.get('cycle')} state={data.get('state')} last_action={data.get('last_action')} "
    f"task_id={data.get('task_id') or '-'} task_kind={data.get('task_kind') or '-'} "
    f"followups={len(data.get('follow_up_task_ids') or [])} triaged={counters.get('blocked_tasks_triaged', 0)} "
    f"created={counters.get('follow_up_tasks_created', 0)} updated_at={data.get('updated_at')}"
)
PY
    else
      echo "watch-${role}: running (pid $(cat "${pid_file}"))"
    fi
  else
    if [[ -f "${status_file}" ]]; then
      python3 - "${role}" "${status_file}" <<'PY'
import json, sys
role, status_file = sys.argv[1:]
data = json.load(open(status_file))
print(
    f"watch-{role}: stopped | "
    f"last_cycle={data.get('cycle')} state={data.get('state')} last_action={data.get('last_action')} "
    f"task_id={data.get('task_id') or '-'} updated_at={data.get('updated_at')}"
)
PY
    else
      echo "watch-${role}: stopped"
    fi
  fi
}

cmd="${1:-}"
if [[ -z "${cmd}" ]]; then
  usage
  exit 1
fi
shift || true

cd "${ROOT_DIR}"
# Skip janitor for read-only / stop commands — they're fast and don't need it.
case "${cmd}" in
  watch-all-status|dev-status|watch-all-stop|watch-stop|watchdog-stop|plane-status|providers-status|doctor|status|worker-backend-status|worker-backend-probe|loop-start|loop-stop|loop-status|loop-log) ;;
  *) run_janitor ;;
esac

case "${cmd}" in
  setup)
    ensure_venv
    run_with_log setup "${VENV_DIR}/bin/python" -m operations_center.entrypoints.setup.main "$@"
    ;;
  start|plane-up)
    load_env_file
    run_with_log plane-up "${PLANE_MANAGER}" up
    maybe_open_browser
    ;;
  stop|plane-down)
    load_env_file
    run_with_log plane-down "${PLANE_MANAGER}" down
    ;;
  plane-status)
    load_env_file
    run_with_log plane-status "${PLANE_MANAGER}" status
    ;;
  dev-up)
    ensure_venv
    load_env_file
    run_with_log plane-up "${PLANE_MANAGER}" up
    maybe_open_browser
    start_watch_role intake
    start_watch_role goal
    start_watch_role test
    start_watch_role improve
    start_watch_role propose
    start_watch_role review
    start_watch_role spec
    start_watchdog
    run_with_log plane-status "${PLANE_MANAGER}" status
    ;;
  dev-down)
    load_env_file
    stop_watchdog
    stop_watch_role intake
    stop_watch_role goal
    stop_watch_role test
    stop_watch_role improve
    stop_watch_role propose
    stop_watch_role review
    stop_watch_role spec
    run_with_log plane-down "${PLANE_MANAGER}" down
    ;;
  dev-down-safe)
    ensure_venv
    load_env_file
    # Stop watchdog first so it doesn't revive roles during drain.
    stop_watchdog
    # Stop watchers from claiming new tasks.
    stop_watch_role intake
    stop_watch_role goal
    stop_watch_role test
    stop_watch_role improve
    stop_watch_role propose
    stop_watch_role review
    stop_watch_role spec
    # Poll until all Running tasks complete or timeout is reached.
    _safe_timeout="${OPERATIONS_CENTER_SAFE_DOWN_TIMEOUT_SECONDS:-600}"
    _safe_start=$(date +%s)
    echo "Waiting for in-flight tasks (timeout: ${_safe_timeout}s)..."
    while true; do
      _elapsed=$(( $(date +%s) - _safe_start ))
      if [[ $_elapsed -ge $_safe_timeout ]]; then
        echo "Timeout after ${_elapsed}s — proceeding with forced shutdown"
        break
      fi
      _running=$("${VENV_DIR}/bin/python" - <<PYEOF 2>/dev/null || echo "0"
import sys
sys.path.insert(0, '${ROOT_DIR}/src')
from operations_center.settings import load_settings
from operations_center.adapters.plane.client import PlaneClient
s = load_settings('${CONFIG_PATH}')
c = PlaneClient(base_url=s.plane.base_url, api_token=s.plane_token(),
                workspace_slug=s.plane.workspace_slug, project_id=s.plane.project_id)
try:
    issues = c.list_issues()
    running = [i for i in issues if (i.get('state') or {}).get('name') == 'Running']
    print(len(running))
finally:
    c.close()
PYEOF
)
      if [[ "${_running}" == "0" ]]; then
        echo "No tasks in Running state — safe to shut down"
        break
      fi
      echo "  ${_running} task(s) still Running (${_elapsed}s elapsed) — waiting 30s..."
      sleep 30
    done
    run_with_log plane-down "${PLANE_MANAGER}" down
    ;;
  dev-restart)
    load_env_file
    stop_watchdog
    stop_watch_role intake
    stop_watch_role goal
    stop_watch_role test
    stop_watch_role improve
    stop_watch_role propose
    stop_watch_role review
    stop_watch_role spec
    run_with_log plane-down "${PLANE_MANAGER}" down
    ensure_venv
    run_with_log plane-up "${PLANE_MANAGER}" up
    maybe_open_browser
    start_watch_role intake
    start_watch_role goal
    start_watch_role test
    start_watch_role improve
    start_watch_role propose
    start_watch_role review
    start_watch_role spec
    start_watchdog
    run_with_log plane-status "${PLANE_MANAGER}" status
    ;;
  dev-status)
    load_env_file
    run_with_log plane-status "${PLANE_MANAGER}" status || true
    status_watch_role intake
    status_watch_role goal
    status_watch_role test
    status_watch_role improve
    status_watch_role propose
    status_watch_role review
    status_watch_role spec
    status_watchdog
    ;;
  providers-status|doctor)
    ensure_venv
    load_env_file
    run_with_log providers-status "${VENV_DIR}/bin/python" -m operations_center.entrypoints.setup.doctor "$@"
    ;;
  test)
    ensure_venv
    run_with_log test "${VENV_DIR}/bin/pytest" -q "$@"
    ;;
  run)
    ensure_venv
    load_env_file
    run_with_log worker "${VENV_DIR}/bin/python" -m operations_center.entrypoints.worker.main --config "${CONFIG_PATH}" "$@"
    ;;
  run-next)
    ensure_venv
    load_env_file
    run_with_log worker "${VENV_DIR}/bin/python" -m operations_center.entrypoints.worker.main --config "${CONFIG_PATH}" --first-ready "$@"
    ;;
  watch)
    ensure_venv
    load_env_file
    # Parse --role from args so we can dispatch through start_watch_role,
    # which handles the reviewer entrypoint, pid files, and auto-restart.
    _watch_role=""
    for _arg in "$@"; do
      if [[ "${_watch_role}" == "__next__" ]]; then
        _watch_role="${_arg}"
        break
      fi
      [[ "${_arg}" == "--role" ]] && _watch_role="__next__"
    done
    if [[ -n "${_watch_role}" && "${_watch_role}" != "__next__" ]]; then
      start_watch_role "${_watch_role}"
    else
      # No --role given: run worker inline (foreground, for debugging).
      run_with_log worker "${VENV_DIR}/bin/python" -m operations_center.entrypoints.worker.main --config "${CONFIG_PATH}" --watch "$@"
    fi
    ;;
  watch-stop)
    # Stop a single watcher role: scripts/operations-center.sh watch-stop --role goal
    _stop_role=""
    for _arg in "$@"; do
      if [[ "${_stop_role}" == "__next__" ]]; then
        _stop_role="${_arg}"
        break
      fi
      [[ "${_arg}" == "--role" ]] && _stop_role="__next__"
    done
    if [[ -n "${_stop_role}" && "${_stop_role}" != "__next__" ]]; then
      stop_watch_role "${_stop_role}"
    else
      echo "Usage: watch-stop --role <role>" >&2
      exit 1
    fi
    ;;
  watch-all)
    ensure_venv
    load_env_file
    start_watch_role intake
    start_watch_role goal
    start_watch_role test
    start_watch_role improve
    start_watch_role propose
    start_watch_role review
    start_watch_role spec
    start_watchdog
    ;;
  watch-all-stop)
    stop_watchdog
    stop_watch_role intake
    stop_watch_role goal
    stop_watch_role test
    stop_watch_role improve
    stop_watch_role propose
    stop_watch_role review
    stop_watch_role spec
    ;;
  watch-all-status)
    status_watch_role intake
    status_watch_role goal
    status_watch_role test
    status_watch_role improve
    status_watch_role propose
    status_watch_role review
    status_watch_role spec
    status_watchdog
    ;;
  status)
    status_watchdog
    ;;
  worker)
    ensure_venv
    load_env_file
    run_with_log worker "${VENV_DIR}/bin/python" -m operations_center.entrypoints.worker.main --config "${CONFIG_PATH}" "$@"
    ;;
  smoke)
    ensure_venv
    load_env_file
    run_with_log smoke "${VENV_DIR}/bin/python" -m operations_center.entrypoints.smoke.plane --config "${CONFIG_PATH}" "$@"
    ;;
  observe-repo)
    ensure_venv
    load_env_file
    run_with_log observe-repo "${VENV_DIR}/bin/python" -m operations_center.entrypoints.observer.main --config "${CONFIG_PATH}" "$@"
    ;;
  backfill-pr-reviews)
    ensure_venv
    load_env_file
    run_with_log backfill-pr-reviews "${VENV_DIR}/bin/python" -m operations_center.entrypoints.reviewer.main --config "${CONFIG_PATH}" --backfill "$@"
    ;;
  generate-insights)
    ensure_venv
    load_env_file
    run_with_log generate-insights "${VENV_DIR}/bin/python" -m operations_center.entrypoints.insights.main "$@"
    ;;
  decide-proposals)
    ensure_venv
    load_env_file
    run_with_log decide-proposals "${VENV_DIR}/bin/python" -m operations_center.entrypoints.decision.main "$@"
    ;;
  propose-from-candidates)
    ensure_venv
    load_env_file
    run_with_log propose-from-candidates "${VENV_DIR}/bin/python" -m operations_center.entrypoints.proposer.main --config "${CONFIG_PATH}" "$@"
    ;;
  autonomy-cycle)
    ensure_venv
    load_env_file
    run_with_log autonomy-cycle "${VENV_DIR}/bin/python" -m operations_center.entrypoints.autonomy_cycle.main --config "${CONFIG_PATH}" "$@"
    ;;
  analyze-artifacts)
    ensure_venv
    load_env_file
    run_with_log analyze-artifacts "${VENV_DIR}/bin/python" -m operations_center.entrypoints.analyze.main "$@"
    ;;
  tune-autonomy)
    ensure_venv
    load_env_file
    run_with_log tune-autonomy "${VENV_DIR}/bin/python" -m operations_center.entrypoints.tuning.main --config "${CONFIG_PATH}" "$@"
    ;;
  promote-backlog)
    ensure_venv
    load_env_file
    run_with_log promote-backlog "${VENV_DIR}/bin/python" -m operations_center.entrypoints.promote_backlog.main --config "${CONFIG_PATH}" "$@"
    ;;
  worker-backend-status)
    ensure_venv
    load_env_file
    "${VENV_DIR}/bin/python" -m operations_center.entrypoints.worker_backend_status.main "$@"
    ;;
  worker-backend-probe)
    ensure_venv
    load_env_file
    "${VENV_DIR}/bin/python" -m operations_center.entrypoints.worker_backend_probe.main "$@"
    ;;
  lineage)
    # Execution-lineage read-model (trust-labeled, display-only). The CLI was
    # complete but unreachable from operator tooling — this verb wires it in.
    ensure_venv
    load_env_file
    "${VENV_DIR}/bin/python" -m operations_center.lineage.cli "$@"
    ;;
  plane-doctor)
    ensure_venv
    load_env_file
    run_with_log plane-doctor "${VENV_DIR}/bin/python" -m operations_center.entrypoints.smoke.plane_doctor --config "${CONFIG_PATH}" "$@"
    ;;
  dependency-check)
    ensure_venv
    load_env_file
    run_with_log dependency-check "${VENV_DIR}/bin/python" -m operations_center.entrypoints.maintenance.dependency_check --config "${CONFIG_PATH}" "$@"
    ;;
  intake)
    ensure_venv
    load_env_file
    run_with_log intake "${VENV_DIR}/bin/python" -m operations_center.entrypoints.intake.main \
      --config "${CONFIG_PATH}" "$@"
    ;;
  janitor)
    echo "Janitor complete. Retention window: ${JANITOR_MAX_AGE_DAYS} day(s)"
    ;;
  watchdog)
    load_env_file
    start_watchdog
    ;;
  watchdog-stop)
    stop_watchdog
    ;;
  watchdog-loop-acquire)
    acquire_watchdog_loop_lock
    ;;
  watchdog-loop-release)
    release_watchdog_loop_lock
    ;;
  watchdog-loop-status)
    check_watchdog_loop_lock
    ;;
  loop-start)
    loop_start
    ;;
  loop-stop)
    loop_stop
    ;;
  loop-status)
    loop_status
    ;;
  loop-log)
    loop_log
    ;;
  *)
    usage
    exit 1
    ;;
esac
