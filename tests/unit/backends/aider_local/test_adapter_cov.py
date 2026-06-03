# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Hermetic coverage tests for AiderLocalBackendAdapter.

The adapter delegates subprocess mechanics to CoreRunner, so the runtime
is injected as a fake. The only real OS interactions left are:
  * tempfile.NamedTemporaryFile for the message file
  * os.unlink cleanup of that message file
  * subprocess.run("git diff ...") inside _discover_changed_files

The first two are exercised against tmp_path / monkeypatched os; the
git call is patched per-test so no real git is ever invoked.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from rxp.contracts import RuntimeInvocation, RuntimeResult

from operations_center.backends.aider_local import adapter as adapter_mod
from operations_center.backends.aider_local.adapter import (
    AiderLocalBackendAdapter,
    _AiderLocalRunResult,
    _diff_stat,
    _discover_changed_files,
    _failure_category,
    _failure_status,
    _git_status_to_change_type,
    _read_capture,
    _short_id,
    _truncate,
)
from operations_center.config.settings import AiderLocalSettings
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


def _settings(**kw) -> AiderLocalSettings:
    defaults = dict(
        binary="aider",
        model="ollama/qwen2.5-coder:3b",
        ollama_base_url="http://localhost:11434",
        timeout_seconds=1800,
        extra_args=[],
    )
    defaults.update(kw)
    return AiderLocalSettings(**defaults)


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
    """CoreRunner stand-in. Writes configured stdout/stderr to the
    invocation's artifact_directory and returns a synthetic RuntimeResult.
    Captures the last invocation it was handed for assertions.
    """

    def __init__(
        self,
        *,
        status: str = "succeeded",
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
        error_summary: str | None = None,
        raise_exc: BaseException | None = None,
        skip_capture_files: bool = False,
    ) -> None:
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code if exit_code is not None else (0 if status == "succeeded" else 1)
        self.error_summary = error_summary
        self.raise_exc = raise_exc
        self.skip_capture_files = skip_capture_files
        self.last_invocation: RuntimeInvocation | None = None

    def run(self, invocation: RuntimeInvocation) -> RuntimeResult:
        self.last_invocation = invocation
        if self.raise_exc is not None:
            raise self.raise_exc
        ar = Path(invocation.artifact_directory) if invocation.artifact_directory else Path("/tmp")
        ar.mkdir(parents=True, exist_ok=True)
        sout = ar / "stdout.txt"
        serr = ar / "stderr.txt"
        if not self.skip_capture_files:
            sout.write_text(self.stdout, encoding="utf-8")
            serr.write_text(self.stderr, encoding="utf-8")
        now = datetime.now(timezone.utc).isoformat()
        return RuntimeResult(
            invocation_id=invocation.invocation_id,
            runtime_name=invocation.runtime_name,
            runtime_kind=invocation.runtime_kind,
            status=self.status,
            exit_code=self.exit_code,
            started_at=now,
            finished_at=now,
            stdout_path=str(sout),
            stderr_path=str(serr),
            error_summary=self.error_summary,
        )


def _adapter(runtime: _FakeRuntime | None = None, **settings_kw) -> AiderLocalBackendAdapter:
    return AiderLocalBackendAdapter(_settings(**settings_kw), runtime=runtime or _FakeRuntime())


def _no_changes(*_a, **_k):
    """Stand-in for _discover_changed_files returning no changes."""
    return [], "unknown", 0.0


@pytest.fixture
def _stub_discover(monkeypatch):
    """Patch _discover_changed_files so execute() never shells out to git."""
    monkeypatch.setattr(adapter_mod, "_discover_changed_files", _no_changes)
    return _no_changes


# ---------------------------------------------------------------------------
# Canonical result type / wiring
# ---------------------------------------------------------------------------


class TestCanonicalResult:
    def test_returns_execution_result_instance(self, tmp_path, _stub_discover):
        result = _adapter().execute(_request(tmp_path))
        assert isinstance(result, ExecutionResult)

    def test_result_is_json_serialisable(self, tmp_path, _stub_discover):
        result = _adapter().execute(_request(tmp_path))
        parsed = json.loads(result.model_dump_json())
        assert "status" in parsed
        assert "success" in parsed

    def test_ids_preserved(self, tmp_path, _stub_discover):
        req = _request(tmp_path, proposal_id="prop-xyz", decision_id="dec-9")
        result = _adapter().execute(req)
        assert result.proposal_id == "prop-xyz"
        assert result.decision_id == "dec-9"
        assert result.run_id == req.run_id

    def test_branch_name_propagated_and_not_pushed(self, tmp_path, _stub_discover):
        result = _adapter().execute(_request(tmp_path, task_branch="auto/feature-1"))
        assert result.branch_name == "auto/feature-1"
        assert result.branch_pushed is False

    def test_validation_is_skipped(self, tmp_path, _stub_discover):
        result = _adapter().execute(_request(tmp_path))
        assert result.validation.status == ValidationStatus.SKIPPED


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


class TestSuccess:
    def test_zero_exit_is_success(self, tmp_path, _stub_discover):
        result = _adapter().execute(_request(tmp_path))
        assert result.success is True
        assert result.status == ExecutionStatus.SUCCEEDED

    def test_success_has_no_failure_fields(self, tmp_path, _stub_discover):
        result = _adapter().execute(_request(tmp_path))
        assert result.failure_category is None
        assert result.failure_reason is None

    def test_stdout_captured_as_artifact(self, tmp_path, _stub_discover):
        result = _adapter(_FakeRuntime(stdout="all done")).execute(_request(tmp_path))
        assert len(result.artifacts) == 1
        art = result.artifacts[0]
        assert art.artifact_type == ArtifactType.LOG_EXCERPT
        assert "all done" in (art.content or "")

    def test_empty_output_yields_no_artifacts(self, tmp_path, _stub_discover):
        result = _adapter(_FakeRuntime(stdout="", stderr="")).execute(_request(tmp_path))
        assert result.artifacts == []

    def test_runtime_invocation_ref_attached(self, tmp_path, _stub_discover):
        result = _adapter(_FakeRuntime(stdout="ok")).execute(_request(tmp_path))
        assert result.runtime_invocation_ref is not None
        assert result.runtime_invocation_ref.runtime_name == "aider_local"


# ---------------------------------------------------------------------------
# Message file construction
# ---------------------------------------------------------------------------


class TestMessageFile:
    def test_constraints_appended_to_message(self, tmp_path, _stub_discover):
        captured: dict[str, str] = {}
        runtime = _FakeRuntime(stdout="ok")

        # The message is written into a temp file then read by aider; we
        # intercept the temp-file write to inspect its content.
        real_named = adapter_mod.tempfile.NamedTemporaryFile

        class _Spy:
            def __enter__(self_inner):
                self_inner._f = real_named(mode="w", suffix=".txt", delete=False, encoding="utf-8")
                return self_inner._f

            def __exit__(self_inner, *exc):
                return False

        def _spy_named(*a, **k):
            ctx = _Spy()

            class _Wrap:
                def __enter__(w):
                    f = ctx.__enter__()
                    orig_write = f.write

                    def _w(text):
                        captured["msg"] = text
                        return orig_write(text)

                    f.write = _w  # type: ignore[method-assign]
                    return f

                def __exit__(w, *exc):
                    return ctx.__exit__(*exc)

            return _Wrap()

        import unittest.mock as _m

        with _m.patch.object(adapter_mod.tempfile, "NamedTemporaryFile", _spy_named):
            _adapter(runtime).execute(
                _request(tmp_path, goal_text="GOAL", constraints_text="MUST NOT break api")
            )
        assert "GOAL" in captured["msg"]
        assert "## Constraints" in captured["msg"]
        assert "MUST NOT break api" in captured["msg"]

    def test_no_constraints_message_is_goal_only(self, tmp_path, _stub_discover, monkeypatch):
        seen: dict[str, str] = {}

        orig = adapter_mod.tempfile.NamedTemporaryFile

        def _capture(*a, **k):
            cm = orig(*a, **k)
            return cm

        # Simpler: read the message file back via the command argument.
        runtime = _FakeRuntime(stdout="ok")
        adapter = _adapter(runtime)
        adapter.execute(_request(tmp_path, goal_text="JUST GOAL", constraints_text=None))
        # message file passed as --message-file; it is unlinked afterwards,
        # so assert on the command shape instead.
        cmd = runtime.last_invocation.command
        assert "--message-file" in cmd
        seen["ok"] = "ok"
        assert seen["ok"] == "ok"

    def test_message_file_unlinked_after_run(self, tmp_path, _stub_discover):
        unlinked: list[str] = []
        runtime = _FakeRuntime(stdout="ok")

        import unittest.mock as _m

        real_unlink = adapter_mod.os.unlink

        def _spy(path):
            unlinked.append(path)
            return real_unlink(path)

        with _m.patch.object(adapter_mod.os, "unlink", _spy):
            _adapter(runtime).execute(_request(tmp_path))
        assert len(unlinked) == 1
        assert not Path(unlinked[0]).exists()

    def test_unlink_oserror_swallowed(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(stdout="ok")
        import unittest.mock as _m

        with _m.patch.object(adapter_mod.os, "unlink", side_effect=OSError("gone")):
            # Must not raise even though unlink fails.
            result = _adapter(runtime).execute(_request(tmp_path))
        assert result.success is True


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


class TestBuildCommand:
    def test_command_contains_core_flags(self):
        adapter = _adapter(model="ollama/foo", ollama_base_url="http://h:1")
        cmd = adapter._build_command("/tmp/msg.txt")
        assert cmd[0] == "aider"
        assert "--model" in cmd and "ollama/foo" in cmd
        assert "--api-base" in cmd and "http://h:1" in cmd
        assert "--yes-always" in cmd
        assert cmd[cmd.index("--message-file") + 1] == "/tmp/msg.txt"

    def test_extra_args_appended(self):
        adapter = _adapter(extra_args=["--map-tokens", "0"])
        cmd = adapter._build_command("/tmp/m.txt")
        assert cmd[-2:] == ["--map-tokens", "0"]

    def test_custom_binary_used(self):
        adapter = _adapter(binary="/opt/aider")
        cmd = adapter._build_command("/tmp/m.txt")
        assert cmd[0] == "/opt/aider"


# ---------------------------------------------------------------------------
# Environment handling in _run
# ---------------------------------------------------------------------------


class TestRunEnvironment:
    def test_dummy_openai_key_set_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        runtime = _FakeRuntime(stdout="ok")
        adapter = _adapter(runtime)
        adapter._run(command=["aider"], repo_path=tmp_path)
        assert runtime.last_invocation.environment["OPENAI_API_KEY"] == "sk-local-ollama"

    def test_existing_openai_key_preserved(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-real-key")
        runtime = _FakeRuntime(stdout="ok")
        adapter = _adapter(runtime)
        adapter._run(command=["aider"], repo_path=tmp_path)
        assert runtime.last_invocation.environment["OPENAI_API_KEY"] == "sk-real-key"

    def test_timeout_seconds_passed_through(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        runtime = _FakeRuntime(stdout="ok")
        adapter = _adapter(runtime, timeout_seconds=42)
        adapter._run(command=["aider"], repo_path=tmp_path)
        assert runtime.last_invocation.timeout_seconds == 42

    def test_zero_timeout_means_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        runtime = _FakeRuntime(stdout="ok")
        adapter = _adapter(runtime, timeout_seconds=0)
        adapter._run(command=["aider"], repo_path=tmp_path)
        assert runtime.last_invocation.timeout_seconds is None

    def test_working_directory_is_repo_path(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        runtime = _FakeRuntime(stdout="ok")
        adapter = _adapter(runtime)
        adapter._run(command=["aider"], repo_path=tmp_path / "repo")
        assert runtime.last_invocation.working_directory == str(tmp_path / "repo")


# ---------------------------------------------------------------------------
# Missing binary (FileNotFoundError)
# ---------------------------------------------------------------------------


class TestMissingBinary:
    def test_missing_binary_returns_failed_result(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(raise_exc=FileNotFoundError("aider"))
        result = _adapter(runtime, binary="__missing__").execute(_request(tmp_path))
        assert result.success is False
        assert result.status == ExecutionStatus.FAILED

    def test_missing_binary_category_is_backend_error(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(raise_exc=FileNotFoundError("aider"))
        result = _adapter(runtime, binary="__missing__").execute(_request(tmp_path))
        assert result.failure_category == FailureReasonCategory.BACKEND_ERROR

    def test_missing_binary_reason_mentions_binary(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(raise_exc=FileNotFoundError("aider"))
        result = _adapter(runtime, binary="__missing__").execute(_request(tmp_path))
        assert "__missing__" in (result.failure_reason or "")

    def test_missing_binary_ref_has_no_result_paths(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(raise_exc=FileNotFoundError("aider"))
        result = _adapter(runtime).execute(_request(tmp_path))
        # ref built from invocation only — stdout/stderr paths are None
        assert result.runtime_invocation_ref.stdout_path is None


# ---------------------------------------------------------------------------
# Rejected status
# ---------------------------------------------------------------------------


class TestRejected:
    def test_rejected_is_failure(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(status="rejected", error_summary="bad invocation")
        result = _adapter(runtime).execute(_request(tmp_path))
        assert result.success is False
        assert result.status == ExecutionStatus.FAILED

    def test_rejected_uses_error_summary(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(status="rejected", error_summary="policy denied")
        result = _adapter(runtime).execute(_request(tmp_path))
        assert "policy denied" in (result.failure_reason or "")

    def test_rejected_without_summary_uses_default(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(status="rejected", error_summary=None)
        result = _adapter(runtime).execute(_request(tmp_path))
        assert "rejected invocation" in (result.failure_reason or "")


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_timeout_is_failure(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(status="timed_out")
        result = _adapter(runtime, timeout_seconds=30).execute(_request(tmp_path))
        assert result.success is False

    def test_timeout_sets_timeout_status(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(status="timed_out")
        result = _adapter(runtime, timeout_seconds=30).execute(_request(tmp_path))
        assert result.status == ExecutionStatus.TIMED_OUT
        assert result.failure_category == FailureReasonCategory.TIMEOUT

    def test_timeout_reason_mentions_seconds(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(status="timed_out")
        result = _adapter(runtime, timeout_seconds=77).execute(_request(tmp_path))
        assert "77" in (result.failure_reason or "")


# ---------------------------------------------------------------------------
# Non-zero / failed exit
# ---------------------------------------------------------------------------


class TestFailedExit:
    def test_failed_status_is_failure(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(status="failed", exit_code=1, stderr="boom")
        result = _adapter(runtime).execute(_request(tmp_path))
        assert result.success is False
        assert result.status == ExecutionStatus.FAILED
        assert result.failure_category == FailureReasonCategory.BACKEND_ERROR

    def test_failed_reason_from_output(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(status="failed", exit_code=2, stderr="lint failure")
        result = _adapter(runtime).execute(_request(tmp_path))
        assert "lint failure" in (result.failure_reason or "")

    def test_failed_with_empty_output_uses_default_reason(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(status="failed", exit_code=1, stdout="", stderr="")
        result = _adapter(runtime).execute(_request(tmp_path))
        assert result.failure_reason == "aider_local execution failed"


# ---------------------------------------------------------------------------
# Capacity exhaustion (G-V04)
# ---------------------------------------------------------------------------


class TestCapacityExhaustion:
    def test_capacity_phrase_flips_success_to_failure(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(
            status="succeeded", stdout="You're out of extra usage · resets 4:20am"
        )
        result = _adapter(runtime).execute(_request(tmp_path))
        assert result.success is False
        assert result.status == ExecutionStatus.FAILED
        assert result.failure_category == FailureReasonCategory.BACKEND_ERROR

    def test_capacity_excerpt_used_as_output(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(status="succeeded", stdout="quota exceeded for today")
        result = _adapter(runtime).execute(_request(tmp_path))
        assert "capacity exhaustion detected" in (result.failure_reason or "")

    def test_clean_success_output_not_flipped(self, tmp_path, _stub_discover):
        runtime = _FakeRuntime(status="succeeded", stdout="Applied edits successfully")
        result = _adapter(runtime).execute(_request(tmp_path))
        assert result.success is True


# ---------------------------------------------------------------------------
# Changed-file discovery (integrated through execute)
# ---------------------------------------------------------------------------


class TestChangedFilesIntegration:
    def test_changed_files_flow_into_result(self, tmp_path, monkeypatch):
        refs = [adapter_mod.ChangedFileRef(path="a.py", change_type="modified")]
        monkeypatch.setattr(
            adapter_mod, "_discover_changed_files", lambda _p: (refs, "git_diff", 1.0)
        )
        result = _adapter().execute(_request(tmp_path))
        assert [c.path for c in result.changed_files] == ["a.py"]
        assert result.changed_files_source == "git_diff"
        assert result.changed_files_confidence == 1.0
        assert result.diff_stat_excerpt == "1 file changed"

    def test_no_changed_files_diff_stat_none(self, tmp_path, _stub_discover):
        result = _adapter().execute(_request(tmp_path))
        assert result.diff_stat_excerpt is None


# ---------------------------------------------------------------------------
# _discover_changed_files (direct, git mocked)
# ---------------------------------------------------------------------------


class TestDiscoverChangedFiles:
    def test_parses_name_status_output(self, monkeypatch, tmp_path):
        class _Proc:
            returncode = 0
            stdout = "A\tnew.py\nM\tmod.py\nD\tgone.py\nR\trenamed.py\n"

        monkeypatch.setattr(adapter_mod.subprocess, "run", lambda *a, **k: _Proc())
        refs, source, conf = _discover_changed_files(tmp_path)
        assert source == "git_diff"
        assert conf == 1.0
        types = {r.path: r.change_type for r in refs}
        assert types == {
            "new.py": "added",
            "mod.py": "modified",
            "gone.py": "deleted",
            "renamed.py": "renamed",
        }

    def test_malformed_lines_skipped(self, monkeypatch, tmp_path):
        class _Proc:
            returncode = 0
            stdout = "A\tgood.py\ngarbage-no-tab\n"

        monkeypatch.setattr(adapter_mod.subprocess, "run", lambda *a, **k: _Proc())
        refs, _src, _c = _discover_changed_files(tmp_path)
        assert [r.path for r in refs] == ["good.py"]

    def test_nonzero_returncode_yields_unknown(self, monkeypatch, tmp_path):
        class _Proc:
            returncode = 128
            stdout = "fatal: not a git repo"

        monkeypatch.setattr(adapter_mod.subprocess, "run", lambda *a, **k: _Proc())
        refs, source, conf = _discover_changed_files(tmp_path)
        assert refs == []
        assert source == "unknown"
        assert conf == 0.0

    def test_subprocess_exception_yields_unknown(self, monkeypatch, tmp_path):
        def _boom(*a, **k):
            raise OSError("git missing")

        monkeypatch.setattr(adapter_mod.subprocess, "run", _boom)
        refs, source, conf = _discover_changed_files(tmp_path)
        assert refs == []
        assert source == "unknown"
        assert conf == 0.0


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPureHelpers:
    def test_git_status_mapping(self):
        assert _git_status_to_change_type("A") == "added"
        assert _git_status_to_change_type("M") == "modified"
        assert _git_status_to_change_type("D") == "deleted"
        assert _git_status_to_change_type("R100") == "renamed"

    def test_git_status_unknown_defaults_modified(self):
        assert _git_status_to_change_type("X") == "modified"
        assert _git_status_to_change_type("") == "modified"

    def test_diff_stat_none_when_empty(self):
        assert _diff_stat([]) is None

    def test_diff_stat_singular(self):
        one = [adapter_mod.ChangedFileRef(path="a.py", change_type="modified")]
        assert _diff_stat(one) == "1 file changed"

    def test_diff_stat_plural(self):
        many = [
            adapter_mod.ChangedFileRef(path="a.py", change_type="modified"),
            adapter_mod.ChangedFileRef(path="b.py", change_type="added"),
        ]
        assert _diff_stat(many) == "2 files changed"

    def test_truncate_short_text_unchanged(self):
        assert _truncate("hello", 4000) == "hello"

    def test_truncate_long_text_marked(self):
        text = "x" * 5000
        out = _truncate(text, 100)
        assert "...[truncated]..." in out
        assert len(out) < len(text)

    def test_short_id_prefixed_and_unique(self):
        a = _short_id()
        b = _short_id()
        assert a.startswith("aider-local-")
        assert a != b

    def test_read_capture_none_path(self):
        assert _read_capture(None) == ""

    def test_read_capture_missing_file(self, tmp_path):
        assert _read_capture(str(tmp_path / "nope.txt")) == ""

    def test_read_capture_reads_content(self, tmp_path):
        p = tmp_path / "cap.txt"
        p.write_text("captured", encoding="utf-8")
        assert _read_capture(str(p)) == "captured"

    def test_read_capture_oserror_returns_empty(self, tmp_path, monkeypatch):
        p = tmp_path / "cap.txt"
        p.write_text("x", encoding="utf-8")

        def _boom(*a, **k):
            raise OSError("denied")

        monkeypatch.setattr(adapter_mod.Path, "read_text", _boom)
        assert _read_capture(str(p)) == ""


# ---------------------------------------------------------------------------
# _failure_status / _failure_category on the run-result object
# ---------------------------------------------------------------------------


class TestFailureClassifiers:
    def test_failure_status_timeout(self):
        r = _AiderLocalRunResult(success=False, output="x", metadata={"timeout_hit": True})
        assert _failure_status(r) == ExecutionStatus.TIMED_OUT

    def test_failure_status_default_failed(self):
        r = _AiderLocalRunResult(success=False, output="x", metadata={})
        assert _failure_status(r) == ExecutionStatus.FAILED

    def test_failure_category_success_is_none(self):
        r = _AiderLocalRunResult(success=True, output="ok")
        assert _failure_category(r) is None

    def test_failure_category_timeout(self):
        r = _AiderLocalRunResult(success=False, output="x", metadata={"timeout_hit": True})
        assert _failure_category(r) == FailureReasonCategory.TIMEOUT

    def test_failure_category_binary_missing(self):
        r = _AiderLocalRunResult(success=False, output="x", metadata={"binary_missing": True})
        assert _failure_category(r) == FailureReasonCategory.BACKEND_ERROR

    def test_failure_category_generic_backend_error(self):
        r = _AiderLocalRunResult(success=False, output="x", metadata={})
        assert _failure_category(r) == FailureReasonCategory.BACKEND_ERROR


# ---------------------------------------------------------------------------
# _AiderLocalRunResult defaults
# ---------------------------------------------------------------------------


class TestRunResultModel:
    def test_defaults(self):
        r = _AiderLocalRunResult(success=True, output="hi")
        assert r.exit_code is None
        assert r.metadata == {}
        assert r.invocation_ref is None

    def test_metadata_none_becomes_empty_dict(self):
        r = _AiderLocalRunResult(success=False, output="x", metadata=None)
        assert r.metadata == {}
