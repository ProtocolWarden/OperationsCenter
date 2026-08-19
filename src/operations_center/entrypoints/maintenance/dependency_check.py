# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from operations_center.adapters.board import BoardClient, make_board_client
from operations_center.adapters.reporting import Reporter
from operations_center.config import Settings, load_settings
from operations_center.entrypoints.setup.main import load_env_exports
from operations_center.entrypoints.setup.providers import (
    PROVIDER_SPECS,
    detect_all_provider_statuses,
)

GITHUB_ACCEPT = "application/vnd.github+json"


@dataclass
class DependencyStatus:
    key: str
    label: str
    kind: str
    installed_version: str | None
    pinned_version: str | None
    upstream_latest: str | None
    healthy: bool
    notes: list[str]


def normalize_version(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    match = re.search(r"\b\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?\b", stripped)
    if match:
        return match.group(0)
    if re.fullmatch(r"[A-Fa-f0-9]{7,40}", stripped):
        return stripped.lower()
    return stripped


def fetch_github_latest_release(owner: str, repo: str) -> str | None:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    response = httpx.get(url, headers={"Accept": GITHUB_ACCEPT}, timeout=20.0)
    if response.status_code != 200:
        return None
    payload = response.json()
    if isinstance(payload, dict):
        return normalize_version(str(payload.get("tag_name") or "").strip())
    return None


def fetch_npm_latest(package_name: str) -> str | None:
    url = f"https://registry.npmjs.org/{package_name}/latest"
    response = httpx.get(url, timeout=20.0)
    if response.status_code != 200:
        return None
    payload = response.json()
    if isinstance(payload, dict):
        return normalize_version(str(payload.get("version") or "").strip())
    return None


def executor_backend_status(module: str) -> tuple[bool, str | None]:
    """Return ``(importable, distribution version)`` for an execute backend module.

    OC loads TeamExecutor as a LIBRARY (``backends/team_executor/adapter.py``
    imports it directly), and TeamExecutor declares no ``[project.scripts]`` — so
    importability, not PATH, is what "installed" means here. The version is
    best-effort: an editable sibling checkout whose metadata does not map the
    top-level module back to a distribution reports importable with no version.
    """
    try:
        importable = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        importable = False
    if not importable:
        return False, None
    candidates = list(importlib.metadata.packages_distributions().get(module, ()))
    candidates.append(module.replace("_", "-"))
    for distribution in candidates:
        try:
            return True, normalize_version(importlib.metadata.version(distribution))
        except importlib.metadata.PackageNotFoundError:
            continue
    return True, None


def current_board_status(settings: Settings) -> tuple[str | None, bool]:
    """Version and reachability of the Forgejo instance serving the board.

    Replaces the Plane service row this module used to carry. `/api/v1/version`
    needs no auth and answers both questions at once, so a failure here is
    exactly what an operator wants to see in a dependency report: the board is
    the one service whose absence stops everything.
    """
    cfg = getattr(settings, "forgejo", None)
    if cfg is None:
        return None, False
    try:
        response = httpx.get(f"{cfg.base_url.rstrip('/')}/api/v1/version", timeout=10.0)
    except httpx.HTTPError:
        return None, False
    if response.status_code >= 400:
        return None, False
    payload = response.json()
    if not isinstance(payload, dict):
        return None, True
    return normalize_version(str(payload.get("version") or "").strip()), True


def collect_dependency_statuses(settings: Settings, env: dict[str, str]) -> list[DependencyStatus]:
    statuses: list[DependencyStatus] = []

    board_version, board_healthy = current_board_status(settings)
    board_notes: list[str] = []
    if not board_healthy:
        board_notes.append("Forgejo instance is not reachable — the fleet has no board.")
    statuses.append(
        DependencyStatus(
            key="forgejo",
            label="Forgejo (board)",
            kind="service",
            installed_version=board_version,
            pinned_version=None,
            # Forgejo publishes releases on Codeberg, not the GitHub releases
            # API the other rows use. Reporting None is honest; inventing a
            # second fetcher for one row is not this change.
            upstream_latest=None,
            healthy=board_healthy,
            notes=board_notes,
        )
    )

    executor_installed, executor_installed_version = executor_backend_status("team_executor")
    executor_pinned = normalize_version(env.get("OPERATIONS_CENTER_EXECUTOR_INSTALL_REF"))
    executor_latest = fetch_github_latest_release("ProtocolWarden", "TeamExecutor")
    executor_notes: list[str] = []
    if not executor_installed:
        executor_notes.append(
            "team_executor is not importable. Run `./scripts/operations-center.sh setup` or "
            "install the sibling TeamExecutor checkout editable into the OC venv."
        )
    if (
        executor_pinned
        and executor_installed_version
        and executor_pinned != executor_installed_version
    ):
        executor_notes.append(
            f"Installed version {executor_installed_version} does not match pinned ref {executor_pinned}."
        )
    if executor_pinned and executor_latest and executor_pinned != executor_latest:
        executor_notes.append(
            f"Pinned ref {executor_pinned} differs from upstream latest {executor_latest}."
        )
    statuses.append(
        DependencyStatus(
            key="team_executor",
            label="TeamExecutor",
            kind="library",
            installed_version=executor_installed_version,
            pinned_version=executor_pinned,
            upstream_latest=executor_latest,
            healthy=executor_installed,
            notes=executor_notes,
        )
    )

    provider_statuses = {status.key: status for status in detect_all_provider_statuses()}
    provider_pin_env = {
        "claude": "OPERATIONS_CENTER_PROVIDER_CLAUDE_VERSION",
        "codex": "OPERATIONS_CENTER_PROVIDER_CODEX_VERSION",
        "gemini": "OPERATIONS_CENTER_PROVIDER_GEMINI_VERSION",
    }
    for key in ["claude", "codex", "gemini"]:
        provider = provider_statuses[key]
        pinned = normalize_version(env.get(provider_pin_env[key]))
        npm_pkg = PROVIDER_SPECS[key].npm_package
        latest = fetch_npm_latest(npm_pkg) if npm_pkg else None
        notes: list[str] = []
        if not provider.installed:
            notes.append("Provider CLI is not installed.")
        if provider.installed and not provider.authenticated and key in {"claude", "codex"}:
            notes.append("Provider CLI is installed but not logged in.")
        installed_version = normalize_version(provider.version)
        if pinned and installed_version and pinned != installed_version:
            notes.append(
                f"Installed version {installed_version} does not match pinned version {pinned}."
            )
        if pinned and latest and pinned != latest:
            notes.append(f"Pinned version {pinned} differs from upstream latest {latest}.")
        statuses.append(
            DependencyStatus(
                key=key,
                label=provider.label,
                kind="provider",
                installed_version=installed_version,
                pinned_version=pinned,
                upstream_latest=latest,
                healthy=provider.installed,
                notes=notes,
            )
        )

    return statuses


def actionable_statuses(statuses: list[DependencyStatus]) -> list[DependencyStatus]:
    return [status for status in statuses if status.notes]


def dependency_task_description(settings: Settings, status: DependencyStatus) -> str:
    repo_key = next(iter(settings.repos.keys()))
    repo_cfg = settings.repos[repo_key]
    lines = [
        "## Execution",
        f"repo: {repo_key}",
        f"base_branch: {repo_cfg.default_branch}",
        "mode: goal",
        "",
        "## Goal",
        f"Investigate and resolve dependency maintenance issue for {status.label}.",
        "",
        "## Constraints",
        f"- dependency: {status.key}",
        f"- pinned_version: {status.pinned_version or 'none'}",
        f"- installed_version: {status.installed_version or 'none'}",
        f"- upstream_latest: {status.upstream_latest or 'unknown'}",
    ]
    lines.extend(f"- note: {note}" for note in status.notes)
    return "\n".join(lines)


def ensure_follow_up_task(
    client: BoardClient, settings: Settings, status: DependencyStatus
) -> str | None:
    title = f"Dependency maintenance: {status.label}"
    for issue in client.list_issues():
        if str(issue.get("name", "")).strip() == title and issue_status_name(issue) not in {
            "Done",
            "Cancelled",
        }:
            return None
    created = client.create_issue(
        name=title,
        description=dependency_task_description(settings, status),
        state="Ready for AI",
        label_names=["task-kind: improve", "source: dependency-check"],
    )
    return str(created.get("id"))


def issue_status_name(issue: dict[str, Any]) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return str(state.get("name", ""))
    return str(state or "")


def write_dependency_report(
    run_dir: Path, statuses: list[DependencyStatus], created_task_ids: list[str]
) -> list[str]:
    json_path = run_dir / "dependency_report.json"
    md_path = run_dir / "dependency_summary.md"
    json_path.write_text(
        json.dumps(
            {
                "statuses": [asdict(status) for status in statuses],
                "created_task_ids": created_task_ids,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    lines = ["# Dependency Check", "", "## Statuses"]
    for status in statuses:
        lines.append(
            f"- {status.label}: healthy={status.healthy} pinned={status.pinned_version or 'none'} installed={status.installed_version or 'none'} upstream={status.upstream_latest or 'unknown'}"
        )
        for note in status.notes:
            lines.append(f"  - {note}")
    lines.extend(["", "## Created Tasks"])
    lines.extend([f"- {task_id}" for task_id in created_task_ids] or ["- none"])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return [str(json_path), str(md_path)]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check pinned tool versions against installed state and upstream latest versions"
    )
    parser.add_argument("--config", required=True)
    # Renamed from --create-plane-tasks: it always went through
    # make_board_client and was never Plane-specific, only Plane-named.
    parser.add_argument("--create-board-tasks", action="store_true")
    args = parser.parse_args()

    settings = load_settings(args.config)
    env_path = Path(os.environ.get("OPERATIONS_CENTER_ENV_FILE", ".env.operations-center.local"))
    env = load_env_exports(env_path)
    reporter = Reporter(settings.report_root)
    run_id = uuid.uuid4().hex[:12]
    run_dir = reporter.create_run_dir("dependency-check", run_id)
    reporter.write_request_context(run_dir, "dependency-check", run_id, phase="dependency_check")

    statuses = collect_dependency_statuses(settings, env)
    created_task_ids: list[str] = []

    if args.create_board_tasks:
        client = make_board_client(settings)
        try:
            for status in actionable_statuses(statuses):
                task_id = ensure_follow_up_task(client, settings, status)
                if task_id:
                    created_task_ids.append(task_id)
        finally:
            client.close()

    artifacts = write_dependency_report(run_dir, statuses, created_task_ids)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "artifacts": artifacts,
                "statuses": [asdict(status) for status in statuses],
                "created_task_ids": created_task_ids,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
