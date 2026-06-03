# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cxrp.contracts.execution_target import (
    BackendName as CxrpBackendName,
    ExecutionTargetEnvelope,
    ExecutorName as CxrpExecutorName,
    LaneType,
    RuntimeBinding,
)
from cxrp.vocabulary.runtime import RuntimeKind, SelectionMode

from operations_center.contracts.enums import BackendName, LaneName
from operations_center.execution import binding as bmod
from operations_center.execution.binding import (
    InvalidRuntimeBindingError,
    MissingProvenanceError,
    PolicyViolationError,
    TargetBindError,
    UnknownBackendError,
    _AlwaysAllowPolicy,
    _provenance_from_registry,
    _runtime_binding_to_summary,
    bind_execution_target,
)
from operations_center.execution.target import (
    BackendProvenance,
    BoundExecutionTarget,
)


# ── Fixtures / helpers ──────────────────────────────────────────────────


def _envelope(
    *,
    backend=CxrpBackendName.AIDER_LOCAL,
    executor=CxrpExecutorName.AIDER_LOCAL,
    lane=LaneType.CODING_AGENT,
    runtime_binding=None,
):
    return ExecutionTargetEnvelope(
        lane=lane,
        backend=backend,
        executor=executor,
        runtime_binding=runtime_binding,
    )


@pytest.fixture(autouse=True)
def _no_registry(monkeypatch, tmp_path):
    """Chdir into an empty tmp dir so registry/* files do not exist.

    This makes ``_provenance_from_registry`` return None by default
    (no ``registry/source_registry.yaml``), keeping tests hermetic.
    """
    monkeypatch.chdir(tmp_path)


class _RejectPolicy:
    def allows(self, target):
        return False, "nope"


class _AllowPolicy:
    def __init__(self):
        self.seen = None

    def allows(self, target):
        self.seen = target
        return True, ""


# ── _AlwaysAllowPolicy ──────────────────────────────────────────────────


def test_always_allow_policy_returns_true_empty_reason():
    pol = _AlwaysAllowPolicy()
    ok, reason = pol.allows(object())
    assert ok is True
    assert reason == ""


# ── error class hierarchy ───────────────────────────────────────────────


def test_error_subclasses_are_target_bind_errors():
    for cls in (
        UnknownBackendError,
        InvalidRuntimeBindingError,
        PolicyViolationError,
        MissingProvenanceError,
    ):
        assert issubclass(cls, TargetBindError)
    assert issubclass(TargetBindError, ValueError)


# ── _runtime_binding_to_summary ─────────────────────────────────────────


def test_runtime_binding_to_summary_none_returns_none():
    assert _runtime_binding_to_summary(None) is None


def test_runtime_binding_to_summary_with_enum_values():
    rb = RuntimeBinding(
        kind=RuntimeKind.HOSTED_API,
        selection_mode=SelectionMode.EXPLICIT_REQUEST,
        model="m1",
        provider="prov",
        endpoint="http://x",
        config_ref="cfg",
    )
    summary = _runtime_binding_to_summary(rb)
    assert summary is not None
    assert summary.kind == "hosted_api"
    assert summary.selection_mode == "explicit_request"
    assert summary.model == "m1"
    assert summary.provider == "prov"
    assert summary.endpoint == "http://x"
    assert summary.config_ref == "cfg"


def test_runtime_binding_to_summary_with_string_kind_branches():
    """kind/selection_mode given as plain strings hit the str() fallback.

    Uses valid pairing strings so the resulting RuntimeBindingSummary
    passes its own __post_init__ validation.
    """
    rb = SimpleNamespace(
        kind="backend_default",
        selection_mode="backend_default",
        model=None,
        provider=None,
        endpoint=None,
        config_ref=None,
    )
    summary = _runtime_binding_to_summary(rb)
    assert summary.kind == "backend_default"
    assert summary.selection_mode == "backend_default"


def test_runtime_binding_to_summary_missing_field_raises():
    rb = SimpleNamespace(kind=RuntimeKind.HUMAN)  # missing selection_mode etc.
    with pytest.raises(InvalidRuntimeBindingError) as exc:
        _runtime_binding_to_summary(rb)
    assert "missing required field" in str(exc.value)


# ── _provenance_from_registry ───────────────────────────────────────────


def test_provenance_import_error_returns_none(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "source_registry":
            raise ImportError("boom")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert _provenance_from_registry("aider_local") is None


def test_provenance_missing_registry_file_returns_none(tmp_path, monkeypatch):
    # autouse fixture already chdir'd to an empty tmp dir → no registry file
    assert _provenance_from_registry("aider_local") is None


def test_provenance_from_yaml_exception_returns_none(monkeypatch, tmp_path):
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry" / "source_registry.yaml").write_text("x: 1\n")

    def _boom(_path):
        raise RuntimeError("bad yaml")

    monkeypatch.setattr(bmod, "_DEFAULT_REGISTRY_PATH", "registry/source_registry.yaml")
    import source_registry

    monkeypatch.setattr(source_registry.SourceRegistry, "from_yaml", staticmethod(_boom))
    assert _provenance_from_registry("aider_local") is None


def _make_registry_yaml(tmp_path):
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry" / "source_registry.yaml").write_text("placeholder\n")


def test_provenance_github_url_stripped_with_git_suffix(monkeypatch, tmp_path):
    _make_registry_yaml(tmp_path)
    import source_registry

    entry = SimpleNamespace(
        fork_url="https://github.com/owner/repo.git",
        upstream_url="https://github.com/up/stream.git",
        expected_sha="abc123",
    )
    fake_reg = SimpleNamespace(resolve=lambda bid: entry)
    monkeypatch.setattr(
        source_registry.SourceRegistry, "from_yaml", staticmethod(lambda p: fake_reg)
    )
    # No patches dir → patch_ids stays []
    prov = _provenance_from_registry("aider_local")
    assert isinstance(prov, BackendProvenance)
    assert prov.source == "registry"
    assert prov.repo == "owner/repo"
    assert prov.ref == "abc123"
    assert prov.patches == []


def test_provenance_falls_back_to_upstream_url(monkeypatch, tmp_path):
    _make_registry_yaml(tmp_path)
    import source_registry

    entry = SimpleNamespace(
        fork_url=None,
        upstream_url="https://github.com/up/stream",
        expected_sha="sha9",
    )
    fake_reg = SimpleNamespace(resolve=lambda bid: entry)
    monkeypatch.setattr(
        source_registry.SourceRegistry, "from_yaml", staticmethod(lambda p: fake_reg)
    )
    prov = _provenance_from_registry("direct_local")
    assert prov.repo == "up/stream"
    assert prov.ref == "sha9"


def test_provenance_non_github_repo_left_untouched(monkeypatch, tmp_path):
    _make_registry_yaml(tmp_path)
    import source_registry

    entry = SimpleNamespace(
        fork_url="git@gitlab.com:owner/repo.git",
        upstream_url=None,
        expected_sha="z",
    )
    fake_reg = SimpleNamespace(resolve=lambda bid: entry)
    monkeypatch.setattr(
        source_registry.SourceRegistry, "from_yaml", staticmethod(lambda p: fake_reg)
    )
    prov = _provenance_from_registry("openclaw")
    # Not a github.com URL → untouched
    assert prov.repo == "git@gitlab.com:owner/repo.git"


def test_provenance_with_patches_loaded(monkeypatch, tmp_path):
    _make_registry_yaml(tmp_path)
    (tmp_path / "registry" / "patches").mkdir()
    import source_registry

    entry = SimpleNamespace(
        fork_url="https://github.com/owner/repo",
        upstream_url=None,
        expected_sha="r1",
    )
    fake_reg = SimpleNamespace(resolve=lambda bid: entry)
    monkeypatch.setattr(
        source_registry.SourceRegistry, "from_yaml", staticmethod(lambda p: fake_reg)
    )

    patches = [SimpleNamespace(patch_id="p1"), SimpleNamespace(patch_id="p2")]
    fake_patch_reg = SimpleNamespace(for_source=lambda bid: patches)
    monkeypatch.setattr(source_registry, "load_patches", lambda root: fake_patch_reg)

    prov = _provenance_from_registry("aider_local")
    assert prov.repo == "owner/repo"
    assert prov.patches == ["p1", "p2"]


def test_provenance_patches_load_exception_yields_empty_list(monkeypatch, tmp_path):
    _make_registry_yaml(tmp_path)
    (tmp_path / "registry" / "patches").mkdir()
    import source_registry

    entry = SimpleNamespace(
        fork_url="https://github.com/owner/repo",
        upstream_url=None,
        expected_sha="r2",
    )
    fake_reg = SimpleNamespace(resolve=lambda bid: entry)
    monkeypatch.setattr(
        source_registry.SourceRegistry, "from_yaml", staticmethod(lambda p: fake_reg)
    )

    def _boom(root):
        raise RuntimeError("patch load failed")

    monkeypatch.setattr(source_registry, "load_patches", _boom)
    prov = _provenance_from_registry("aider_local")
    assert prov.patches == []


# ── bind_execution_target ───────────────────────────────────────────────


def test_bind_missing_backend_raises():
    env = _envelope(backend=None)
    with pytest.raises(UnknownBackendError) as exc:
        bind_execution_target(env)
    assert "backend is required" in str(exc.value)


def test_bind_happy_path_no_optionals():
    env = _envelope()
    target = bind_execution_target(env)
    assert isinstance(target, BoundExecutionTarget)
    assert target.backend == BackendName.AIDER_LOCAL
    assert target.executor == LaneName.AIDER_LOCAL
    assert target.lane == "coding_agent"
    # registry file absent → provenance None
    assert target.provenance is None
    assert target.runtime_binding is None


def test_bind_executor_none_yields_none_executor():
    env = _envelope(executor=None)
    target = bind_execution_target(env)
    assert target.executor is None


def test_bind_lane_without_value_attr_uses_str(monkeypatch):
    env = _envelope()
    # Replace lane with a plain string (no .value) → str() branch
    object.__setattr__(env, "lane", "plain_lane")
    target = bind_execution_target(env)
    assert target.lane == "plain_lane"


def test_bind_catalog_membership_ok():
    env = _envelope(backend=CxrpBackendName.AIDER_LOCAL)
    catalog = SimpleNamespace(entries={"aider_local": object()})
    target = bind_execution_target(env, catalog=catalog)
    assert target.backend == BackendName.AIDER_LOCAL


def test_bind_catalog_membership_missing_raises():
    env = _envelope(backend=CxrpBackendName.AIDER_LOCAL)
    catalog = SimpleNamespace(entries={"openclaw": object()})
    with pytest.raises(UnknownBackendError) as exc:
        bind_execution_target(env, catalog=catalog)
    assert "not present in executor catalog" in str(exc.value)


def test_bind_with_runtime_binding_populates_summary():
    rb = RuntimeBinding(
        kind=RuntimeKind.LOCAL_MODEL_SERVER,
        selection_mode=SelectionMode.POLICY_SELECTED,
        model="qwen",
    )
    env = _envelope(runtime_binding=rb)
    target = bind_execution_target(env)
    assert target.runtime_binding is not None
    assert target.runtime_binding.kind == "local_model_server"
    assert target.runtime_binding.model == "qwen"


def test_bind_require_provenance_missing_raises():
    env = _envelope()
    with pytest.raises(MissingProvenanceError) as exc:
        bind_execution_target(env, require_provenance=True)
    assert "no provenance entry" in str(exc.value)


def test_bind_require_provenance_present_ok(monkeypatch):
    env = _envelope()
    prov = BackendProvenance(source="registry", repo="o/r", ref="abc")
    monkeypatch.setattr(bmod, "_provenance_from_registry", lambda bid: prov)
    target = bind_execution_target(env, require_provenance=True)
    assert target.provenance == prov


def test_bind_policy_rejects_raises():
    env = _envelope()
    with pytest.raises(PolicyViolationError) as exc:
        bind_execution_target(env, policy=_RejectPolicy())
    assert "policy rejected bound target: nope" in str(exc.value)


def test_bind_policy_allows_passes_built_target():
    env = _envelope()
    pol = _AllowPolicy()
    target = bind_execution_target(env, policy=pol)
    assert pol.seen is target
    assert isinstance(target, BoundExecutionTarget)


def test_bind_default_policy_used_when_none(monkeypatch):
    """No policy supplied → _AlwaysAllowPolicy lets it through."""
    env = _envelope()
    target = bind_execution_target(env, policy=None)
    assert isinstance(target, BoundExecutionTarget)


def test_bind_invalid_runtime_binding_propagates(monkeypatch):
    env = _envelope()
    bad = SimpleNamespace(kind=RuntimeKind.HUMAN)  # missing fields
    object.__setattr__(env, "runtime_binding", bad)
    with pytest.raises(InvalidRuntimeBindingError):
        bind_execution_target(env)
