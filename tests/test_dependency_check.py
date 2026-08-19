# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
import importlib.metadata
from types import SimpleNamespace

from operations_center.entrypoints.maintenance.dependency_check import (
    DependencyStatus,
    actionable_statuses,
    dependency_task_description,
    executor_backend_status,
    normalize_version,
)


def test_normalize_version_extracts_semver() -> None:
    assert normalize_version("codex-cli 0.117.0") == "0.117.0"
    assert normalize_version("v1.2.3") == "v1.2.3"


def test_executor_backend_status_reports_unimportable_module() -> None:
    assert executor_backend_status("definitely_not_an_installed_backend") == (False, None)


def test_executor_backend_status_reports_importable_module_without_distribution() -> None:
    # Importable but not backed by a distribution — the shape an editable sibling
    # checkout takes when its metadata does not map the top-level module back.
    assert executor_backend_status("json") == (True, None)


def test_executor_backend_status_reports_installed_distribution_version() -> None:
    importable, version = executor_backend_status("pydantic")

    assert importable is True
    assert version == normalize_version(importlib.metadata.version("pydantic"))


def test_actionable_statuses_filters_to_items_with_notes() -> None:
    statuses = [
        DependencyStatus(
            "team_executor", "TeamExecutor", "library", "1.0.0", "1.0.0", "1.1.0", True, []
        ),
        DependencyStatus(
            "codex",
            "Codex",
            "provider",
            "0.117.0",
            "0.117.0",
            "0.118.0",
            True,
            ["Pinned version differs"],
        ),
    ]
    assert [status.key for status in actionable_statuses(statuses)] == ["codex"]


def test_dependency_task_description_uses_default_repo_and_context() -> None:
    settings = SimpleNamespace(
        repos={
            "operations-center": SimpleNamespace(default_branch="main"),
        }
    )
    description = dependency_task_description(
        settings=settings,
        status=DependencyStatus(
            key="codex",
            label="Codex",
            kind="provider",
            installed_version="0.117.0",
            pinned_version="0.117.0",
            upstream_latest="0.118.0",
            healthy=True,
            notes=["Pinned version differs"],
        ),
    )
    assert "repo: operations-center" in description
    assert "base_branch: main" in description
    assert "mode: goal" in description
    assert "dependency: codex" in description


# ── board health probe ───────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status_code, payload=None, raises=False):
        self.status_code = status_code
        self._payload = payload
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


class _Settings:
    class forgejo:
        base_url = "http://forge.local"


def _probe(monkeypatch, response):
    from operations_center.entrypoints.maintenance import dependency_check

    monkeypatch.setattr(
        dependency_check.httpx, "get", lambda *a, **k: response, raising=True
    )
    return dependency_check.current_board_status(_Settings())


def test_board_status_reports_version_and_health(monkeypatch):
    assert _probe(monkeypatch, _Resp(200, {"version": "13.0.5+gitea-1.22.0"})) == (
        "13.0.5",
        True,
    )


def test_board_status_survives_a_non_json_body(monkeypatch):
    """A 200 carrying HTML — a proxy interstitial, a login page.

    The decode error must not escape: this function is how the dependency
    report *learns* the board is unusable, so raising here would take down the
    whole report over one row. And "healthy" would be the wrong answer, because
    the fleet cannot use that as a board.
    """
    assert _probe(monkeypatch, _Resp(200, raises=True)) == (None, False)


def test_board_status_rejects_a_non_object_payload(monkeypatch):
    assert _probe(monkeypatch, _Resp(200, ["not", "an", "object"])) == (None, False)


def test_board_status_reports_http_errors_as_unhealthy(monkeypatch):
    assert _probe(monkeypatch, _Resp(503)) == (None, False)


def test_board_status_without_a_forgejo_block_is_unhealthy(monkeypatch):
    from operations_center.entrypoints.maintenance import dependency_check

    class _NoBoard:
        forgejo = None

    assert dependency_check.current_board_status(_NoBoard()) == (None, False)
