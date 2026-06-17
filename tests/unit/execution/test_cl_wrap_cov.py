# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Hermetic branch-coverage tests for execution.cl_wrap.

Covers lineage derivation, the `_to_dict` serialization variants, the
`_cl_active` gate, and every branch of the `cl_dispatch_wrap` context
manager (no-op, happy path, hydrate AnchorMissing/SessionNotStarted,
generic hydrate failure, error payload, no_result payload, capture
failure swallowed). All collaborators are mocked; no real
`context_lifecycle`, network, or filesystem use.
"""

from __future__ import annotations

import sys
import types

import pytest

from operations_center.execution import cl_wrap


# ---------------------------------------------------------------------------
# Fake context_lifecycle plumbing
# ---------------------------------------------------------------------------


class _FakeCL:
    def __init__(self) -> None:
        self.hydrate_calls: list[tuple[str, dict]] = []
        self.capture_calls: list[tuple[str, dict]] = []
        self.hydrate_exc: BaseException | None = None
        self.capture_exc: BaseException | None = None

    def hydrate(self, lineage_id, work_item):
        self.hydrate_calls.append((lineage_id, work_item))
        if self.hydrate_exc is not None:
            raise self.hydrate_exc
        return {"lineage_id": lineage_id, "hydrated": True}

    def capture(self, lineage_id, result):
        if self.capture_exc is not None:
            raise self.capture_exc
        self.capture_calls.append((lineage_id, result))


class _AnchorMissingError(Exception):
    pass


class _SessionNotStartedError(Exception):
    pass


def _install_fake_cl(monkeypatch, state: _FakeCL) -> None:
    fake = types.ModuleType("context_lifecycle")
    fake.hydrate = state.hydrate  # type: ignore[attr-defined]
    fake.capture = state.capture  # type: ignore[attr-defined]
    fake.AnchorMissing = _AnchorMissingError  # type: ignore[attr-defined]
    fake.SessionNotStarted = _SessionNotStartedError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "context_lifecycle", fake)
    monkeypatch.setenv("CL_ANCHOR", "/tmp/anchor")


@pytest.fixture
def fake_cl(monkeypatch):
    state = _FakeCL()
    _install_fake_cl(monkeypatch, state)
    return state


# ---------------------------------------------------------------------------
# derive_lineage_id
# ---------------------------------------------------------------------------


def test_derive_prefers_lineage_id() -> None:
    wi = types.SimpleNamespace(lineage_id="l-explicit", run_id="r-x", proposal_id="p-x")
    assert cl_wrap.derive_lineage_id(wi) == "l-explicit"


def test_derive_falls_back_to_run_id() -> None:
    wi = types.SimpleNamespace(run_id="r-42")
    assert cl_wrap.derive_lineage_id(wi) == "l-r-42"


def test_derive_falls_back_to_proposal_id() -> None:
    wi = types.SimpleNamespace(proposal_id="p-9")
    assert cl_wrap.derive_lineage_id(wi) == "l-p-9"


def test_derive_unknown_when_no_attrs() -> None:
    assert cl_wrap.derive_lineage_id(object()) == "l-unknown"


def test_derive_preserves_existing_prefix() -> None:
    wi = types.SimpleNamespace(run_id="l-already")
    assert cl_wrap.derive_lineage_id(wi) == "l-already"


def test_derive_skips_non_string_values() -> None:
    # run_id is an int (not str) -> skipped; proposal_id used instead.
    wi = types.SimpleNamespace(lineage_id=None, run_id=123, proposal_id="p-1")
    assert cl_wrap.derive_lineage_id(wi) == "l-p-1"


def test_derive_skips_empty_string() -> None:
    wi = types.SimpleNamespace(run_id="", proposal_id="p-2")
    assert cl_wrap.derive_lineage_id(wi) == "l-p-2"


# ---------------------------------------------------------------------------
# _to_dict
# ---------------------------------------------------------------------------


def test_to_dict_none() -> None:
    assert cl_wrap._to_dict(None) == {}


def test_to_dict_passes_dict_copy() -> None:
    src = {"a": 1}
    out = cl_wrap._to_dict(src)
    assert out == {"a": 1}
    out["b"] = 2
    assert "b" not in src  # copy, not alias


def test_to_dict_model_dump_json_mode() -> None:
    class _Model:
        def model_dump(self, mode=None):
            assert mode == "json"
            return {"kind": "model", "mode": mode}

    assert cl_wrap._to_dict(_Model()) == {"kind": "model", "mode": "json"}


def test_to_dict_model_dump_json_fails_then_plain() -> None:
    class _Model:
        def model_dump(self, mode=None):
            if mode == "json":
                raise ValueError("no json mode")
            return {"plain": True}

    assert cl_wrap._to_dict(_Model()) == {"plain": True}


def test_to_dict_model_dump_both_fail_falls_through() -> None:
    class _Model:
        def model_dump(self, mode=None):
            raise RuntimeError("always broken")

        def __init__(self) -> None:
            self.x = 5
            self._hidden = 9

    # Both model_dump attempts fail; falls through to __dict__ scan.
    out = cl_wrap._to_dict(_Model())
    assert out == {"x": 5}


def test_to_dict_vars_strips_underscore() -> None:
    class _Obj:
        def __init__(self) -> None:
            self.public = "p"
            self._private = "q"

    assert cl_wrap._to_dict(_Obj()) == {"public": "p"}


def test_to_dict_repr_fallback() -> None:
    class _Slots:
        __slots__ = ()

    out = cl_wrap._to_dict(_Slots())
    assert set(out.keys()) == {"value"}
    assert "_Slots" in out["value"]


def test_to_dict_non_callable_model_dump_uses_dict() -> None:
    class _Obj:
        model_dump = "not callable"

        def __init__(self) -> None:
            self.field = 1

    out = cl_wrap._to_dict(_Obj())
    # model_dump not callable -> skipped; __dict__ used. (class attr excluded)
    assert out == {"field": 1}


# ---------------------------------------------------------------------------
# _cl_active gate
# ---------------------------------------------------------------------------


def test_cl_active_false_without_anchor(monkeypatch) -> None:
    monkeypatch.delenv("CL_ANCHOR", raising=False)
    assert cl_wrap._cl_active() is False


def test_cl_active_false_on_import_error(monkeypatch) -> None:
    monkeypatch.setenv("CL_ANCHOR", "/tmp/anchor")
    monkeypatch.setitem(sys.modules, "context_lifecycle", None)
    # Setting sys.modules[name] = None makes import raise ImportError.
    assert cl_wrap._cl_active() is False


def test_cl_active_true_when_importable(fake_cl) -> None:
    assert cl_wrap._cl_active() is True


# ---------------------------------------------------------------------------
# cl_dispatch_wrap — no-op gate
# ---------------------------------------------------------------------------


def test_wrap_noop_yields_stub(monkeypatch) -> None:
    monkeypatch.delenv("CL_ANCHOR", raising=False)
    wi = types.SimpleNamespace(run_id="r-1")
    with cl_wrap.cl_dispatch_wrap(wi) as ctx:
        assert ctx["lineage_id"] is None
        # set_result is a callable no-op returning None.
        assert ctx["set_result"]({"status": "ok"}) is None
        assert "hydrated_context" not in ctx


# ---------------------------------------------------------------------------
# cl_dispatch_wrap — active happy path
# ---------------------------------------------------------------------------


def test_wrap_active_hydrate_then_capture(fake_cl) -> None:
    wi = types.SimpleNamespace(run_id="r-abc", repo_key="svc")
    with cl_wrap.cl_dispatch_wrap(wi) as ctx:
        assert ctx["lineage_id"] == "l-r-abc"
        assert ctx["hydrated_context"] == {"lineage_id": "l-r-abc", "hydrated": True}
        ctx["set_result"]({"status": "ok", "repo_key": "svc"})

    assert fake_cl.hydrate_calls == [("l-r-abc", {"run_id": "r-abc", "repo_key": "svc"})]
    assert len(fake_cl.capture_calls) == 1
    lineage, payload = fake_cl.capture_calls[0]
    assert lineage == "l-r-abc"
    assert payload["status"] == "ok"
    assert payload["repo_key"] == "svc"
    assert payload["lineage_id"] == "l-r-abc"


def test_wrap_active_result_without_lineage_gets_default(fake_cl) -> None:
    wi = types.SimpleNamespace(run_id="r-2")
    with cl_wrap.cl_dispatch_wrap(wi) as ctx:
        ctx["set_result"]({"status": "ok"})
    _, payload = fake_cl.capture_calls[0]
    # setdefault fills in lineage_id when result omits it.
    assert payload["lineage_id"] == "l-r-2"


def test_wrap_active_result_keeps_own_lineage(fake_cl) -> None:
    wi = types.SimpleNamespace(run_id="r-3")
    with cl_wrap.cl_dispatch_wrap(wi) as ctx:
        ctx["set_result"]({"status": "ok", "lineage_id": "l-override"})
    _, payload = fake_cl.capture_calls[0]
    # setdefault must NOT clobber a lineage the result already carries.
    assert payload["lineage_id"] == "l-override"


def test_wrap_set_result_replaces_prior(fake_cl) -> None:
    wi = types.SimpleNamespace(run_id="r-4")
    with cl_wrap.cl_dispatch_wrap(wi) as ctx:
        ctx["set_result"]({"status": "first", "a": 1})
        ctx["set_result"]({"status": "second"})
    _, payload = fake_cl.capture_calls[0]
    assert payload["status"] == "second"
    assert "a" not in payload  # cleared on replace


# ---------------------------------------------------------------------------
# cl_dispatch_wrap — no_result branch
# ---------------------------------------------------------------------------


def test_wrap_active_no_result_payload(fake_cl) -> None:
    wi = types.SimpleNamespace(run_id="r-quiet")
    with cl_wrap.cl_dispatch_wrap(wi):
        pass
    _, payload = fake_cl.capture_calls[0]
    assert payload == {"lineage_id": "l-r-quiet", "status": "no_result"}


# ---------------------------------------------------------------------------
# cl_dispatch_wrap — error branch
# ---------------------------------------------------------------------------


def test_wrap_active_error_payload_and_reraise(fake_cl) -> None:
    wi = types.SimpleNamespace(run_id="r-boom")
    with pytest.raises(RuntimeError, match="boom"):
        with cl_wrap.cl_dispatch_wrap(wi):
            raise RuntimeError("boom")
    lineage, payload = fake_cl.capture_calls[0]
    assert lineage == "l-r-boom"
    assert payload["status"] == "error"
    assert payload["error"] == "RuntimeError: boom"
    assert payload["lineage_id"] == "l-r-boom"


def test_wrap_error_takes_precedence_over_set_result(fake_cl) -> None:
    wi = types.SimpleNamespace(run_id="r-mix")
    with pytest.raises(ValueError, match="late"):
        with cl_wrap.cl_dispatch_wrap(wi) as ctx:
            ctx["set_result"]({"status": "ok"})
            raise ValueError("late")
    _, payload = fake_cl.capture_calls[0]
    # Even though set_result ran, the error payload wins.
    assert payload["status"] == "error"
    assert "ValueError: late" in payload["error"]


# ---------------------------------------------------------------------------
# cl_dispatch_wrap — hydrate failure branches
# ---------------------------------------------------------------------------


def test_wrap_hydrate_anchor_missing_falls_back(fake_cl) -> None:
    fake_cl.hydrate_exc = _AnchorMissingError("gone")
    wi = types.SimpleNamespace(run_id="r-am")
    with cl_wrap.cl_dispatch_wrap(wi) as ctx:
        assert ctx["lineage_id"] == "l-r-am"
        assert "hydrated_context" not in ctx
        assert ctx["set_result"]({"status": "ok"}) is None
    # Fallback path: hydrate attempted once, capture never called.
    assert len(fake_cl.hydrate_calls) == 1
    assert fake_cl.capture_calls == []


def test_wrap_hydrate_session_not_started_falls_back(fake_cl) -> None:
    fake_cl.hydrate_exc = _SessionNotStartedError("nope")
    wi = types.SimpleNamespace(run_id="r-sns")
    with cl_wrap.cl_dispatch_wrap(wi) as ctx:
        assert ctx["lineage_id"] == "l-r-sns"
    assert fake_cl.capture_calls == []


def test_wrap_hydrate_generic_error_falls_back(fake_cl) -> None:
    fake_cl.hydrate_exc = RuntimeError("hydrate exploded")
    wi = types.SimpleNamespace(run_id="r-gen")
    with cl_wrap.cl_dispatch_wrap(wi) as ctx:
        assert ctx["lineage_id"] == "l-r-gen"
        assert "hydrated_context" not in ctx
    assert fake_cl.capture_calls == []


# ---------------------------------------------------------------------------
# cl_dispatch_wrap — capture failure swallowed
# ---------------------------------------------------------------------------


def test_wrap_capture_failure_swallowed(fake_cl) -> None:
    fake_cl.capture_exc = RuntimeError("capture write failed")
    wi = types.SimpleNamespace(run_id="r-cap")
    # Must not raise despite capture blowing up.
    with cl_wrap.cl_dispatch_wrap(wi) as ctx:
        ctx["set_result"]({"status": "ok"})
    # capture raised before recording, so nothing was appended.
    assert fake_cl.capture_calls == []


def test_wrap_capture_failure_during_error_still_reraises_original(fake_cl) -> None:
    fake_cl.capture_exc = RuntimeError("capture down")
    wi = types.SimpleNamespace(run_id="r-both")
    # The original dispatch error must propagate; capture failure swallowed.
    with pytest.raises(KeyError, match="orig"):
        with cl_wrap.cl_dispatch_wrap(wi):
            raise KeyError("orig")
