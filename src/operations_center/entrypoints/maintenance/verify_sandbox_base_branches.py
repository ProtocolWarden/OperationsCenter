# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Sandbox base-branch preflight — verify each repo's sandbox_base_branch exists.

A repo with a configured ``sandbox_base_branch`` targets autonomy PRs at that
branch instead of the default branch. If the branch does not exist on origin,
``WorkspaceManager.prepare`` self-heals per task — but that discovery happens
*deep in execution, once per task*, so a whole queue of tasks can stall serially
on the same missing branch before anyone notices.

This preflight surfaces (and optionally heals) the problem *once, up front* — run
from the watchdog loop so the base branch is guaranteed before any task is
claimed. Read-only by default; ``--heal`` creates a missing branch from the
remote default (reusing the same GitClient helpers the per-task self-heal uses).

Run (read-only — report + nonzero exit if any sandbox branch is missing):
  python -m operations_center.entrypoints.maintenance.verify_sandbox_base_branches \\
      --config config/operations_center.local.yaml

Run and heal (create any missing sandbox branch from the remote default):
  python -m operations_center.entrypoints.maintenance.verify_sandbox_base_branches \\
      --config config/operations_center.local.yaml --heal

Exit code: 0 when every configured sandbox branch exists (or was healed); 1 when
any remains missing (so a watchdog wrapper can alarm).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from operations_center.adapters.git.client import GitClient
from operations_center.config import load_settings

logger = logging.getLogger(__name__)


@dataclass
class SandboxBranchResult:
    """Per-repo outcome of the sandbox base-branch preflight."""

    repo_key: str
    sandbox_base_branch: str | None
    exists: bool = False
    healed: bool = False
    skipped: bool = False
    error: str | None = None

    @property
    def missing(self) -> bool:
        """A configured branch that is absent on origin and was not healed."""
        return (
            not self.skipped
            and self.sandbox_base_branch is not None
            and not self.exists
            and self.error is None
        )


def _fetch_origin(local_path: Path) -> None:
    """Best-effort ``git fetch`` so origin/<default> is resolvable for a heal."""
    GitClient()._run(["git", "fetch", "origin", "--quiet"], cwd=local_path, timeout=120)


def _check_repo(
    repo_key: str,
    local_path: Path,
    sandbox_base_branch: str | None,
    default_branch: str,
    *,
    heal: bool,
    git: GitClient,
    fetch: Any,
) -> SandboxBranchResult:
    """Check if a repo's configured sandbox base branch exists on origin.

    If heal=True and the branch is missing, attempts to create it from the
    remote default branch. Returns a SandboxBranchResult with status details.
    """
    result = SandboxBranchResult(repo_key=repo_key, sandbox_base_branch=sandbox_base_branch)

    # No sandbox branch configured → nothing to verify (the repo uses its default).
    if not sandbox_base_branch:
        result.skipped = True
        return result
    if not (local_path / ".git").exists():
        result.skipped = True
        result.error = "no local checkout"
        return result

    try:
        git.verify_remote_branch_exists(local_path, sandbox_base_branch)
        result.exists = True
        return result
    except ValueError:
        result.exists = False  # branch genuinely absent on origin
    except Exception as exc:  # noqa: BLE001 — ls-remote network/transport failure
        result.error = f"ls-remote failed: {exc}"
        return result

    if not heal:
        return result

    try:
        fetch(local_path)
        git.create_remote_branch_from(local_path, sandbox_base_branch, f"origin/{default_branch}")
        result.exists = True
        result.healed = True
    except Exception as exc:  # noqa: BLE001 — push/fetch failure during heal
        result.error = f"heal failed: {exc}"
    return result


def scan(
    settings: Any,
    *,
    heal: bool = False,
    git: GitClient | None = None,
    fetch: Any = None,
) -> list[SandboxBranchResult]:
    """Check every configured repo's sandbox base branch; optionally heal missing."""
    git = git or GitClient()
    fetch = fetch or _fetch_origin
    results: list[SandboxBranchResult] = []
    for repo_key, repo_cfg in settings.repos.items():
        local_path_str = repo_cfg.local_path
        if not local_path_str:
            # No local checkout configured — can't verify; skip silently (the
            # repo isn't serviced from this host).
            continue
        results.append(
            _check_repo(
                repo_key=repo_key,
                local_path=Path(local_path_str),
                sandbox_base_branch=repo_cfg.sandbox_base_branch,
                default_branch=repo_cfg.default_branch,
                heal=heal,
                git=git,
                fetch=fetch,
            )
        )
    return results


def main(argv: list[str] | None = None) -> int:
    """Check and optionally heal missing sandbox base branches in configured repos.

    Exit 0 when all configured branches exist (or are healed); exit 1 if any
    remain missing. Supports JSON output for integration with watchdog loops.
    """
    parser = argparse.ArgumentParser(description="Sandbox base-branch preflight")
    parser.add_argument("--config", required=True, help="Path to operations_center.local.yaml")
    parser.add_argument(
        "--heal",
        action="store_true",
        help="Create any missing sandbox branch from the remote default branch",
    )
    parser.add_argument("--json", dest="output_json", action="store_true", help="Emit JSON output")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    settings = load_settings(args.config)
    results = scan(settings, heal=args.heal)

    missing = [r for r in results if r.missing]
    healed = [r for r in results if r.healed]
    errors = [r for r in results if r.error]

    if args.output_json:
        print(
            json.dumps(
                {
                    "scanned_at": datetime.now(UTC).isoformat(),
                    "healed": args.heal,
                    "results": [
                        {
                            "repo_key": r.repo_key,
                            "sandbox_base_branch": r.sandbox_base_branch,
                            "exists": r.exists,
                            "healed": r.healed,
                            "skipped": r.skipped,
                            "error": r.error,
                        }
                        for r in results
                    ],
                    "missing_count": len(missing),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        checked = [r for r in results if not r.skipped]
        if healed:
            print(f"HEALED ({len(healed)}):")
            for r in healed:
                print(f"  {r.repo_key}: created {r.sandbox_base_branch} from default")
        if missing:
            print(f"MISSING sandbox base branches ({len(missing)}):")
            for r in missing:
                print(f"  {r.repo_key}: {r.sandbox_base_branch} not on origin")
        if errors:
            print(f"ERRORS ({len(errors)}):")
            for r in errors:
                print(f"  {r.repo_key}: {r.error}")
        if not missing and not errors:
            print(f"OK — {len(checked)} configured sandbox branch(es) present.")

    # Nonzero only when a configured branch is still missing (gate the watchdog).
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
