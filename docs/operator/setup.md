# Setup Guide

`./scripts/operations-center.sh setup` is the interactive local operator setup flow.

It prepares:

- local Forgejo API config (board + PRs + CI)
- local repo config
- provider readiness
- executor (TeamExecutor) install/verification
- repo target defaults

## Files Written

Setup writes (all gitignored):

- `config/operations_center.local.yaml`
- `.env.operations-center.local`
- `config/managed_repos/local/*.yaml` — per-repo managed repo entries

## Backup and Restore (SS)

Local configs are backed up to `~/sync/platform/config/` via Syncthing.

**Backup** (run after any config change):

```bash
scripts/backup-secrets.sh
```

**Restore on a fresh clone or new machine:**

```bash
scripts/setup-secrets.sh
```

This symlinks flat files and copies the `managed_repos/local/` tree to the correct
target paths. Do not manually copy files — the paths are non-obvious and the
restore script gets them right.

## Typical Flow (fresh machine)

```bash
# Option A — restore from SS backup (preferred if backup exists)
scripts/setup-secrets.sh
source .env.operations-center.local

# Option B — interactive setup from scratch
./scripts/operations-center.sh setup
source .env.operations-center.local
```

## What Setup Covers

### Forgejo

The forge OC runs against: it hosts the board (repo issues), the pull requests,
and the CI that produces the `audit` status branch protection requires.

- base URL (e.g. `http://localhost:3000`)
- owner and board repo
- API token, via `FORGEJO_API_TOKEN` (see `.env.operations-center.example` —
  it must be a literal value, not command substitution)
- optional live API verification

Standing the instance itself up on a new machine — containers, runner
registration, the CI job image, and branch protection — is
`deploy/forgejo/README.md`, not this guide.

Branch protection lives in the forge's own database, not in this repo, so
cloning gets you none of it. Which of the two procedures you are running decides
what that means:

* **Restoring a volume backup** (moving an instance): protection comes back with
  the database, along with the repos, PRs and API tokens. Verify with
  `deploy/forgejo/apply-branch-protection.sh --check`; only apply if that reports
  drift.
* **A fresh instance**: nothing is there and it starts **unprotected**, which is
  the failure mode that looks completely fine. Apply it with
  `deploy/forgejo/apply-branch-protection.sh`, then `--check`.

**Plane was retired at the 2026-08-19 cutover** and never ran on this fleet.
`board_backend` accepts only `forgejo`.

### Git

- provider
- optional HTTPS token
- bot author identity
- GitHub SSH bootstrap/verification

### Executor (TeamExecutor)

TeamExecutor is the multi-agent coding engine OperationsCenter uses for task execution.
OperationsCenter consumes it as a Python library (`import team_executor`) — see
`src/operations_center/backends/team_executor/` for the adapter implementation.

- verify the execute backends are importable, installing missing sibling checkouts editable
- configure orchestrator defaults
- persist local execution settings

### Providers

- detect Claude Code, Codex CLI, Gemini CLI, Cursor Agent
- install supported missing CLIs when possible
- verify login/auth readiness
- record preferred smart/fast provider choices

### Repo Targets

- clone URL
- default/base branch
- validation commands
- repo-local `.venv` bootstrap behavior

## Repo Bootstrap Convention

Before the executor runs on a task, OperationsCenter bootstraps the repo's Python environment.

**Default (Python repos):** set `bootstrap_enabled: true` in the repo config.
OperationsCenter creates a venv at `venv_dir` and runs `install_dev_command`.

**Custom bootstrap:** set `bootstrap_enabled: false` and place a `tools/bootstrap.sh`
in the repo root.  OperationsCenter auto-discovers and runs it — no `bootstrap_commands`
config needed.  The script can set up any environment the repo requires; it runs
with the repo root as the working directory.

`bootstrap_commands` in the repo config can still override this for one-off cases,
but the preferred pattern for repos with their own setup process is `tools/bootstrap.sh`.

Validation commands run after the executor using full paths (e.g. `.codebase-venv/bin/python -m pytest -q`)
so they work regardless of which venv was activated during bootstrap.

## Executor Install Behavior

OperationsCenter loads its execute backends as **libraries**, not CLIs — the adapters in
`src/operations_center/backends/<name>/` do a plain `import team_executor` / `import dag_executor` /
`import critique_executor`. So readiness means "importable in the OC venv", not "on `PATH`".
None of the three ships a console script OC invokes.

The backends are sibling *checkouts*, not declared OC dependencies: `uv pip install -e .[dev]`
never installs them, and a `uv sync` or venv recreate actively drops them.

Setup:

- probes each backend with `<oc-venv-python> -c "import <module>"`
- installs `uv` if needed, and only if a backend is actually missing
- installs the missing backend editable from its sibling checkout
  (`../TeamExecutor`, `../DAGExecutor`, `../CritiqueExecutor`)
- fails with the expected checkout path if a sibling is not cloned next to this repo
- re-probes after installing and fails if a backend is still not importable

Setup is idempotent: the import probe is cheap and the install only fires for backends that
are actually missing. `scripts/operations-center.sh` runs the same self-heal
(`ensure_executor_backends`) at every fleet launch, so a mid-life drop recovers on the next start.

## Advanced Mode

Advanced mode also exposes optional version pins for:

- TeamExecutor (`OPERATIONS_CENTER_EXECUTOR_INSTALL_REF`)
- supported provider CLIs

Pins record the version this machine is expected to run. They do not automatically trigger update
checks during normal runs, and the TeamExecutor pin does not drive an install — the backend comes
from the sibling checkout. `dependency-check` compares each pin against what is installed and
against the upstream latest release, and reports the drift.

## Per-Repo Reviewer Settings

### `ci_ignored_checks`

Some repos have CI checks that were failing before the PR was opened (pre-existing failures). Listing check name substrings in `ci_ignored_checks` tells the reviewer watcher to treat those checks as non-blocking:

```yaml
repos:
  my_repo:
    await_review: true
    ci_ignored_checks:
      - "file-tag-linter"     # pre-existing linter failure unrelated to PR changes
      - "legacy-integration"  # broken upstream check we don't own
```

When every failing check matches an entry in this list, the PR is auto-merged (with `allow_unstable=True`). This prevents orphaned PRs from being blocked indefinitely by broken CI that predates the PR. The merge is logged as `reason: ci_ignored_checks_all_clear`.

Substrings are matched case-sensitively against the check run name. On Forgejo a
context is `<workflow name> / <job name> (<event>)`, e.g.
`CI / Lint (ruff) (pull_request)`. Use the most specific prefix or suffix that uniquely identifies the check to avoid unintentional matches.

## Notes

- The setup wizard is for local operator use, not production secret management.
- The local environment is still single-machine and polling-based after setup completes.
- Re-run readiness checks later with `providers-status` or `dependency-check`.
