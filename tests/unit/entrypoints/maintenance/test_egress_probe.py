"""Tests for the controller-tier egress-boundary probe (D-OP-2)."""

from __future__ import annotations

from datetime import UTC, datetime

from operations_center.entrypoints.maintenance.egress_probe import (
    EgressProbeTask,
    _http_probe,
)
from operations_center.maintenance.contracts import MaintenanceContext

_PROXY = "http://127.0.0.1:8889"


def _ctx(plane_client=None) -> MaintenanceContext:
    resources = {"plane_client": plane_client} if plane_client is not None else {}
    return MaintenanceContext(
        cycle_id="c", now=datetime(2026, 6, 21, tzinfo=UTC), resources=resources
    )


class _FakePlane:
    """Minimal Plane stub recording created issues + serving existing ones."""

    def __init__(self, existing: list[dict] | None = None) -> None:
        self.existing = existing or []
        self.created: list[dict] = []

    def list_issues(self) -> list[dict]:
        return self.existing

    def create_issue(self, *, name, description, label_names=None):
        issue = {"id": f"new-{len(self.created)}", "name": name}
        self.created.append(
            {"name": name, "description": description, "labels": label_names}
        )
        return issue


def _probe(mapping: dict[str, str]):
    """Build a probe_fn returning a fixed outcome per host."""

    def _fn(proxy, host, **_):
        assert proxy == _PROXY
        return mapping[host]

    return _fn


def test_skipped_when_no_proxy_configured(monkeypatch):
    monkeypatch.delenv("OC_EGRESS_PROXY", raising=False)
    task = EgressProbeTask(settings=None, probe_fn=_probe({}))
    result = task.run_once(_ctx())
    assert result.status == "skipped"
    assert "not configured" in result.details["reason"]


def test_healthy_boundary_is_ok(monkeypatch):
    monkeypatch.setenv("OC_EGRESS_PROXY", _PROXY)
    plane = _FakePlane()
    task = EgressProbeTask(
        settings=None,
        probe_fn=_probe({"github.com": "allowed", "example.com": "denied"}),
    )
    result = task.run_once(_ctx(plane))
    assert result.status == "ok"
    assert result.details["allow_result"] == "allowed"
    assert result.details["deny_result"] == "denied"
    assert plane.created == []


def test_proxy_down_is_skipped_fail_open(monkeypatch):
    monkeypatch.setenv("OC_EGRESS_PROXY", _PROXY)
    plane = _FakePlane()
    task = EgressProbeTask(
        settings=None,
        probe_fn=_probe({"github.com": "proxy_down", "example.com": "denied"}),
    )
    result = task.run_once(_ctx(plane))
    assert result.status == "skipped"
    assert "unreachable" in result.details["reason"]
    assert plane.created == []


def test_rot_allowlisted_denied_opens_fix_task(monkeypatch):
    monkeypatch.setenv("OC_EGRESS_PROXY", _PROXY)
    plane = _FakePlane()
    task = EgressProbeTask(
        settings=None,
        probe_fn=_probe({"github.com": "denied", "example.com": "denied"}),
    )
    result = task.run_once(_ctx(plane))
    assert result.status == "failed"
    assert "DENIED" in result.error
    assert len(plane.created) == 1
    assert plane.created[0]["name"].startswith("[egress-probe] proxy boundary fault")
    assert result.details["fix_task"].startswith("created:")


def test_breach_denied_allowed_opens_fix_task(monkeypatch):
    monkeypatch.setenv("OC_EGRESS_PROXY", _PROXY)
    plane = _FakePlane()
    task = EgressProbeTask(
        settings=None,
        probe_fn=_probe({"github.com": "allowed", "example.com": "allowed"}),
    )
    result = task.run_once(_ctx(plane))
    assert result.status == "failed"
    assert "ALLOWED" in result.error
    assert len(plane.created) == 1


def test_existing_open_fault_is_not_duplicated(monkeypatch):
    monkeypatch.setenv("OC_EGRESS_PROXY", _PROXY)
    plane = _FakePlane(
        existing=[
            {
                "id": "abc",
                "name": "[egress-probe] proxy boundary fault: old",
                "state": {"name": "In Progress"},
            }
        ]
    )
    task = EgressProbeTask(
        settings=None,
        probe_fn=_probe({"github.com": "denied", "example.com": "denied"}),
    )
    result = task.run_once(_ctx(plane))
    assert result.status == "failed"
    assert plane.created == []
    assert result.details["fix_task"] == "exists:abc"


def test_terminal_fault_does_not_suppress_new_task(monkeypatch):
    monkeypatch.setenv("OC_EGRESS_PROXY", _PROXY)
    plane = _FakePlane(
        existing=[
            {
                "id": "old",
                "name": "[egress-probe] proxy boundary fault: resolved",
                "state": {"name": "Done"},
            }
        ]
    )
    task = EgressProbeTask(
        settings=None,
        probe_fn=_probe({"github.com": "denied", "example.com": "denied"}),
    )
    result = task.run_once(_ctx(plane))
    assert result.status == "failed"
    assert len(plane.created) == 1


def test_file_failure_does_not_raise(monkeypatch):
    monkeypatch.setenv("OC_EGRESS_PROXY", _PROXY)

    class _Broken:
        def list_issues(self):
            raise RuntimeError("plane down")

    task = EgressProbeTask(
        settings=None,
        probe_fn=_probe({"github.com": "denied", "example.com": "denied"}),
    )
    result = task.run_once(_ctx(_Broken()))
    assert result.status == "failed"
    assert result.details["fix_task"].startswith("file_failed:")


def test_http_probe_classifies_httpx_errors(monkeypatch):
    import httpx

    calls = {}

    class _Client:
        def __init__(self, **kw):
            calls.update(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            if "deny" in url:
                raise httpx.ProxyError("403 Forbidden")
            if "down" in url:
                raise httpx.ConnectError("All connection attempts to proxy failed")
            return None

    monkeypatch.setattr(httpx, "Client", _Client)
    assert _http_probe(_PROXY, "allow.test") == "allowed"
    assert _http_probe(_PROXY, "deny.test") == "denied"
    assert _http_probe(_PROXY, "down.test") == "proxy_down"
    assert calls["proxy"] == _PROXY


def test_satisfies_maintenance_task_protocol():
    from operations_center.maintenance.contracts import MaintenanceTask

    task = EgressProbeTask(settings=None)
    assert isinstance(task, MaintenanceTask)
    assert task.name == "egress_probe"
    assert task.interval_seconds > 0
    assert task.enabled is True


def test_registers_in_a_maintenance_registry():
    from operations_center.maintenance.registry import MaintenanceRegistry

    registry = MaintenanceRegistry()
    registry.register(EgressProbeTask(settings=None))
    assert "egress_probe" in {t.name for t in registry.list_tasks()}
