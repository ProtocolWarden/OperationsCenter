# Setup Guide

`./scripts/operations-center.sh setup` is the interactive local operator setup flow.

It prepares:

- local Plane API config
- local repo config
- provider readiness
- Kodo install/verification
- repo target defaults

## Files Written

Setup writes (all gitignored):

- `config/operations_center.local.yaml`
- `.env.operations-center.local`
- `config/plane_task_template.local.md`
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

### Plane

- base URL
- workspace identifier
- project id
- API token
- optional live API verification

### Git

- provider
- optional HTTPS token
- bot author identity
- GitHub SSH bootstrap/verification

### Kodo

Kodo is the multi-agent coding engine OperationsCenter uses for task execution.
This installation uses the **ProtocolWarden fork** (`github.com/ProtocolWarden/kodo`,
`dev` branch) rather than the upstream (`github.com/ikamensh/kodo`).

The fork contains fixes that are not yet in upstream releases:
- `--full-auto → -a never` for the Codex orchestrator (codex dropped `--full-auto`)

Install command (must use fork, not upstream):
```bash
uv tool install git+https://github.com/ProtocolWarden/kodo.git@dev --force
```

Do **not** run `uv tool install kodo` (installs upstream from PyPI).  The setup script
must be updated to use the fork URL when kodo is missing or needs to be reinstalled.

- install/verify `kodo` (from ProtocolWarden fork, `dev` branch)
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

Before kodo runs on a task, OperationsCenter bootstraps the repo's Python environment.

**Default (Python repos):** set `bootstrap_enabled: true` in the repo config.
OperationsCenter creates a venv at `venv_dir` and runs `install_dev_command`.

**Custom bootstrap:** set `bootstrap_enabled: false` and place a `tools/bootstrap.sh`
in the repo root.  OperationsCenter auto-discovers and runs it — no `bootstrap_commands`
config needed.  The script can set up any environment the repo requires; it runs
with the repo root as the working directory.

`bootstrap_commands` in the repo config can still override this for one-off cases,
but the preferred pattern for repos with their own setup process is `tools/bootstrap.sh`.

Validation commands run after kodo using full paths (e.g. `.codebase-venv/bin/python -m pytest -q`)
so they work regardless of which venv was activated during bootstrap.

## Kodo Install Behavior

Setup:

- checks whether `kodo` is on `PATH`
- installs `uv` if needed
- installs Kodo from the **ProtocolWarden fork** (`dev` branch) if missing:
  `uv tool install git+https://github.com/ProtocolWarden/kodo.git@dev --force`
- verifies the install with `kodo --help`

Setup is intended to be idempotent: it does not reinstall Kodo when the current install already works.

> **Important:** The setup script's kodo install logic must use the fork URL.
> If the script currently installs from PyPI or upstream GitHub, update it.
> See `scripts/kodo-shim` for the runtime shim that wraps the installed kodo binary.

## Advanced Mode

Advanced mode also exposes optional version pins for:

- Plane
- Kodo
- supported provider CLIs

Pins are for reproducible local installs. They do not automatically trigger update checks during normal runs.

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

Substrings are matched case-sensitively against the GitHub check run name. Use the most specific prefix or suffix that uniquely identifies the check to avoid unintentional matches.

## Notes

- The setup wizard is for local operator use, not production secret management.
- The local environment is still single-machine and polling-based after setup completes.
- Re-run readiness checks later with `providers-status`, `plane-doctor`, or `dependency-check`.
