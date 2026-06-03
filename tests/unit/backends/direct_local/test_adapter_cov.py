# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from rxp.contracts import RuntimeInvocation, RuntimeResult

import operations_center.backends.direct_local.adapter as adapter_mod
from operations_center.backends.direct_local.adapter import (
    DirectLocalBackendAdapter,
    _DirectLocalRunResult,
    _diff_stat,
    _discover_changed_files,
    _failure_category,
    _failure_status,
    _git_status_to_change_type,
    _read_capture,
    _short_id,
    _truncate,
)
from operations_center.config.settings import AiderSettings
from operations_center.contracts.common import ChangedFileRef
from operations_center.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(**kw) -> AiderSettings:
    defaults = dict(binary="aider", model_prefix="openai", profile="capable", timeout_seconds=30)
    defaults.update(kw)
    return AiderSettings(**defaults)


def _request(tmp_path: Path, **kw) -> ExecutionRequest:
    defaults = dict(
        proposal_id="prop-1",
        decision_id="dec-1",
        goal_text="Fix all lint errors",
        repo_key="api-service",
        clone_url="https://git.example.com/api.git",
        base_branch="main",
        task_branch="auto/lint-abc",
        workspace_path=tmp_path / "repo",
    )
    defaults.update(kw)
    return ExecutionRequest(**defaults)


class _FakeRuntime:
    """CoreRunner stand-in. Writes stdout/stderr into the invocation
    artifact directory and returns a synthetic RuntimeResult. Captures
    the invocation it was handed for inspection."""

    def __init__(
        self,
        *,
        status: str = "succeeded",
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        error_summary: str | None = None,
        raise_exc: BaseException | None = None,
        write_paths: bool = True,
    ) -> None:
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code if exit_code is not None else (0 if status == "succeeded" else 1)
        self.error_summary = error_summary
        self.raise_exc = raise_exc
        self.write_paths = write_paths
        self.last_invocation: RuntimeInvocation | None = None

    def run(self, invocation: RuntimeInvocation) -> RuntimeResult:
        self.last_invocation = invocation
        if self.raise_exc is not None:
            raise self.raise_exc
        ar = Path(invocation.artifact_directory) if invocation.artifact_directory else Path("/tmp")
        sout_path = None
        serr_path = None
        if self.write_paths:
            ar.mkdir(parents=True, exist_ok=True)
            sout = ar / "stdout.txt"
            serr = ar / "stderr.txt"
            sout.write_text(self.stdout, encoding="utf-8")
            serr.write_text(self.stderr, encoding="utf-8")
            sout_path = str(sout)
            serr_path = str(serr)
        now = datetime.now(timezone.utc).isoformat()
        return RuntimeResult(
            invocation_id=invocation.invocation_id,
            runtime_name=invocation.runtime_name,
            runtime_kind=invocation.runtime_kind,
            status=self.status,
            exit_code=self.exit_code,
            started_at=now,
            finished_at=now,
            stdout_path=sout_path,
            stderr_path=serr_path,
            error_summary=self.error_summary,
        )


def _adapter(runtime: _FakeRuntime | None = None, **settings_kw) -> DirectLocalBackendAdapter:
    return DirectLocalBackendAdapter(_settings(**settings_kw), runtime=runtime or _FakeRuntime())


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_uses_injected_runtime(self):
        rt = _FakeRuntime()
        adapter = DirectLocalBackendAdapter(_settings(), runtime=rt)
        assert adapter._runtime is rt

    def test_default_runtime_constructed_when_none(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(adapter_mod, "CoreRunner", lambda: sentinel)
        adapter = DirectLocalBackendAdapter(_settings(), runtime=None)
        assert adapter._runtime is sentinel

    def test_stores_settings(self):
        s = _settings(binary="zzz")
        adapter = DirectLocalBackendAdapter(s, runtime=_FakeRuntime())
        assert adapter._settings is s


# ---------------------------------------------------------------------------
# execute() happy path
# ---------------------------------------------------------------------------


class TestExecuteSuccess:
    def test_succeeded_status_and_success_flag(self, tmp_path, monkeypatch):
        monkeypatch.setattr(adapter_mod, "_discover_changed_files", lambda p: ([], "unknown", 0.0))
        (tmp_path / "repo").mkdir()
        result = _adapter(_FakeRuntime(stdout="all done")).execute(_request(tmp_path))
        assert isinstance(result, ExecutionResult)
        assert result.status == ExecutionStatus.SUCCEEDED
        assert result.success is True
        assert result.failure_reason is None
        assert result.failure_category is None

    def test_log_artifact_added_when_output_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(adapter_mod, "_discover_changed_files", lambda p: ([], "unknown", 0.0))
        (tmp_path / "repo").mkdir()
        result = _adapter(_FakeRuntime(stdout="some output")).execute(_request(tmp_path))
        assert len(result.artifacts) == 1
        art = result.artifacts[0]
        assert art.artifact_type == ArtifactType.LOG_EXCERPT
        assert art.label == "direct_local run log"
        assert "some output" in art.content

    def test_no_artifact_when_no_output(self, tmp_path, monkeypatch):
        monkeypatch.setattr(adapter_mod, "_discover_changed_files", lambda p: ([], "unknown", 0.0))
        (tmp_path / "repo").mkdir()
        result = _adapter(_FakeRuntime(stdout="", stderr="")).execute(_request(tmp_path))
        assert result.artifacts == []

    def test_propagates_request_ids_and_branch(self, tmp_path, monkeypatch):
        monkeypatch.setattr(adapter_mod, "_discover_changed_files", lambda p: ([], "unknown", 0.0))
        (tmp_path / "repo").mkdir()
        req = _request(tmp_path, task_branch="auto/xyz")
        result = _adapter().execute(req)
        assert result.run_id == req.run_id
        assert result.proposal_id == "prop-1"
        assert result.decision_id == "dec-1"
        assert result.branch_name == "auto/xyz"
        assert result.branch_pushed is False
        assert result.validation.status == ValidationStatus.SKIPPED

    def test_runtime_invocation_ref_populated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(adapter_mod, "_discover_changed_files", lambda p: ([], "unknown", 0.0))
        (tmp_path / "repo").mkdir()
        result = _adapter(_FakeRuntime(stdout="x")).execute(_request(tmp_path))
        assert result.runtime_invocation_ref is not None
        assert result.runtime_invocation_ref.runtime_name == "direct_local"

    def test_changed_files_threaded_into_result(self, tmp_path, monkeypatch):
        refs = [ChangedFileRef(path="a.py", change_type="modified")]
        monkeypatch.setattr(
            adapter_mod, "_discover_changed_files", lambda p: (refs, "git_diff", 1.0)
        )
        (tmp_path / "repo").mkdir()
        result = _adapter().execute(_request(tmp_path))
        assert [f.path for f in result.changed_files] == ["a.py"]
        assert result.changed_files_source == "git_diff"
        assert result.changed_files_confidence == 1.0
        assert result.diff_stat_excerpt == "1 file changed"


# ---------------------------------------------------------------------------
# execute() capacity-exhaustion guard (G-V04)
# ---------------------------------------------------------------------------


class TestCapacityGuard:
    def test_capacity_exhaustion_flips_success_to_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(adapter_mod, "_discover_changed_files", lambda p: ([], "unknown", 0.0))
        (tmp_path / "repo").mkdir()
        rt = _FakeRuntime(status="succeeded", stdout="You're out of usage right now")
        result = _adapter(rt).execute(_request(tmp_path))
        assert result.success is False
        assert result.status == ExecutionStatus.FAILED
        assert result.failure_category == FailureReasonCategory.BACKEND_ERROR
        assert "capacity exhaustion detected" in result.failure_reason

    def test_no_capacity_match_keeps_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr(adapter_mod, "_discover_changed_files", lambda p: ([], "unknown", 0.0))
        (tmp_path / "repo").mkdir()
        rt = _FakeRuntime(status="succeeded", stdout="ordinary output, no limit here")
        result = _adapter(rt).execute(_request(tmp_path))
        assert result.success is True

    def test_capacity_guard_skipped_on_failure(self, tmp_path, monkeypatch):
        # When the run already failed, the capacity classifier must not run.
        called = {"hit": False}

        def _spy(_out):
            called["hit"] = True
            return "should-not-be-used"

        monkeypatch.setattr(adapter_mod, "classify_capacity_exhaustion", _spy)
        monkeypatch.setattr(adapter_mod, "_discover_changed_files", lambda p: ([], "unknown", 0.0))
        (tmp_path / "repo").mkdir()
        rt = _FakeRuntime(status="failed", stdout="boom")
        result = _adapter(rt).execute(_request(tmp_path))
        assert called["hit"] is False
        assert result.success is False


# ---------------------------------------------------------------------------
# execute() failure paths
# ---------------------------------------------------------------------------


class TestExecuteFailure:
    def test_failed_status_and_reason(self, tmp_path, monkeypatch):
        monkeypatch.setattr(adapter_mod, "_discover_changed_files", lambda p: ([], "unknown", 0.0))
        (tmp_path / "repo").mkdir()
        rt = _FakeRuntime(status="failed", stdout="aider error log")
        result = _adapter(rt).execute(_request(tmp_path))
        assert result.success is False
        assert result.status == ExecutionStatus.FAILED
        assert result.failure_category == FailureReasonCategory.BACKEND_ERROR
        assert result.failure_reason == "aider error log"

    def test_failure_reason_falls_back_when_no_output(self, tmp_path, monkeypatch):
        monkeypatch.setattr(adapter_mod, "_discover_changed_files", lambda p: ([], "unknown", 0.0))
        (tmp_path / "repo").mkdir()
        rt = _FakeRuntime(status="failed", stdout="", stderr="")
        result = _adapter(rt).execute(_request(tmp_path))
        assert result.failure_reason == "direct_local execution failed"

    def test_timeout_status(self, tmp_path, monkeypatch):
        monkeypatch.setattr(adapter_mod, "_discover_changed_files", lambda p: ([], "unknown", 0.0))
        (tmp_path / "repo").mkdir()
        rt = _FakeRuntime(status="timed_out")
        result = _adapter(rt).execute(_request(tmp_path))
        assert result.status == ExecutionStatus.TIMED_OUT
        assert result.failure_category == FailureReasonCategory.TIMEOUT
        assert "Timed out after" in result.failure_reason


# ---------------------------------------------------------------------------
# _build_invocation
# ---------------------------------------------------------------------------


class TestBuildInvocation:
    def test_basic_command_shape(self, tmp_path):
        adapter = _adapter()
        cmd, env = adapter._build_invocation(_repo_path=tmp_path, goal="do thing", constraints="")
        assert cmd[0] == "aider"
        assert cmd[1] == "--model"
        assert cmd[2] == "openai/capable"
        assert "--message" in cmd
        assert cmd[cmd.index("--message") + 1] == "do thing"
        assert "--yes" in cmd

    def test_constraints_appended_to_message(self, tmp_path):
        adapter = _adapter()
        cmd, _ = adapter._build_invocation(
            _repo_path=tmp_path, goal="goal", constraints="no deletes"
        )
        msg = cmd[cmd.index("--message") + 1]
        assert msg == "goal\n\n## Constraints\nno deletes"

    def test_model_settings_file_added_when_exists(self, tmp_path):
        msf = tmp_path / "settings.yaml"
        msf.write_text("x: 1")
        adapter = _adapter(model_settings_file=str(msf))
        cmd, _ = adapter._build_invocation(_repo_path=tmp_path, goal="g", constraints="")
        assert "--model-settings-file" in cmd
        assert cmd[cmd.index("--model-settings-file") + 1] == str(msf)

    def test_model_settings_file_skipped_when_missing(self, tmp_path):
        adapter = _adapter(model_settings_file=str(tmp_path / "nope.yaml"))
        cmd, _ = adapter._build_invocation(_repo_path=tmp_path, goal="g", constraints="")
        assert "--model-settings-file" not in cmd

    def test_model_settings_file_empty_string_skipped(self, tmp_path):
        adapter = _adapter(model_settings_file="")
        cmd, _ = adapter._build_invocation(_repo_path=tmp_path, goal="g", constraints="")
        assert "--model-settings-file" not in cmd

    def test_extra_args_appended(self, tmp_path):
        adapter = _adapter(extra_args=["--foo", "--bar"])
        cmd, _ = adapter._build_invocation(_repo_path=tmp_path, goal="g", constraints="")
        assert cmd[-2:] == ["--foo", "--bar"]

    def test_openai_key_injected_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        adapter = _adapter()
        _, env = adapter._build_invocation(_repo_path=tmp_path, goal="g", constraints="")
        assert env["OPENAI_API_KEY"] == "sk-local-direct"

    def test_openai_key_preserved_when_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
        adapter = _adapter()
        _, env = adapter._build_invocation(_repo_path=tmp_path, goal="g", constraints="")
        assert env["OPENAI_API_KEY"] == "sk-real"


# ---------------------------------------------------------------------------
# _run
# ---------------------------------------------------------------------------


class TestRun:
    def test_succeeded_reads_captures_and_combines(self, tmp_path):
        rt = _FakeRuntime(status="succeeded", stdout="OUT ", stderr="ERR")
        adapter = _adapter(rt)
        res = adapter._run(
            command=["aider", "--model", "openai/capable"], repo_path=tmp_path, env={}
        )
        assert res.success is True
        assert res.output == "OUT ERR"
        assert res.exit_code == 0
        assert res.metadata["model"] == "openai/capable"

    def test_invocation_fields_passed_through(self, tmp_path):
        rt = _FakeRuntime()
        adapter = _adapter(rt, timeout_seconds=42)
        adapter._run(command=["aider", "--model", "m"], repo_path=tmp_path, env={"K": "V"})
        inv = rt.last_invocation
        assert inv.runtime_name == "direct_local"
        assert inv.runtime_kind == "subprocess"
        assert inv.working_directory == str(tmp_path)
        assert inv.timeout_seconds == 42
        assert inv.environment == {"K": "V"}
        assert inv.invocation_id.startswith("direct-local-")

    def test_timeout_none_when_zero(self, tmp_path):
        rt = _FakeRuntime()
        adapter = _adapter(rt, timeout_seconds=0)
        adapter._run(command=["aider", "--model", "m"], repo_path=tmp_path, env={})
        assert rt.last_invocation.timeout_seconds is None

    def test_filenotfound_returns_binary_not_found(self, tmp_path):
        rt = _FakeRuntime(raise_exc=FileNotFoundError("missing"))
        adapter = _adapter(rt, binary="aider-x")
        res = adapter._run(command=["aider-x"], repo_path=tmp_path, env={})
        assert res.success is False
        assert "Binary not found: aider-x" in res.output
        assert res.invocation_ref is not None

    def test_rejected_status(self, tmp_path):
        rt = _FakeRuntime(status="rejected", error_summary="bad request")
        adapter = _adapter(rt)
        res = adapter._run(command=["aider"], repo_path=tmp_path, env={})
        assert res.success is False
        assert res.output == "bad request"

    def test_rejected_status_default_message(self, tmp_path):
        rt = _FakeRuntime(status="rejected", error_summary=None)
        adapter = _adapter(rt)
        res = adapter._run(command=["aider"], repo_path=tmp_path, env={})
        assert res.output == "executor runtime rejected invocation"

    def test_timed_out_status(self, tmp_path):
        rt = _FakeRuntime(status="timed_out")
        adapter = _adapter(rt, timeout_seconds=15)
        res = adapter._run(command=["aider"], repo_path=tmp_path, env={})
        assert res.success is False
        assert res.metadata["timeout_hit"] is True
        assert "Timed out after 15s" in res.output

    def test_failed_status_not_succeeded(self, tmp_path):
        rt = _FakeRuntime(status="failed", stdout="nope")
        adapter = _adapter(rt)
        res = adapter._run(command=["aider", "--model", "m"], repo_path=tmp_path, env={})
        assert res.success is False
        assert res.output == "nope"


# ---------------------------------------------------------------------------
# _DirectLocalRunResult
# ---------------------------------------------------------------------------


class TestRunResultModel:
    def test_defaults(self):
        r = _DirectLocalRunResult(success=True, output="hi")
        assert r.success is True
        assert r.output == "hi"
        assert r.exit_code is None
        assert r.metadata == {}
        assert r.invocation_ref is None

    def test_explicit_metadata(self):
        r = _DirectLocalRunResult(
            success=False, output="x", exit_code=3, metadata={"a": 1}, invocation_ref="ref"
        )
        assert r.exit_code == 3
        assert r.metadata == {"a": 1}
        assert r.invocation_ref == "ref"


# ---------------------------------------------------------------------------
# _read_capture
# ---------------------------------------------------------------------------


class TestReadCapture:
    def test_none_path(self):
        assert _read_capture(None) == ""

    def test_empty_string_path(self):
        assert _read_capture("") == ""

    def test_missing_file(self, tmp_path):
        assert _read_capture(str(tmp_path / "absent.txt")) == ""

    def test_reads_existing(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("content", encoding="utf-8")
        assert _read_capture(str(p)) == "content"

    def test_oserror_returns_empty(self, tmp_path, monkeypatch):
        p = tmp_path / "f.txt"
        p.write_text("x", encoding="utf-8")

        def _boom(*a, **k):
            raise OSError("read fail")

        monkeypatch.setattr(Path, "read_text", _boom)
        assert _read_capture(str(p)) == ""


# ---------------------------------------------------------------------------
# _short_id
# ---------------------------------------------------------------------------


class TestShortId:
    def test_prefix_and_length(self):
        sid = _short_id()
        assert sid.startswith("direct-local-")
        assert len(sid) == len("direct-local-") + 8

    def test_unique(self):
        assert _short_id() != _short_id()


# ---------------------------------------------------------------------------
# _failure_status
# ---------------------------------------------------------------------------


class TestFailureStatus:
    def test_timeout(self):
        r = _DirectLocalRunResult(success=False, output="", metadata={"timeout_hit": True})
        assert _failure_status(r) == ExecutionStatus.TIMED_OUT

    def test_generic_failed(self):
        r = _DirectLocalRunResult(success=False, output="", metadata={})
        assert _failure_status(r) == ExecutionStatus.FAILED


# ---------------------------------------------------------------------------
# _failure_category
# ---------------------------------------------------------------------------


class TestFailureCategory:
    def test_success_returns_none(self):
        r = _DirectLocalRunResult(success=True, output="")
        assert _failure_category(r) is None

    def test_timeout_category(self):
        r = _DirectLocalRunResult(success=False, output="", metadata={"timeout_hit": True})
        assert _failure_category(r) == FailureReasonCategory.TIMEOUT

    def test_capacity_category(self):
        r = _DirectLocalRunResult(success=False, output="", metadata={"capacity_exhausted": True})
        assert _failure_category(r) == FailureReasonCategory.BACKEND_ERROR

    def test_default_backend_error(self):
        r = _DirectLocalRunResult(success=False, output="", metadata={})
        assert _failure_category(r) == FailureReasonCategory.BACKEND_ERROR


# ---------------------------------------------------------------------------
# _discover_changed_files
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


class TestDiscoverChangedFiles:
    def test_subprocess_exception_returns_unknown(self, tmp_path, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("git missing")

        monkeypatch.setattr(adapter_mod.subprocess, "run", _boom)
        refs, source, conf = _discover_changed_files(tmp_path)
        assert refs == []
        assert source == "unknown"
        assert conf == 0.0

    def test_nonzero_returncode_returns_unknown(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            adapter_mod.subprocess, "run", lambda *a, **k: _FakeProc(returncode=128)
        )
        refs, source, conf = _discover_changed_files(tmp_path)
        assert refs == []
        assert source == "unknown"
        assert conf == 0.0

    def test_parses_changed_files(self, tmp_path, monkeypatch):
        out = "M\tsrc/a.py\nA\tsrc/b.py\nD\told.py\nR\trenamed.py"
        monkeypatch.setattr(adapter_mod.subprocess, "run", lambda *a, **k: _FakeProc(0, out))
        refs, source, conf = _discover_changed_files(tmp_path)
        assert source == "git_diff"
        assert conf == 1.0
        assert [(r.path, r.change_type) for r in refs] == [
            ("src/a.py", "modified"),
            ("src/b.py", "added"),
            ("old.py", "deleted"),
            ("renamed.py", "renamed"),
        ]

    def test_skips_malformed_lines(self, tmp_path, monkeypatch):
        out = "M\tgood.py\ngarbage_no_tab\n\n"
        monkeypatch.setattr(adapter_mod.subprocess, "run", lambda *a, **k: _FakeProc(0, out))
        refs, _, _ = _discover_changed_files(tmp_path)
        assert [r.path for r in refs] == ["good.py"]

    def test_empty_stdout(self, tmp_path, monkeypatch):
        monkeypatch.setattr(adapter_mod.subprocess, "run", lambda *a, **k: _FakeProc(0, ""))
        refs, source, conf = _discover_changed_files(tmp_path)
        assert refs == []
        assert source == "git_diff"


# ---------------------------------------------------------------------------
# _git_status_to_change_type
# ---------------------------------------------------------------------------


class TestGitStatusMapping:
    @pytest.mark.parametrize(
        "status,expected",
        [
            ("A", "added"),
            ("M", "modified"),
            ("D", "deleted"),
            ("R", "renamed"),
            ("a", "added"),
            ("R100", "renamed"),
            ("X", "modified"),
            ("", "modified"),
        ],
    )
    def test_mapping(self, status, expected):
        assert _git_status_to_change_type(status) == expected


# ---------------------------------------------------------------------------
# _diff_stat
# ---------------------------------------------------------------------------


class TestDiffStat:
    def test_empty(self):
        assert _diff_stat([]) is None

    def test_single(self):
        refs = [ChangedFileRef(path="a.py")]
        assert _diff_stat(refs) == "1 file changed"

    def test_plural(self):
        refs = [ChangedFileRef(path="a.py"), ChangedFileRef(path="b.py")]
        assert _diff_stat(refs) == "2 files changed"


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello", 100) == "hello"

    def test_exact_length_unchanged(self):
        text = "x" * 10
        assert _truncate(text, 10) == text

    def test_long_text_truncated(self):
        text = "a" * 50 + "b" * 50
        out = _truncate(text, 20)
        assert "...[truncated]..." in out
        assert out.startswith("a" * 10)
        assert out.endswith("b" * 10)
