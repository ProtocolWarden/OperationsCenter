---
adr: "0010"
title: "Work Order — Architecture Hardening"
status: proposed
date: "2026-05-30"
---

# ADR 0010 — Work Order: Architecture Hardening

## Context

A full architecture audit of OperationsCenter (2026-05-30) identified three
structural issues that require operator design decisions before autonomous
execution can proceed. This ADR documents the decisions and defines the work
order for each.

Autonomous tasks (Plane #165–168) cover the logging and config validation
improvements that don't require design input. This ADR covers the three items
that do.

---

## Issue 1 — board_worker Monolith

### Problem

`entrypoints/board_worker/main.py` is 2,186 lines with 67 inter-module imports.
Critical functions:

- `_process_issue()`: 361 lines, 20 nested branches
- `_claim_next()`: 181 lines — quota, filtering, priority in one function
- `_handle_spec_author_success()`: 155 lines

The file is C29-exempt in Custodian (intentionally) but the exemption is
masking genuine complexity that makes testing and iteration expensive. Every
new capability (spec-author, campaign-mode, OPEN_PR_GATE) gets bolted in here.

### Decision

**Phase out `_claim_next()` and `_process_issue()` as top-level functions;
replace with a `BoardWorkerSession` class.**

Decomposition plan:

```
board_worker/
  main.py              ← entry point only (~150 lines: arg parsing, signal handling, loop)
  claim_engine.py      ← _claim_next() → BoardClaimEngine.claim()
  task_dispatch.py     ← _process_issue() → TaskDispatcher.dispatch(issue)
  spec_author_worker.py ← spec-author path extracted from dispatch
```

Constraints:

- Keep the outer poll loop in `main.py` — supervisor compatibility requires a
  single entry point with `--watch` semantics.
- `BoardClaimEngine` must remain stateless per call — Plane state is the
  source of truth, not in-memory.
- Do NOT change the claim/release protocol (transition to Running → cleanup
  on failure). This is the concurrency contract with Plane.
- Each extracted class gets its own test file in `tests/unit/board_worker/`.

### Definition of Done

- [ ] `_claim_next()` extracted to `BoardClaimEngine`; `main.py` calls it
- [ ] `_process_issue()` extracted to `TaskDispatcher`; spec-author path in own class
- [ ] `main.py` ≤ 300 lines
- [ ] No function in any extracted file exceeds 100 lines
- [ ] Unit tests for `BoardClaimEngine.claim()` covering quota, state filtering, priority
- [ ] All existing integration tests pass unchanged

---

## Issue 2 — State File Race Conditions

### Problem

Multiple processes write to shared JSON state files without coordination:

| File | Writers | Risk |
|------|---------|------|
| `state/proposal_feedback/*.json` | usage_store, insights derivers, feedback entrypoint | HIGH |
| `state/proposal_rejections.json` | quality_alerts, decision layer | HIGH |
| `state/ci_lineage.json` | ci_evaluator, ci_coordinator | MED |
| `state/campaigns/active.json` | spec_author/state.py, spec_hygiene | MED |

Current pattern: `path.write_text(json.dumps(data))` — not atomic, no lock.
Concurrent writes produce partial JSON, breaking the next reader silently.

### Decision

**Adopt an atomic write + advisory lock pattern via a shared `state_io` module.**

```python
# src/operations_center/state_io.py
import fcntl, json, os
from pathlib import Path

def read_state(path: Path, default=None):
    try:
        with path.open() as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}

def write_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    lock = path.with_suffix(".lock")
    with lock.open("w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp, path)   # atomic on POSIX
```

Migration:

1. Add `state_io.py` with `read_state()` / `write_state()`.
2. Replace all `path.write_text(json.dumps(...))` calls on state files with
   `write_state(path, data)`.
3. Replace all `json.loads(path.read_text())` on the same files with
   `read_state(path)`.
4. Files in scope: `usage_store.py`, `insights/derivers/proposal_outcome.py`,
   `feedback/main.py`, `quality_alerts.py`, `decision/`, `ci_evaluator.py`,
   `ci_coordinator.py`, `spec_author/state.py`, `spec_hygiene/main.py`.

Constraints:

- Do NOT migrate `state/pr_reviews/*.json` — already single-writer per key,
  safe as-is.
- Do NOT migrate `state/audit_dispatch/locks/` — uses its own locking scheme.
- `.lock` files are ephemeral; add `state/**/*.lock` and `state/**/*.tmp` to
  `.gitignore`.

### Definition of Done

- [ ] `state_io.py` implemented with `read_state()` / `write_state()`
- [ ] All multi-writer state files migrated to `state_io`
- [ ] `state/**/*.lock` and `state/**/*.tmp` added to `.gitignore`
- [ ] Unit tests for `state_io`: concurrent write test, partial-write recovery

---

## Issue 3 — subprocess shell=True Security

### Problem

`application/validation.py:24` runs validation commands with `shell=True`
using caller-provided command strings:

```python
proc = subprocess.run(command, cwd=cwd, shell=True, ...)
```

`command` comes from `validation_commands` in `RepoSettings` (repo YAML config).
The config is operator-controlled (not user-controlled at runtime), so the
immediate injection risk is low. However:

- A compromised or malicious repo config could inject arbitrary shell commands.
- `ci_evaluator.py` substitutes `{n}` into an `evaluation_command` string
  before passing to `shell=True` — the substitution pattern is safe today
  but fragile.

### Decision

**Document and enforce the trusted-operator boundary; add validation for
high-risk patterns.**

Three-tier approach:

**Tier 1 (immediate)** — Document the security contract in code:

```python
# application/validation.py
# SECURITY: commands are shell strings from operator-controlled YAML config.
# They are NOT sanitized against injection — the operator is trusted.
# Do not expose this code path to untrusted input.
```

**Tier 2 (near-term)** — Add a config-level pattern block for obviously
dangerous constructs in `validation_commands`:

```python
_BLOCKED_PATTERNS = [
    r"\$\(", r"`",        # subshell
    r"&&\s*rm\b",         # destructive chain
    r"\|\s*bash\b",       # piped execution
]

def _validate_command(cmd: str) -> None:
    for pat in _BLOCKED_PATTERNS:
        if re.search(pat, cmd):
            raise ConfigurationError(f"Blocked shell pattern in validation_command: {cmd!r}")
```

Call this in `load_settings()` during config validation.

**Tier 3 (long-term, optional)** — For repos with `repo_untrusted: true` flag in
config, use `shell=False` with `shlex.split()` instead of `shell=True`.
This constrains high-risk repos to simple commands without shell features.

### Definition of Done

- [ ] Security contract comment added to `application/validation.py` and
  `execution/ci_evaluator.py`
- [ ] `_validate_command()` with blocked pattern list added to settings validation
- [ ] `load_settings()` calls validator on all `validation_commands`
- [ ] Unit tests for blocked pattern detection
- [ ] `repo_untrusted` flag added to `RepoSettings` in `config/settings.py` (optional, default False)

---

## Priority Order

| Priority | Item | Effort | Autonomous? |
|----------|------|--------|-------------|
| P1 | State file locking (Issue 2) | 2–3 days | Yes — well-scoped, no design ambiguity |
| P2 | subprocess security Tier 1+2 (Issue 3) | 1 day | Yes — additive only |
| P3 | board_worker refactor (Issue 1) | 1–2 weeks | Partial — extraction is mechanical, but test coverage must be verified by operator |

## Related

- Plane tasks: #165 (silent exception logging), #166 (broad exception logging),
  #167 (config validation), #168 (adapter/decision test coverage)
- ADR 0009: Execution Hygiene (completed)
