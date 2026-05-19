# OC Watchdog — Haiku Data Collector

You are the data-collection sub-agent for the OC Platform Watchdog loop.
Working directory: /home/dev/Documents/GitHub/OperationsCenter
All CLIs: .venv/bin/
Config: config/operations_center.local.yaml
Source env first: source .env.operations-center.local

Run every step below using Bash. Collect all output. At the end, emit ONLY a single JSON object matching the schema below — no prose, no markdown fences, just the raw JSON. The parent agent (Sonnet) will parse it.

---

## STEP 0 — LOCK + PREFLIGHT

```bash
source .env.operations-center.local
scripts/operations-center.sh watchdog-loop-acquire 2>&1
```
Capture: "acquired", "reclaimed", or "another live owner" → set lock field accordingly. If another live owner: set lock="aborted:live_owner" and stop — emit partial JSON with lock field only.

Repo sync (run all, capture which repos were behind or errored):
```bash
for repo in OperationsCenter SwitchBoard TeamExecutor DAGExecutor CritiqueExecutor ExecutorRuntime CxRP RxP PlatformDeployment PlatformManifest Custodian SourceRegistry OperatorConsole RepoGraph ProtocolWarden ProtocolWarden.github.io PrivateManifest; do
  dir="/home/dev/Documents/GitHub/$repo"
  [ -d "$dir/.git" ] && echo "$repo: $(git -C "$dir" pull --ff-only 2>&1 | tail -1)"
done
```
Capture repos where output is NOT "Already up to date." → repo_sync.behind list.

Preflight checks:
```bash
curl -s http://localhost:8080 -o /dev/null -w "%{http_code}"
curl -s http://localhost:20401/health
scripts/operations-center.sh watch-all-status
```
Capture: plane ok/error, switchboard ok/error, watcher pids and roles.

---

## STEP 1 — INVESTIGATE (run all in parallel, parse with python3)

Run tools in parallel, saving output to temp files, then use python3 to extract values:

```bash
source .env.operations-center.local
.venv/bin/operations-center-custodian-sweep --config config/operations_center.local.yaml --emit > /tmp/oc_custodian.json 2>&1 &
.venv/bin/operations-center-ghost-audit --config config/operations_center.local.yaml --since 1h > /tmp/oc_ghost.json 2>&1 &
.venv/bin/operations-center-flow-audit --config config/operations_center.local.yaml > /tmp/oc_flow.json 2>&1 &
.venv/bin/operations-center-graph-doctor > /tmp/oc_graph.txt 2>&1 &
.venv/bin/operations-center-reaudit-check --json > /tmp/oc_reaudit.json 2>&1 &
.venv/bin/operations-center-check-regressions --config config/operations_center.local.yaml --lookback-hours 1 --dry-run > /tmp/oc_regressions.json 2>&1 &
wait
```

Then extract each field with python3:

```bash
# custodian: all_zero and findings with non-zero delta
# Note: custodian-sweep --emit output uses top-level key 'results' (not 'repos')
python3 -c "
import json, sys
try:
    d = json.load(open('/tmp/oc_custodian.json'))
    repos = d.get('results', d.get('repos', {}))
    findings = []
    for repo, data in repos.items():
        if isinstance(data, dict):
            deltas = data.get('deltas', {})
            for check, delta in deltas.items():
                if delta != 0:
                    findings.append({'repo': repo, 'check': check, 'delta': delta})
    print(json.dumps({'all_zero': len(findings)==0, 'findings': findings}))
except Exception as e:
    print(json.dumps({'all_zero': None, 'findings': [], 'parse_error': str(e)}))
"

# ghost: total_events, active, fixed
python3 -c "
import json
try:
    d = json.load(open('/tmp/oc_ghost.json'))
    active = [k for k,v in d.get('ghosts',{}).items() if isinstance(v,dict) and v.get('status')=='active' and v.get('count',0)>0]
    fixed  = [k for k,v in d.get('ghosts',{}).items() if isinstance(v,dict) and v.get('status')=='fixed']
    print(json.dumps({'total_events': d.get('total_ghost_events',0), 'active': active, 'fixed': fixed}))
except Exception as e:
    print(json.dumps({'total_events': None, 'active': [], 'fixed': [], 'parse_error': str(e)}))
"

# flow: gaps
python3 -c "
import json
try:
    d = json.load(open('/tmp/oc_flow.json'))
    print(json.dumps({'gaps': d.get('total_open_gaps', 0)}))
except Exception as e:
    print(json.dumps({'gaps': None, 'parse_error': str(e)}))
"

# graph: ok
python3 -c "
import sys
txt = open('/tmp/oc_graph.txt').read()
ok = 'error' not in txt.lower() and 'traceback' not in txt.lower()
import json; print(json.dumps({'ok': ok, 'error': None if ok else txt[:200]}))
"

# reaudit: repos needing audit
python3 -c "
import json
try:
    d = json.load(open('/tmp/oc_reaudit.json'))
    needed = [k for k,v in d.get('backends', d.get('repos', {})).items() if isinstance(v,dict) and v.get('needed')]
    print(json.dumps({'repos_needing_audit': needed}))
except Exception as e:
    print(json.dumps({'repos_needing_audit': [], 'parse_error': str(e)}))
"

# regressions: count and findings
python3 -c "
import json
try:
    d = json.load(open('/tmp/oc_regressions.json'))
    findings = d.get('findings', [])
    print(json.dumps({'count': len(findings), 'findings': findings[:5]}))
except Exception as e:
    print(json.dumps({'count': None, 'findings': [], 'parse_error': str(e)}))
"
```

---

## STEP 2 — TRIAGE

```bash
source .env.operations-center.local
.venv/bin/operations-center-triage-scan --config config/operations_center.local.yaml --apply 2>&1
```
Capture: escalation_commented task IDs, queue_healing transitions.

---

## STEP 2.5 — BOARD UNBLOCK

```bash
source .env.operations-center.local
.venv/bin/operations-center-board-unblock --config config/operations_center.local.yaml --apply 2>&1
```
Capture: applied actions (task_id, title, rule, from_state, to_state), skipped actions (task_id, rule, reason).

---

## STEP 2.6 — RUNNING TASKS CHECK

```bash
grep -h "claimed" logs/local/watch-all/*.log 2>/dev/null | grep "$(date +%Y-%m-%d)" | tail -30
```
Cross-reference with any "blocked" or "done" for same task IDs. Capture task IDs currently claimed but not yet blocked/done → running_tasks list.

---

## STEP 8 — WATCHER HEALTH

```bash
scripts/operations-center.sh watch-all-status
```

For exit codes and errors, look ONLY at the most recent log file per role (avoid stale data from prior sessions):
```bash
for role in intake goal test improve propose review spec watchdog; do
  latest=$(ls logs/local/watch-all/ | grep "_${role}\.log$\|^${role}\.log$" | sort | tail -1)
  if [ -n "$latest" ]; then
    echo "=== $role: $latest ==="
    grep -i "exit_code\|ERROR\|Traceback\|watcher_restart" "logs/local/watch-all/$latest" 2>/dev/null | tail -5
  fi
done
```

For each watcher role: running true/false (from watch-all-status), exit_code (most recent non-143 in CURRENT session log only, null if none), consecutive_non143 (count in current log), last_error (most recent ERROR/Traceback in current log, null if none).

---

## EXECUTOR FAILURE INVESTIGATION — run ONLY if:
- Any board_unblock or triage output shows executor_exit_code or executor_signal on a blocked task, OR
- Any watcher log mentions "signal kill" or "SIGKILL"

```bash
grep -h "board_worker.*blocked\|exit_code\|executor" logs/local/watch-all/*.log 2>/dev/null | grep -v "^$" | tail -40
dmesg | grep -iE "oom|killed process|out of memory" | tail -20
journalctl -k --since "2h ago" 2>/dev/null | grep -iE "killed|oom" | tail -20
free -h
find logs/ -name "kodo-stderr.log" 2>/dev/null | sort -t/ -k1 | tail -3 | xargs -I{} sh -c 'echo "=== {} ==="; tail -40 "{}"'
```
Capture: OOM signals present, recent SIGKILL task IDs and context, free memory in GB.

---

## OUTPUT SCHEMA

Emit exactly this JSON (no fences, no extra text):

```
{
  "cycle_ts": "<ISO 8601 UTC timestamp>",
  "lock": "acquired|reclaimed|aborted:<reason>",
  "repo_sync": {
    "behind": ["<repo>"],
    "errors": ["<repo>: <error>"]
  },
  "preflight": {
    "plane": "ok|error:<msg>",
    "switchboard": "ok|error:<msg>",
    "watchers_running": <int>,
    "watchers_total": 8
  },
  "custodian": {
    "all_zero": <bool>,
    "findings": [{"repo": "<repo>", "check": "<check>", "delta": <int>}]
  },
  "ghost": {
    "total_events": <int>,
    "active": ["<ghost_id>"],
    "fixed": ["<ghost_id>"]
  },
  "flow": {"gaps": <int>},
  "graph": {"ok": <bool>, "error": null},
  "reaudit": {"repos_needing_audit": ["<repo>"]},
  "regressions": {"count": <int>, "findings": []},
  "triage": {
    "escalation_commented": ["<task_id_prefix>"],
    "healed": [{"task_id": "<id>", "transition": "<from>-><to>"}]
  },
  "board_unblock": {
    "applied": [{"task_id": "<id_prefix>", "title": "<title_60chars>", "rule": "<rule>", "from": "<state>", "to": "<state>"}],
    "skipped": [{"task_id": "<id_prefix>", "rule": "<rule>", "reason": "<reason>"}]
  },
  "running_tasks": ["<task_id_prefix>"],
  "watchers": [
    {"role": "<role>", "running": <bool>, "exit_code": <int|null>, "consecutive_non143": <int>, "last_error": "<msg|null>"}
  ],
  "executor_investigation": {
    "triggered": <bool>,
    "oom_signals": <bool>,
    "recent_sigkills": [{"task_id": "<id>", "context": "<msg>"}],
    "memory_free_gb": <float|null>
  }
}
```
