# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
import os
from pathlib import Path

import pytest
import typer

from operations_center.entrypoints.setup import main as setup_main
from operations_center.entrypoints.setup.main import (
    EXECUTOR_BACKENDS,
    RepoSetupAnswers,
    SetupAnswers,
    check_command_installed,
    default_orchestrator_for_statuses,
    ensure_executor_backends_installed,
    github_https_to_ssh,
    infer_repo_key_from_clone_url,
    missing_executor_backends,
    parse_remote_branches,
    prepend_local_bin_to_path,
    provider_default_orchestrator,
    render_env_file,
    render_settings_yaml,
    render_task_template,
)
from operations_center.entrypoints.setup.providers import (
    ProviderStatus,
    summarize_provider_statuses,
)


def test_render_settings_yaml_contains_local_repo_bootstrap_defaults() -> None:
    answers = SetupAnswers(
        forgejo_base_url="http://forge.local",
        forgejo_owner="protocolwarden",
        forgejo_repo="board",
        forgejo_api_token_env="FORGEJO_API_TOKEN",
        forgejo_api_token_value="forge-secret",
        git_provider="github",
        git_token_env="GITHUB_TOKEN",
        git_token_value="gh-secret",
        git_author_name="Operations Center Bot",
        git_author_email="bot@example.com",
        git_sign_commits=True,
        git_signing_key="ABC12345",
        executor_install_ref=None,
        executor_team="budget",
        executor_cycles=3,
        executor_exchanges=20,
        executor_orchestrator="api",
        executor_effort="standard",
        preferred_smart_provider="claude",
        preferred_fast_provider="codex",
        allowed_providers=["claude", "codex"],
        headless_required=False,
        provider_versions={},
        repos=[
            RepoSetupAnswers(
                repo_key="operations-center",
                repo_clone_url="git@github.com:you/operations-center.git",
                repo_default_branch="main",
                repo_allowed_base_branches=["main", "develop"],
                repo_validation_commands=[".venv/bin/pytest -q", ".venv/bin/ruff check ."],
                repo_bootstrap_enabled=True,
                repo_python_binary="python3",
                repo_venv_dir=".venv",
                repo_install_dev_command=".venv/bin/pip install -e .[dev]",
            )
        ],
        default_repo_key="operations-center",
    )

    rendered = render_settings_yaml(answers)

    assert "api_token_env: FORGEJO_API_TOKEN" in rendered
    assert "board_backend: forgejo" in rendered
    assert "owner: protocolwarden" in rendered
    assert "token_env: GITHUB_TOKEN" in rendered
    assert "sign_commits: true" in rendered
    assert "signing_key: ABC12345" in rendered
    assert "bootstrap_enabled: true" in rendered
    assert "venv_dir: .venv" in rendered
    assert "install_dev_command: .venv/bin/pip install -e .[dev]" in rendered
    assert "- .venv/bin/pytest -q" in rendered
    assert "team_name: budget" in rendered
    assert "tier_name: budget" in rendered
    assert "dynamic_team_selection: false" in rendered
    assert "dynamic_tier_selection: false" in rendered
    assert "dynamic_worker_backend_selection: true" in rendered


def test_provider_default_orchestrator_prefers_cli_sessions() -> None:
    assert provider_default_orchestrator("claude") == "claude-code:opus"
    assert provider_default_orchestrator("codex") == "codex:gpt-5.4"


def test_default_orchestrator_for_statuses_uses_preferred_smart_provider() -> None:
    statuses = [
        ProviderStatus(
            key="claude",
            label="Claude Code",
            installed=True,
            version="2.1.88",
            auth_mode="browser_login",
            interactive_ready=True,
            headless_ready=False,
            authenticated=True,
            detail="ok",
        ),
        ProviderStatus(
            key="codex",
            label="OpenAI Codex CLI",
            installed=True,
            version="0.117.0",
            auth_mode="browser_login",
            interactive_ready=True,
            headless_ready=False,
            authenticated=True,
            detail="ok",
        ),
    ]
    assert (
        default_orchestrator_for_statuses(statuses, preferred_smart_provider="claude")
        == "claude-code:opus"
    )
    assert (
        default_orchestrator_for_statuses(statuses, preferred_smart_provider="codex")
        == "codex:gpt-5.4"
    )


def test_render_env_file_for_subscription_mode_skips_provider_secret_export() -> None:
    answers = SetupAnswers(
        forgejo_base_url="http://forge.local",
        forgejo_owner="protocolwarden",
        forgejo_repo="board",
        forgejo_api_token_env="FORGEJO_API_TOKEN",
        forgejo_api_token_value="forge-secret",
        git_provider="github",
        git_token_env="GITHUB_TOKEN",
        git_token_value="gh-secret",
        git_author_name="Operations Center Bot",
        git_author_email="bot@example.com",
        git_sign_commits=False,
        git_signing_key=None,
        executor_install_ref="v0.4.272",
        executor_team="full",
        executor_cycles=3,
        executor_exchanges=20,
        executor_orchestrator="api",
        executor_effort="standard",
        preferred_smart_provider="claude",
        preferred_fast_provider="codex",
        allowed_providers=["claude", "codex"],
        headless_required=False,
        provider_versions={"codex": "0.117.0"},
        repos=[
            RepoSetupAnswers(
                repo_key="operations-center",
                repo_clone_url="git@github.com:you/operations-center.git",
                repo_default_branch="main",
                repo_allowed_base_branches=["main"],
                repo_validation_commands=[".venv/bin/pytest -q"],
                repo_bootstrap_enabled=True,
                repo_python_binary="python3",
                repo_venv_dir=".venv",
                repo_install_dev_command=".venv/bin/pip install -e .[dev]",
            )
        ],
        default_repo_key="operations-center",
    )

    rendered = render_env_file(answers)

    assert "export FORGEJO_API_TOKEN='forge-secret'" in rendered
    # The board is a service the operator runs; setup no longer writes
    # start-command / browser / version exports for it.
    assert "PLANE" not in rendered
    assert "OPERATIONS_CENTER_PLANE_START_COMMAND" not in rendered
    assert "export OPERATIONS_CENTER_EXECUTOR_INSTALL_REF='v0.4.272'" in rendered
    assert "export GITHUB_TOKEN='gh-secret'" in rendered
    assert "export OPERATIONS_CENTER_PROVIDER_CODEX_VERSION='0.117.0'" in rendered
    assert "export OPERATIONS_CENTER_PROVIDER_PREFERRED_SMART='claude'" in rendered
    assert "export OPERATIONS_CENTER_PROVIDER_PREFERRED_FAST='codex'" in rendered
    assert "export OPERATIONS_CENTER_ALLOWED_PROVIDERS='claude,codex'" in rendered
    assert "export OPERATIONS_CENTER_PROVIDER_HEADLESS_REQUIRED=0" in rendered
    assert "export OPERATIONS_CENTER_DEFAULT_REPO='operations-center'" in rendered
    assert "OPENAI_API_KEY" not in rendered


def test_render_settings_yaml_supports_multiple_repos() -> None:
    answers = SetupAnswers(
        forgejo_base_url="http://forge.local",
        forgejo_owner="protocolwarden",
        forgejo_repo="board",
        forgejo_api_token_env="FORGEJO_API_TOKEN",
        forgejo_api_token_value="forge-secret",
        git_provider="github",
        git_token_env="GITHUB_TOKEN",
        git_token_value="gh-secret",
        git_author_name="Operations Center Bot",
        git_author_email="bot@example.com",
        git_sign_commits=False,
        git_signing_key=None,
        executor_install_ref=None,
        executor_team="budget",
        executor_cycles=3,
        executor_exchanges=20,
        executor_orchestrator="api",
        executor_effort="standard",
        preferred_smart_provider="claude",
        preferred_fast_provider="codex",
        allowed_providers=["claude", "codex"],
        headless_required=False,
        provider_versions={},
        repos=[
            RepoSetupAnswers(
                repo_key="operations-center",
                repo_clone_url="git@github.com:you/operations-center.git",
                repo_default_branch="main",
                repo_allowed_base_branches=["main"],
                repo_validation_commands=[".venv/bin/pytest -q"],
                repo_bootstrap_enabled=True,
                repo_python_binary="python3",
                repo_venv_dir=".venv",
                repo_install_dev_command=".venv/bin/pip install -e .[dev]",
            ),
            RepoSetupAnswers(
                repo_key="other-repo",
                repo_clone_url="git@github.com:you/other-repo.git",
                repo_default_branch="develop",
                repo_allowed_base_branches=["develop", "feature/*"],
                repo_validation_commands=[".venv/bin/pytest -q"],
                repo_bootstrap_enabled=False,
                repo_python_binary="python3",
                repo_venv_dir=".venv",
                repo_install_dev_command=".venv/bin/pip install -e .[dev]",
            ),
        ],
        default_repo_key="operations-center",
    )

    rendered = render_settings_yaml(answers)

    assert "operations-center:" in rendered
    assert "other-repo:" in rendered
    assert "bootstrap_enabled: false" in rendered
    assert "- feature/*" in rendered


def test_render_task_template_uses_default_repo() -> None:
    answers = SetupAnswers(
        forgejo_base_url="http://forge.local",
        forgejo_owner="protocolwarden",
        forgejo_repo="board",
        forgejo_api_token_env="FORGEJO_API_TOKEN",
        forgejo_api_token_value="forge-secret",
        git_provider="github",
        git_token_env="GITHUB_TOKEN",
        git_token_value="gh-secret",
        git_author_name="Operations Center Bot",
        git_author_email="bot@example.com",
        git_sign_commits=False,
        git_signing_key=None,
        executor_install_ref=None,
        executor_team="budget",
        executor_cycles=3,
        executor_exchanges=20,
        executor_orchestrator="api",
        executor_effort="standard",
        preferred_smart_provider="claude",
        preferred_fast_provider="codex",
        allowed_providers=["claude", "codex"],
        headless_required=False,
        provider_versions={},
        repos=[
            RepoSetupAnswers(
                repo_key="operations-center",
                repo_clone_url="git@github.com:you/operations-center.git",
                repo_default_branch="main",
                repo_allowed_base_branches=["main", "develop"],
                repo_validation_commands=[".venv/bin/pytest -q"],
                repo_bootstrap_enabled=True,
                repo_python_binary="python3",
                repo_venv_dir=".venv",
                repo_install_dev_command=".venv/bin/pip install -e .[dev]",
            )
        ],
        default_repo_key="operations-center",
    )

    rendered = render_task_template(answers)

    assert "repo: operations-center" in rendered
    assert "base_branch: main" in rendered
    assert "## Goal" in rendered


def test_github_https_to_ssh_converts_github_remote() -> None:
    assert (
        github_https_to_ssh("https://github.com/ProtocolWarden/OperationsCenter.git")
        == "git@github.com:ProtocolWarden/OperationsCenter.git"
    )


def test_github_https_to_ssh_ignores_non_github_remote() -> None:
    assert github_https_to_ssh("git@gitlab.com:group/repo.git") is None


def test_parse_remote_branches_extracts_head_names() -> None:
    output = "\n".join(
        [
            "abc123\trefs/heads/main",
            "def456\trefs/heads/develop",
            "ghi789\trefs/tags/v1.0.0",
        ]
    )
    assert parse_remote_branches(output) == ["develop", "main"]


def test_infer_repo_key_from_clone_url_prefers_repo_name() -> None:
    assert (
        infer_repo_key_from_clone_url("git@github.com:ProtocolWarden/OperationsCenter.git")
        == "OperationsCenter"
    )


def test_prepend_local_bin_to_path_adds_home_local_bin(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    prepend_local_bin_to_path()
    assert str((Path.home() / ".local" / "bin")) in os.environ["PATH"]


def test_check_command_installed_uses_local_bin_path(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(
        "shutil.which",
        lambda command: (
            "/home/dev/.local/bin/team-executor" if command == "team-executor" else None
        ),
    )
    assert check_command_installed("team-executor") is True


def test_summarize_provider_statuses_distinguishes_states() -> None:
    summary = summarize_provider_statuses(
        [
            ProviderStatus(
                key="claude",
                label="Claude Code",
                installed=True,
                version="1.0.0",
                auth_mode="browser_login",
                interactive_ready=True,
                headless_ready=False,
                authenticated=True,
                detail="ok",
            ),
            ProviderStatus(
                key="codex",
                label="OpenAI Codex CLI",
                installed=True,
                version="1.0.0",
                auth_mode="api_key",
                interactive_ready=True,
                headless_ready=True,
                authenticated=True,
                detail="ok",
            ),
            ProviderStatus(
                key="gemini",
                label="Gemini CLI",
                installed=False,
                version=None,
                auth_mode=None,
                interactive_ready=False,
                headless_ready=False,
                authenticated=False,
                detail="missing",
            ),
        ]
    )

    assert "Claude Code: installed + logged in (1.0.0)" in summary
    assert "OpenAI Codex CLI: installed + headless ready (1.0.0)" in summary
    assert "Gemini CLI: not installed" in summary


class _FakeProc:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


class _FakeRunner:
    """Stands in for ``subprocess.run`` during executor-backend checks.

    Import probes fail for every module in ``missing``; a successful editable
    install of a sibling checkout removes the matching module from that set, so
    the post-install re-probe sees the repaired state.
    """

    def __init__(self, missing: set[str], *, install_rc: int = 0, install_fixes: bool = True):
        self.missing = set(missing)
        self.install_rc = install_rc
        self.install_fixes = install_fixes
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):  # type: ignore[no-untyped-def]
        args = list(cmd)
        self.calls.append(args)
        if len(args) >= 3 and args[1] == "-c" and args[2].startswith("import "):
            module = args[2].removeprefix("import ")
            return _FakeProc(1 if module in self.missing else 0)
        if args[:3] == ["uv", "pip", "install"]:
            if self.install_rc == 0 and self.install_fixes:
                checkout = Path(args[-1]).name
                for module, checkout_name in EXECUTOR_BACKENDS:
                    if checkout_name == checkout:
                        self.missing.discard(module)
            return _FakeProc(self.install_rc)
        return _FakeProc(0)

    @property
    def install_targets(self) -> list[str]:
        return [Path(call[-1]).name for call in self.calls if call[:3] == ["uv", "pip", "install"]]


def _make_checkouts(tmp_path: Path, *names: str) -> Path:
    repo_root = tmp_path / "OperationsCenter"
    repo_root.mkdir()
    for name in names:
        checkout = tmp_path / name
        checkout.mkdir()
        (checkout / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    return repo_root


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: _FakeRunner) -> None:
    monkeypatch.setattr(setup_main.subprocess, "run", runner)
    # uv is a prerequisite of the install path, not of the probe — assume present.
    monkeypatch.setattr(setup_main, "check_command_installed", lambda command: True)


def test_missing_executor_backends_reports_unimportable_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _FakeRunner({"dag_executor"})
    _patch_runner(monkeypatch, runner)

    assert missing_executor_backends("/usr/bin/python3") == ["dag_executor"]
    assert runner.calls == [
        ["/usr/bin/python3", "-c", "import team_executor"],
        ["/usr/bin/python3", "-c", "import dag_executor"],
        ["/usr/bin/python3", "-c", "import critique_executor"],
    ]


def test_executor_backends_cover_the_three_adapters_oc_loads() -> None:
    assert [module for module, _ in EXECUTOR_BACKENDS] == [
        "team_executor",
        "dag_executor",
        "critique_executor",
    ]


def test_ensure_executor_backends_installed_is_a_noop_when_all_importable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _make_checkouts(tmp_path)
    runner = _FakeRunner(set())
    _patch_runner(monkeypatch, runner)

    ensure_executor_backends_installed(repo_root, python_binary="/usr/bin/python3")

    assert runner.install_targets == []


def test_ensure_executor_backends_installed_installs_missing_siblings_editable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _make_checkouts(tmp_path, "TeamExecutor", "CritiqueExecutor")
    runner = _FakeRunner({"team_executor", "critique_executor"})
    _patch_runner(monkeypatch, runner)

    ensure_executor_backends_installed(repo_root, python_binary="/usr/bin/python3")

    assert runner.install_targets == ["TeamExecutor", "CritiqueExecutor"]
    install_calls = [call for call in runner.calls if call[:3] == ["uv", "pip", "install"]]
    assert install_calls[0][:6] == [
        "uv",
        "pip",
        "install",
        "--python",
        "/usr/bin/python3",
        "-e",
    ]
    assert Path(install_calls[0][-1]) == (tmp_path / "TeamExecutor").resolve()


def test_ensure_executor_backends_installed_errors_without_a_sibling_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _make_checkouts(tmp_path)
    _patch_runner(monkeypatch, _FakeRunner({"dag_executor"}))

    with pytest.raises(typer.BadParameter) as excinfo:
        ensure_executor_backends_installed(repo_root, python_binary="/usr/bin/python3")

    assert "dag_executor" in str(excinfo.value)
    assert "DAGExecutor" in str(excinfo.value)


def test_ensure_executor_backends_installed_errors_when_install_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _make_checkouts(tmp_path, "TeamExecutor")
    _patch_runner(monkeypatch, _FakeRunner({"team_executor"}, install_rc=1))

    with pytest.raises(typer.BadParameter) as excinfo:
        ensure_executor_backends_installed(repo_root, python_binary="/usr/bin/python3")

    assert "editable install of TeamExecutor failed" in str(excinfo.value)


def test_ensure_executor_backends_installed_errors_when_still_unimportable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _make_checkouts(tmp_path, "TeamExecutor")
    _patch_runner(monkeypatch, _FakeRunner({"team_executor"}, install_fixes=False))

    with pytest.raises(typer.BadParameter) as excinfo:
        ensure_executor_backends_installed(repo_root, python_binary="/usr/bin/python3")

    assert "still not importable after install: team_executor" in str(excinfo.value)
