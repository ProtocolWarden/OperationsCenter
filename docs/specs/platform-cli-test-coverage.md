---
campaign_id: 1f19ba58-6aab-4e9f-ab1a-c1126466f54f
slug: platform-cli-test-coverage
phases:
  - implement
  - test
  - improve
repos:
  - PlatformDeployment
area_keywords:
  - tools/platform_deployment_cli
  - test/unit
status: active
created_at: 2026-05-19T00:00:00Z
---

## Overview

The PlatformDeployment CLI (`tools/platform_deployment_cli/`) recently unified all operational scripts into a single argparse-based tool with 6 subcommand groups (stack, lane, plane, secrets, workers, workspace). Configuration parsing and data models are well-tested (6 modules, ~1,700 lines of tests), but every CLI command handler and the supporting health/services layer — 1,241 lines across 8 modules — has zero test coverage. This campaign adds unit tests for the CLI dispatch layer and the health/services utilities, using subprocess mocks and fixture-driven patterns consistent with the existing test suite.

## Goals

1. **Add unit tests for `health.py` and `services.py`** — Test health endpoint probing with mocked HTTP responses (healthy 200, unhealthy 5xx, connection timeout, unreachable host). Test service discovery helpers with fixture config files. Cover `--json` output formatting where applicable. Target: `test/unit/test_health.py` and `test/unit/test_services.py` (~15 test methods total).

2. **Add unit tests for `plane_cli.py`** — Test all 6 plane subcommands (`up`, `down`, `status`, `backup`, `restore`, `list`) by mocking `subprocess.run` and filesystem interactions. Verify: backup creates the expected pg_dump invocation, restore prompts for confirmation (and `-y` skips it), `list` scans the correct directory, `status` returns structured output. Target: `test/unit/test_plane_cli.py` (~12 test methods).

3. **Add unit tests for `secrets_cli.py` and `workspace_cli.py`** — For secrets: mock `shutil.copy2` and filesystem state to test `backup`, `setup`, and `list` subcommands, including missing-source-file error paths. For workspace: mock `subprocess.run` to test `clone-all` with fresh clone vs `--pull` on existing repos, and verify it reads from PlatformManifest. Target: `test/unit/test_secrets_cli.py` and `test/unit/test_workspace_cli.py` (~10 test methods total).

4. **Add unit tests for `main.py` argument parsing and dispatch** — Test that the argparse parser correctly routes each top-level command and subcommand to the expected handler function. Use `unittest.mock.patch` on handler functions and invoke `main()` with synthetic `sys.argv` lists. Cover: missing required arguments exit with code 2, `--json` flag propagates to subcommands, unknown subcommands produce usage errors. Target: `test/unit/test_main_dispatch.py` (~10 test methods).

## Constraints

- **Test-only campaign** — no production code changes unless a bug blocks test creation (document any fix as a separate commit with rationale).
- **Follow existing patterns** — match the style in `test/unit/test_lane_config.py` and `test/unit/test_lane_manager.py` for fixture layout, assertion style, and `tmp_path` usage.
- **Mock external I/O only** — subprocess calls (`docker compose`, `pg_dump`, `git clone`), HTTP requests, and filesystem side effects must be mocked. Never invoke real Docker or database commands from unit tests.
- **No overlap with smoke tests** — `test/smoke/test_stack_health.py` covers live endpoint probing; these unit tests must remain fully offline and not duplicate that coverage.
- **Respect Custodian policy** — all new test files must pass `ruff check` with the project's `pyproject.toml` rules. Use `encoding="utf-8"` on any `Path.read_text()` calls per existing C16 convention.

## Success Criteria

- Every public function in `health.py`, `services.py`, `plane_cli.py`, `secrets_cli.py`, `workspace_cli.py`, and `main.py` has at least one happy-path and one error-path unit test.
- Total new test methods: ≥45 across the 6 new test files.
- All new tests pass via `pytest test/unit/` with zero failures and no warnings about unclosed resources.
- Untested line count in `tools/platform_deployment_cli/` drops from ~1,241 to under 300 (entry points and trivial glue excluded).
- `workers_cli.py` (54 lines, thin wrapper) is explicitly deferred — it may be covered in a follow-up campaign if the workers subsystem stabilizes.
