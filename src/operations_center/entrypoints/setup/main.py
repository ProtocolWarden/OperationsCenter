# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from operations_center.entrypoints.setup.providers import (
    PROVIDER_SPECS,
    ProviderStatus,
    choose_preferred_provider,
    detect_all_provider_statuses,
    install_provider,
    run_interactive_provider_login,
    summarize_provider_statuses,
    write_provider_summary,
)

app = typer.Typer(help="Interactive local setup for Operations Center.")
console = Console()

DEFAULT_FORGEJO_URL = "http://localhost:3000"
DEFAULT_FORGEJO_BOARD_REPO = "board"
DEFAULT_SSH_KEY_PATH = Path.home() / ".ssh" / "id_ed25519"
SSH_KEY_CANDIDATES = [
    Path.home() / ".ssh" / "id_ed25519",
    Path.home() / ".ssh" / "id_rsa",
]

# The execute backends OC loads, as (import name, sibling checkout dir).
# OC consumes all three as LIBRARIES — backends/<name>/adapter.py does a plain
# `import <module>` — so importability in OC's interpreter is the only readiness
# signal that means anything. PATH is not: TeamExecutor and CritiqueExecutor
# declare no `[project.scripts]` at all, and the one console script that does
# exist (DAGExecutor's `dag-executor`) is never invoked by OC.
# They are sibling CHECKOUTS, not declared OC dependencies, so `uv pip install
# -e .[dev]` never installs them and a `uv sync` / venv-recreate actively drops
# them. Mirrors ensure_executor_backends() in scripts/operations-center.sh,
# which self-heals the same drop at every fleet launch.
EXECUTOR_BACKENDS: tuple[tuple[str, str], ...] = (
    ("team_executor", "TeamExecutor"),
    ("dag_executor", "DAGExecutor"),
    ("critique_executor", "CritiqueExecutor"),
)


@dataclass
class RepoSetupAnswers:
    repo_key: str
    repo_clone_url: str
    repo_default_branch: str
    repo_allowed_base_branches: list[str]
    repo_validation_commands: list[str]
    repo_bootstrap_enabled: bool
    repo_python_binary: str
    repo_venv_dir: str
    repo_install_dev_command: str | None


@dataclass
class RepoDiscoveryChoice:
    label: str
    repo_key: str
    clone_url: str
    default_branch: str


@dataclass
class SetupAnswers:
    forgejo_base_url: str
    forgejo_owner: str
    forgejo_repo: str
    forgejo_api_token_env: str
    forgejo_api_token_value: str
    git_provider: str
    git_token_env: str
    git_token_value: str
    git_author_name: str
    git_author_email: str
    git_sign_commits: bool
    git_signing_key: str | None
    # Version pin recorded for drift reporting only (dependency-check compares it
    # against the installed backend and the upstream latest release). Nothing
    # installs from it — the backends come from sibling checkouts.
    executor_install_ref: str | None
    executor_team: str
    executor_cycles: int
    executor_exchanges: int
    executor_orchestrator: str
    executor_effort: str
    preferred_smart_provider: str | None
    preferred_fast_provider: str | None
    allowed_providers: list[str]
    headless_required: bool
    provider_versions: dict[str, str]
    repos: list[RepoSetupAnswers]
    default_repo_key: str


def split_multiline(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def print_section(title: str, detail: str | None = None) -> None:
    typer.echo("")
    body = detail or ""
    console.print(
        Panel.fit(
            body,
            title=f"[bold cyan]{title}[/bold cyan]",
            border_style="cyan",
            padding=(0, 2),
        )
    )


def print_banner() -> None:
    width = max(60, min(shutil.get_terminal_size((80, 20)).columns, 100))
    rule = "=" * width
    title = "Operations Center Setup"
    subtitle = "Forgejo + executor local operator bootstrap"
    console.print(f"[blue]{rule}[/blue]")
    console.print(f"[bold green]{title.center(width)}[/bold green]")
    console.print(f"[bright_black]{subtitle.center(width)}[/bright_black]")
    console.print(f"[blue]{rule}[/blue]")


def prompt_with_default(
    label: str, default: str, *, note: str | None = None, hide_input: bool = False
) -> str:
    if note:
        console.print(f"[bright_black]{note}[/bright_black]")
    return typer.prompt(label, default=default, hide_input=hide_input)


def prepend_local_bin_to_path() -> None:
    local_bin = str(Path.home() / ".local" / "bin")
    current_path = os.environ.get("PATH", "")
    parts = current_path.split(":") if current_path else []
    if local_bin not in parts:
        os.environ["PATH"] = f"{local_bin}:{current_path}" if current_path else local_bin


def check_command_installed(command: str) -> bool:
    prepend_local_bin_to_path()
    return shutil.which(command) is not None


def provider_default_orchestrator(provider_key: str) -> str:
    mapping = {
        "claude": "claude-code:opus",
        "codex": "codex:gpt-5.4",
        "gemini": "gemini-cli:gemini-3-flash",
        "cursor": "cursor:sonnet-5",
    }
    return mapping.get(provider_key, "codex:gpt-5.4")


def default_orchestrator_for_statuses(
    statuses: list[ProviderStatus],
    *,
    preferred_smart_provider: str | None = None,
    saved_value: str | None = None,
) -> str:
    if saved_value:
        return saved_value
    usable = {status.key for status in statuses if status.interactive_ready}
    if preferred_smart_provider and preferred_smart_provider in usable:
        return provider_default_orchestrator(preferred_smart_provider)
    for candidate in ("claude", "codex", "gemini", "cursor"):
        if candidate in usable:
            return provider_default_orchestrator(candidate)
    return "codex:gpt-5.4"


def ensure_uv_installed() -> None:
    if check_command_installed("uv"):
        return
    typer.echo("[executor] uv not found -> installing...")
    proc = subprocess.run(
        "curl -LsSf https://astral.sh/uv/install.sh | sh",
        shell=True,
        check=False,
        env=os.environ.copy(),
    )
    prepend_local_bin_to_path()
    if proc.returncode != 0 or not check_command_installed("uv"):
        raise typer.BadParameter("[executor] ERROR: uv installation failed")


def missing_executor_backends(python_binary: str | None = None) -> list[str]:
    """Return the import names of EXECUTOR_BACKENDS ``python_binary`` cannot import.

    Probed in a subprocess rather than via importlib in this process: an install
    that lands partway through setup must be visible to the re-check, and the
    parent's import caches (and any already-bound ``sys.modules`` entry) would
    still answer for the pre-install state.
    """
    interpreter = python_binary or sys.executable
    missing: list[str] = []
    for module, _checkout in EXECUTOR_BACKENDS:
        proc = subprocess.run(
            [interpreter, "-c", f"import {module}"],
            check=False,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        if proc.returncode != 0:
            missing.append(module)
    return missing


def ensure_executor_backends_installed(repo_root: Path, python_binary: str | None = None) -> None:
    """Make every execute backend importable, installing sibling checkouts if not.

    Idempotent: the import probe is ~free and the editable install only fires for
    backends that are actually missing.
    """
    interpreter = python_binary or sys.executable
    typer.echo("[executor] Checking backend imports...")
    missing = missing_executor_backends(interpreter)
    if not missing:
        typer.echo("[executor] all backends importable")
        return

    checkout_dirs = dict(EXECUTOR_BACKENDS)
    ensure_uv_installed()
    for module in missing:
        checkout_name = checkout_dirs[module]
        checkout = (repo_root.parent / checkout_name).resolve()
        if not (checkout / "pyproject.toml").is_file():
            raise typer.BadParameter(
                f"[executor] ERROR: {module} is not importable and no sibling checkout was "
                f"found at {checkout}. Clone {checkout_name} next to this repo and rerun setup."
            )
        typer.echo(f"[executor] {module} missing -> installing {checkout} (editable)")
        proc = subprocess.run(
            ["uv", "pip", "install", "--python", interpreter, "-e", str(checkout)],
            check=False,
            env=os.environ.copy(),
        )
        if proc.returncode != 0:
            raise typer.BadParameter(
                f"[executor] ERROR: editable install of {checkout_name} failed"
            )

    still_missing = missing_executor_backends(interpreter)
    if still_missing:
        raise typer.BadParameter(
            "[executor] ERROR: backends still not importable after install: "
            + ", ".join(still_missing)
        )
    typer.echo("[executor] backends installed successfully")


def verify_forgejo_configuration(
    base_url: str,
    api_token: str,
    owner: str,
    repo: str,
) -> bool:
    """Check the operator's just-typed credentials against the live instance.

    Constructs `ForgejoClient` directly rather than going through
    `make_board_client`: the whole point of this step is to validate values the
    operator has entered but not yet written to a config file, so there is no
    `Settings` object for the factory to build from. That is why this module is
    the board seam's one allowlisted direct-construction site.
    """
    from operations_center.adapters.forgejo import ForgejoClient

    client = ForgejoClient(
        base_url=base_url,
        api_token=api_token,
        owner=owner,
        repo=repo,
    )
    try:
        project = client.fetch_project()
    except Exception as exc:
        console.print(f"[red]Forgejo API verification failed:[/red] {exc}")
        console.print(
            "[bright_black]Check that the board repo exists and the token has "
            "read/write access to its issues. The board repo is created by you "
            "(or by setup) on the Forgejo instance; it is not created "
            "automatically here.[/bright_black]"
        )
        return False
    finally:
        client.close()

    full_name = str(project.get("full_name") or f"{owner}/{repo}")
    console.print(
        f"[green]Forgejo API verified.[/green] Board repo [cyan]{full_name}[/cyan]."
    )
    return True


def prompt_choice(label: str, options: list[tuple[str, str]], default_index: int = 1) -> str:
    table = Table(show_header=True, header_style="bold magenta", box=None, pad_edge=False)
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("Option", style="white")
    for index, (_, description) in enumerate(options, start=1):
        table.add_row(str(index), description)
    console.print(table)
    while True:
        raw = typer.prompt(label, default=str(default_index))
        try:
            selected = int(raw)
        except ValueError:
            console.print("[red]Enter one of the numbered options.[/red]")
            continue
        if 1 <= selected <= len(options):
            return options[selected - 1][0]
        console.print("[red]Enter one of the numbered options.[/red]")


def parse_github_remote(url: str) -> tuple[str, str] | None:
    if url.startswith("git@github.com:"):
        path = url.removeprefix("git@github.com:")
    elif url.startswith("https://github.com/"):
        path = url.removeprefix("https://github.com/")
    else:
        return None
    if path.endswith(".git"):
        path = path[:-4]
    if "/" not in path:
        return None
    owner, repo = path.split("/", 1)
    return owner, repo


def infer_repo_key_from_clone_url(clone_url: str) -> str:
    parsed = parse_github_remote(clone_url)
    if parsed:
        return parsed[1]
    tail = clone_url.rstrip("/").rsplit("/", 1)[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def discover_repo_choices(
    existing_config: dict[str, object], repo_root: Path
) -> list[RepoDiscoveryChoice]:
    choices: list[RepoDiscoveryChoice] = []
    seen: set[str] = set()

    current_remote = get_origin_remote_url(repo_root)
    if current_remote:
        parsed = parse_github_remote(current_remote)
        if parsed:
            owner, repo = parsed
            clone_url = f"git@github.com:{owner}/{repo}.git"
            key = repo
            choices.append(
                RepoDiscoveryChoice(
                    label=f"Current checkout: {owner}/{repo}",
                    repo_key=key,
                    clone_url=clone_url,
                    default_branch="main",
                )
            )
            seen.add(clone_url)

    existing_repos = existing_config.get("repos", {})
    if isinstance(existing_repos, dict):
        for repo_key, raw in existing_repos.items():
            if not isinstance(raw, dict):
                continue
            raw_d = cast(dict[str, object], raw)
            clone_url = str(raw_d.get("clone_url") or "").strip()
            if not clone_url or clone_url in seen:
                continue
            default_branch = str(raw_d.get("default_branch") or "main")
            choices.append(
                RepoDiscoveryChoice(
                    label=f"Saved config: {repo_key}",
                    repo_key=str(repo_key),
                    clone_url=clone_url,
                    default_branch=default_branch,
                )
            )
            seen.add(clone_url)

    return choices


def parse_remote_branches(output: str) -> list[str]:
    branches: list[str] = []
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        ref = parts[1]
        prefix = "refs/heads/"
        if ref.startswith(prefix):
            branches.append(ref[len(prefix) :])
    return sorted(set(branches))


def discover_remote_branches(clone_url: str) -> list[str]:
    proc = subprocess.run(
        ["git", "ls-remote", "--heads", clone_url],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    return parse_remote_branches(proc.stdout)


def prompt_branch_selection(clone_url: str, default_branch: str) -> tuple[str, list[str]]:
    branches = discover_remote_branches(clone_url)
    if not branches:
        branch = typer.prompt("Repo default branch", default=default_branch)
        return branch, [branch]

    if default_branch not in branches:
        default_branch = branches[0]

    if len(branches) == 1:
        only_branch = branches[0]
        console.print(f"[bright_black]Discovered one remote branch: {only_branch}[/bright_black]")
        return only_branch, [only_branch]

    options = [(branch, branch) for branch in branches[:15]]
    options.append(("__manual__", "Enter branch manually"))
    selected = prompt_choice(
        "Select repo default branch", options, default_index=branches.index(default_branch) + 1
    )
    if selected == "__manual__":
        selected = typer.prompt("Repo default branch", default=default_branch)

    allowed_default = selected
    allowed_base_branches = split_csv(
        typer.prompt("Allowed base branches (comma separated)", default=allowed_default)
    )
    return selected, allowed_base_branches


def load_env_exports(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}
    exports: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("export ") or "=" not in line:
            continue
        body = line[len("export ") :]
        key, value = body.split("=", 1)
        parsed = value.strip()
        if parsed.startswith("'") and parsed.endswith("'"):
            parsed = parsed[1:-1].replace("'\"'\"'", "'")
        exports[key.strip()] = parsed
    return exports


def load_existing_config(config_path: Path) -> dict[str, object]:
    if not config_path.exists():
        return {}
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return raw
    return {}


def existing_config_value(config: dict[str, object], *keys: str) -> str | None:
    current: object = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = cast(dict[str, object], current)[key]
    if current is None:
        return None
    return str(current)


def find_ssh_key_pair() -> tuple[Path, Path] | None:
    for private_key in SSH_KEY_CANDIDATES:
        public_key = Path(f"{private_key}.pub")
        if private_key.exists() and public_key.exists():
            return private_key, public_key
    return None


def generate_ssh_key(email: str) -> tuple[Path, Path]:
    DEFAULT_SSH_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-C",
            email,
            "-f",
            str(DEFAULT_SSH_KEY_PATH),
            "-N",
            "",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise typer.BadParameter(f"ssh-keygen failed: {proc.stderr.strip()}")
    return DEFAULT_SSH_KEY_PATH, Path(f"{DEFAULT_SSH_KEY_PATH}.pub")


def start_ssh_agent() -> None:
    proc = subprocess.run(["ssh-agent", "-s"], check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise typer.BadParameter(f"ssh-agent failed: {proc.stderr.strip()}")

    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, remainder = line.split("=", 1)
        value = remainder.split(";", 1)[0]
        if key in {"SSH_AUTH_SOCK", "SSH_AGENT_PID"}:
            os.environ[key] = value


def add_ssh_key_to_agent(private_key: Path) -> None:
    start_ssh_agent()
    proc = subprocess.run(
        ["ssh-add", str(private_key)], check=False, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise typer.BadParameter(f"ssh-add failed: {proc.stderr.strip()}")


def verify_github_ssh() -> tuple[bool, str]:
    proc = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-T", "git@github.com"],
        check=False,
        capture_output=True,
        text=True,
    )
    output = "\n".join(part for part in [proc.stdout.strip(), proc.stderr.strip()] if part).strip()
    success = "successfully authenticated" in output.lower()
    return success, output


def github_https_to_ssh(url: str) -> str | None:
    prefixes = ["https://github.com/", "http://github.com/"]
    for prefix in prefixes:
        if url.startswith(prefix):
            path = url[len(prefix) :]
            return f"git@github.com:{path}"
    return None


def get_origin_remote_url(repo_root: Path) -> str | None:
    proc = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def set_origin_remote_url(repo_root: Path, remote_url: str) -> None:
    proc = subprocess.run(
        ["git", "remote", "set-url", "origin", remote_url],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise typer.BadParameter(f"git remote set-url failed: {proc.stderr.strip()}")


def ensure_github_ssh_setup(git_email: str, repo_root: Path) -> None:
    print_section("GitHub SSH")
    typer.echo("[git] Checking SSH setup...")
    key_pair = find_ssh_key_pair()
    if key_pair is None:
        typer.echo("[git] No SSH key found -> generating one")
        key_pair = generate_ssh_key(git_email)
        typer.echo(f"[git] SSH key created: {key_pair[0]}")
    else:
        typer.echo(f"[git] SSH key found -> {key_pair[0]}")

    private_key, public_key = key_pair
    add_ssh_key_to_agent(private_key)

    success, output = verify_github_ssh()
    if not success:
        typer.echo("[git] Add this SSH key to GitHub:")
        typer.echo("https://github.com/settings/keys")
        typer.echo("")
        typer.echo("--- BEGIN SSH KEY ---")
        typer.echo(public_key.read_text(encoding="utf-8").strip())
        typer.echo("--- END SSH KEY ---")
        typer.prompt(
            "Press ENTER after adding the SSH key to GitHub", default="", show_default=False
        )
        success, output = verify_github_ssh()

    if not success:
        raise typer.BadParameter(
            "[git] SSH verification failed. Please ensure the key was added to GitHub correctly.\n"
            f"{output}"
        )

    typer.echo("[git] SSH authentication successful")

    current_remote = get_origin_remote_url(repo_root)
    if current_remote:
        ssh_remote = github_https_to_ssh(current_remote)
        if ssh_remote and ssh_remote != current_remote:
            switch_remote = typer.confirm(
                f"Switch origin remote to SSH? [{ssh_remote}]",
                default=True,
            )
            if switch_remote:
                set_origin_remote_url(repo_root, ssh_remote)
                typer.echo(f"[git] origin updated to {ssh_remote}")


def render_settings_yaml(answers: SetupAnswers) -> str:
    repos: dict[str, dict[str, object]] = {}
    for repo in answers.repos:
        repos[repo.repo_key] = {
            "clone_url": repo.repo_clone_url,
            "default_branch": repo.repo_default_branch,
            "validation_commands": repo.repo_validation_commands or [".venv/bin/pytest -q"],
            "allowed_base_branches": repo.repo_allowed_base_branches or [repo.repo_default_branch],
            "bootstrap_enabled": repo.repo_bootstrap_enabled,
            "python_binary": repo.repo_python_binary,
            "venv_dir": repo.repo_venv_dir,
            "install_dev_command": repo.repo_install_dev_command
            or f"{repo.repo_venv_dir}/bin/pip install -e .[dev]",
        }

    config = {
        "board_backend": "forgejo",
        "forgejo": {
            "base_url": answers.forgejo_base_url,
            "api_token_env": answers.forgejo_api_token_env,
            "owner": answers.forgejo_owner,
            "repo": answers.forgejo_repo,
        },
        "git": {
            "provider": answers.git_provider,
            "token_env": answers.git_token_env,
            "open_pr_default": False,
            "push_on_validation_failure": True,
            "author_name": answers.git_author_name,
            "author_email": answers.git_author_email,
            "sign_commits": answers.git_sign_commits,
            "signing_key": answers.git_signing_key,
        },
        "team_executor": {
            "team_name": answers.executor_team,
            "timeout_seconds": 3600,
            "worker_backend": "claude_code",
            "dynamic_team_selection": False,
            "dynamic_worker_backend_selection": True,
            "budget_pressure_threshold": 0.75,
        },
        "dag_executor": {
            "tier_name": "budget",
            "timeout_seconds": 3600,
            "worker_backend": "claude_code",
            "dynamic_tier_selection": False,
            "dynamic_worker_backend_selection": True,
            "budget_pressure_threshold": 0.75,
        },
        "critique_executor": {
            "tier_name": "budget",
            "topology": "reflexion",
            "max_rounds": 5,
            "timeout_seconds": 3600,
            "worker_backend": "claude_code",
            "working_dir": "",
            "dynamic_tier_selection": False,
            "dynamic_worker_backend_selection": True,
            "budget_pressure_threshold": 0.75,
        },
        "repos": repos,
        "report_root": "tools/report/oc_plane",
    }
    return yaml.safe_dump(config, sort_keys=False, default_flow_style=False)


def render_env_file(answers: SetupAnswers) -> str:
    lines = [
        "# Local Operations Center environment",
        "# Source this file before running worker/api/smoke commands.",
        "",
        f"export {answers.forgejo_api_token_env}={shell_quote(answers.forgejo_api_token_value)}",
        f"export {answers.git_token_env}={shell_quote(answers.git_token_value)}",
        f"export OPERATIONS_CENTER_PROVIDER_PREFERRED_SMART={shell_quote(answers.preferred_smart_provider or '')}",
        f"export OPERATIONS_CENTER_PROVIDER_PREFERRED_FAST={shell_quote(answers.preferred_fast_provider or '')}",
        f"export OPERATIONS_CENTER_ALLOWED_PROVIDERS={shell_quote(','.join(answers.allowed_providers))}",
        f"export OPERATIONS_CENTER_PROVIDER_HEADLESS_REQUIRED={'1' if answers.headless_required else '0'}",
    ]
    if answers.executor_install_ref:
        lines.append(
            f"export OPERATIONS_CENTER_EXECUTOR_INSTALL_REF={shell_quote(answers.executor_install_ref)}"
        )
    provider_env_keys = {
        "claude": "OPERATIONS_CENTER_PROVIDER_CLAUDE_VERSION",
        "codex": "OPERATIONS_CENTER_PROVIDER_CODEX_VERSION",
        "gemini": "OPERATIONS_CENTER_PROVIDER_GEMINI_VERSION",
    }
    for key, env_key in provider_env_keys.items():
        version = answers.provider_versions.get(key, "")
        if version:
            lines.append(f"export {env_key}={shell_quote(version)}")
    lines.append(
        "# Provider auth is handled by provider-specific CLIs or env vars on this machine."
    )
    lines.extend(
        [
            f"export OPERATIONS_CENTER_DEFAULT_REPO={shell_quote(answers.default_repo_key)}",
            "",
        ]
    )
    return "\n".join(lines)


def render_task_template(answers: SetupAnswers) -> str:
    repo = next(repo for repo in answers.repos if repo.repo_key == answers.default_repo_key)
    allowed_paths = "\n".join(["  - src/"])
    allowed_branches = ", ".join(repo.repo_allowed_base_branches or [repo.repo_default_branch])
    return "\n".join(
        [
            "## Execution",
            f"repo: {repo.repo_key}",
            f"base_branch: {repo.repo_default_branch}",
            "mode: goal",
            "allowed_paths:",
            allowed_paths,
            "",
            "## Goal",
            "Describe the code change you want the executor to make.",
            "",
            "## Constraints",
            f"- Keep the selected base branch within: {allowed_branches}",
            "- Limit edits to the listed allowed paths.",
            "- Leave deployment and infrastructure files alone unless explicitly requested.",
            "",
        ]
    )


def shell_quote(value: str) -> str:
    escaped = value.replace("'", "'\"'\"'")
    return f"'{escaped}'"


def prompt_repo_with_discovery(
    repo_index: int,
    existing_config: dict[str, object],
    repo_root: Path,
) -> RepoSetupAnswers:
    suffix = "" if repo_index == 1 else f" #{repo_index}"
    print_section(f"Repo Setup{suffix}", "Choose a target repo and branch policy for AI task runs.")
    choices = discover_repo_choices(existing_config, repo_root)
    selected_mode: str
    if repo_index == 1 and choices:
        selected_mode = "choice:1"
        console.print(f"[bright_black]Defaulting to {choices[0].label}[/bright_black]")
        use_default_repo = typer.confirm("Use this repo?", default=True)
        if not use_default_repo:
            selection_options = [
                (f"choice:{index}", choice.label) for index, choice in enumerate(choices, start=1)
            ]
            selection_options.append(("manual", "Enter repo details manually"))
            selected_mode = prompt_choice("Select repo source", selection_options, default_index=1)
    else:
        selection_options = [
            (f"choice:{index}", choice.label) for index, choice in enumerate(choices, start=1)
        ]
        selection_options.append(("manual", "Enter repo details manually"))
        selected_mode = prompt_choice("Select repo source", selection_options, default_index=1)

    if selected_mode == "manual":
        repo_key = typer.prompt(
            "Repo key", default="operations-center" if repo_index == 1 else f"repo_{repo_index}"
        )
        repo_clone_url = typer.prompt(
            "Repo clone URL",
            default="git@github.com:you/operations-center.git"
            if repo_index == 1
            else "git@github.com:you/repo.git",
        )
        repo_default_branch, repo_allowed_base_branches = prompt_branch_selection(
            repo_clone_url, "main"
        )
    else:
        selected_choice = choices[int(selected_mode.split(":")[1]) - 1]
        console.print(
            f"[bright_black]Selected {selected_choice.label} -> {selected_choice.clone_url}[/bright_black]"
        )
        repo_key = typer.prompt("Repo key", default=selected_choice.repo_key)
        repo_clone_url = selected_choice.clone_url
        repo_default_branch, repo_allowed_base_branches = prompt_branch_selection(
            repo_clone_url,
            selected_choice.default_branch,
        )

    repo_advanced = typer.confirm(
        "Adjust validation or bootstrap details for this repo?", default=False
    )
    if repo_advanced:
        repo_validation_commands = split_multiline(
            typer.prompt(
                "Validation commands (use ';' between commands)",
                default=".venv/bin/pytest -q;.venv/bin/ruff check .",
            ).replace(";", "\n")
        )
        repo_bootstrap_enabled = typer.confirm(
            "Bootstrap repo-local .venv for this repo?", default=True
        )
        repo_python_binary = typer.prompt("Python binary for bootstrap", default="python3")
        repo_venv_dir = typer.prompt("Repo-local venv directory", default=".venv")
        repo_install_dev_command = typer.prompt(
            "Install dev command",
            default=f"{repo_venv_dir}/bin/pip install -e .[dev]",
        ).strip()
    else:
        console.print(
            "[bright_black]Using default validation and repo-local .venv bootstrap settings.[/bright_black]"
        )
        repo_validation_commands = [".venv/bin/pytest -q", ".venv/bin/ruff check ."]
        repo_bootstrap_enabled = True
        repo_python_binary = "python3"
        repo_venv_dir = ".venv"
        repo_install_dev_command = ".venv/bin/pip install -e .[dev]"

    return RepoSetupAnswers(
        repo_key=repo_key,
        repo_clone_url=repo_clone_url,
        repo_default_branch=repo_default_branch,
        repo_allowed_base_branches=repo_allowed_base_branches,
        repo_validation_commands=repo_validation_commands,
        repo_bootstrap_enabled=repo_bootstrap_enabled,
        repo_python_binary=repo_python_binary,
        repo_venv_dir=repo_venv_dir,
        repo_install_dev_command=repo_install_dev_command,
    )


@app.command()
def main(
    config_path: Path = typer.Option(
        Path("config/operations_center.local.yaml"),
        help="Path to write the local YAML config.",
    ),
    env_path: Path = typer.Option(
        Path(".env.operations-center.local"),
        help="Path to write the local env exports file.",
    ),
    task_template_path: Path = typer.Option(
        Path("config/task_template.local.md"),
        help="Path to write a starter board task template.",
    ),
) -> None:
    print_banner()
    existing_env = load_env_exports(env_path)
    existing_config = load_existing_config(config_path)

    print_section(
        "Forgejo",
        "The self-hosted Forgejo instance and the repo whose issues are the board.",
    )
    advanced_mode = typer.confirm("Advanced setup?", default=False)
    forgejo_base_default = (
        existing_config_value(existing_config, "forgejo", "base_url") or DEFAULT_FORGEJO_URL
    )
    forgejo_base_url = prompt_with_default(
        "Forgejo base URL",
        forgejo_base_default,
        note="Using saved value."
        if existing_config_value(existing_config, "forgejo", "base_url")
        else "The instance itself is a service you run (for example a container "
        "on this host); setup does not start it.",
    )
    forgejo_owner = prompt_with_default(
        "Forgejo user or org that owns the board repo",
        existing_config_value(existing_config, "forgejo", "owner") or "",
    )
    forgejo_repo = prompt_with_default(
        "Board repo name",
        existing_config_value(existing_config, "forgejo", "repo") or DEFAULT_FORGEJO_BOARD_REPO,
        note="One repo holds every fleet task. Forgejo numbers issues per repo, "
        "so a single repo makes that counter a global task-id sequence.",
    )
    forgejo_api_token_env = (
        existing_config_value(existing_config, "forgejo", "api_token_env") or "FORGEJO_API_TOKEN"
    )
    if advanced_mode:
        forgejo_api_token_env = typer.prompt(
            "Forgejo token env var name (required)", default=forgejo_api_token_env
        )

    typer.echo("")
    typer.echo(f"Create a token at {forgejo_base_url.rstrip('/')}/user/settings/applications")
    typer.echo("It needs read/write on repository and issue scopes.")

    existing_forgejo_token = existing_env.get(forgejo_api_token_env, "")
    if existing_forgejo_token and typer.confirm(
        "Reuse existing Forgejo API token from local env?", default=True
    ):
        forgejo_api_token_value = existing_forgejo_token
        typer.secho("Reusing existing Forgejo API token.", fg=typer.colors.BRIGHT_BLACK)
    else:
        forgejo_api_token_value = typer.prompt(
            "Paste Forgejo API token",
            hide_input=True,
            default="",
        )

    if typer.confirm("Verify the board repo now?", default=True):
        verified = verify_forgejo_configuration(
            forgejo_base_url,
            forgejo_api_token_value,
            forgejo_owner,
            forgejo_repo,
        )
        if not verified and not typer.confirm("Continue setup anyway?", default=False):
            raise typer.Abort()

    print_section("Git", "Remote host, authentication, commit identity, and SSH access.")
    git_provider = typer.prompt(
        "Git provider (remote host/service)",
        default=existing_config_value(existing_config, "git", "provider") or "github",
    )
    git_token_env = existing_config_value(existing_config, "git", "token_env") or "GITHUB_TOKEN"
    if advanced_mode:
        git_token_env = typer.prompt(
            "Git authentication key env var name (optional; used for HTTPS clone/push)",
            default=git_token_env,
        )
    typer.echo(
        "HTTPS authentication is optional. If your remotes use SSH, leave the auth key blank."
    )
    existing_git_token = existing_env.get(git_token_env, "")
    if existing_git_token:
        reuse_git_token = typer.confirm("Reuse existing Git token from local env?", default=True)
        if reuse_git_token:
            git_token_value = existing_git_token
            typer.secho("Reusing existing Git authentication key.", fg=typer.colors.BRIGHT_BLACK)
        else:
            git_token_value = typer.prompt(
                "Git authentication key/token (optional; leave blank if SSH will be used for git remotes)",
                hide_input=True,
                default="",
            )
    else:
        git_token_value = typer.prompt(
            "Git authentication key/token (optional; leave blank if SSH will be used for git remotes)",
            hide_input=True,
            default="",
        )
    git_author_name = prompt_with_default(
        "Git bot author name",
        existing_config_value(existing_config, "git", "author_name") or "Operations Center Bot",
        note="Using saved value."
        if existing_config_value(existing_config, "git", "author_name")
        else None,
    )
    git_author_email = prompt_with_default(
        "Git bot author email",
        existing_config_value(existing_config, "git", "author_email")
        or "operations-center-bot@example.com",
        note="Using saved value."
        if existing_config_value(existing_config, "git", "author_email")
        else None,
    )
    typer.echo("Commit signing is optional and only affects future signed-commit wiring.")
    git_sign_commits = typer.confirm(
        "Configure commit signing for the bot identity?",
        default=(existing_config_value(existing_config, "git", "sign_commits") or "false").lower()
        == "true",
    )
    existing_signing_key = existing_config_value(existing_config, "git", "signing_key") or ""
    git_signing_key: str | None = None
    if git_sign_commits:
        git_signing_key = (
            typer.prompt(
                "Git signing key id/fingerprint (optional; leave blank to configure later)",
                default=existing_signing_key,
            ).strip()
            or None
        )
    ensure_github_ssh_setup(git_author_email, Path.cwd())

    executor_install_ref = existing_env.get("OPERATIONS_CENTER_EXECUTOR_INSTALL_REF") or None
    if advanced_mode:
        executor_install_ref = (
            typer.prompt(
                "TeamExecutor version pin for drift reporting (optional)",
                default=executor_install_ref or "",
            ).strip()
            or None
        )

    print_section("Providers", "Supported executor backends detected on this machine.")
    statuses = detect_all_provider_statuses()
    write_provider_summary(statuses)
    provider_version_defaults = {
        "claude": existing_env.get("OPERATIONS_CENTER_PROVIDER_CLAUDE_VERSION", ""),
        "codex": existing_env.get("OPERATIONS_CENTER_PROVIDER_CODEX_VERSION", ""),
        "gemini": existing_env.get("OPERATIONS_CENTER_PROVIDER_GEMINI_VERSION", ""),
    }
    provider_versions = dict(provider_version_defaults)
    if advanced_mode:
        for provider_key in ["claude", "codex", "gemini"]:
            label = PROVIDER_SPECS[provider_key].label
            provider_versions[provider_key] = typer.prompt(
                f"{label} version pin (optional)",
                default=provider_version_defaults[provider_key],
            ).strip()

    for status in list(statuses):
        spec = PROVIDER_SPECS[status.key]
        if status.installed:
            continue
        if not spec.installable:
            typer.echo(f"[provider] {spec.label}: install manually if you want to use it.")
            continue
        should_install = typer.confirm(
            f"Install {spec.label} via {spec.install_method}?",
            default=status.key in {"codex", "claude"},
        )
        if should_install:
            typer.echo(f"[provider] Installing {spec.label}...")
            install_provider(spec, version=provider_versions.get(status.key) or None)
            typer.echo(f"[provider] Installed {spec.label}")
    statuses = detect_all_provider_statuses()

    for status in statuses:
        spec = PROVIDER_SPECS[status.key]
        if not status.installed:
            continue
        if status.key in {"codex", "gemini"} and not status.headless_ready:
            env_var = spec.auth_env_var
            if env_var:
                has_env = bool(os.environ.get(env_var))
                if not has_env:
                    prompt_api_key = typer.confirm(
                        f"Use {env_var} for headless auth with {spec.label}?", default=False
                    )
                    if prompt_api_key:
                        typer.echo(
                            f"Set {env_var} in your shell or local env file before unattended runs."
                        )
            if spec.interactive_login_command and not status.authenticated:
                run_login = typer.confirm(
                    f"Launch {spec.label} login now?", default=status.key in {"codex"}
                )
                if run_login:
                    typer.echo(f"[provider] Running: {spec.interactive_login_command}")
                    if not run_interactive_provider_login(spec, cwd=Path.cwd()):
                        typer.echo(
                            f"[provider] {spec.label} login did not complete successfully. You can finish it later and rerun setup or providers-status."
                        )
        elif status.key == "claude":
            if status.authenticated:
                typer.echo("[provider] Claude Code is already logged in.")
            else:
                typer.echo("Claude Code uses browser-based account login from the CLI.")
                run_login = typer.confirm("Launch Claude Code login now?", default=True)
                if run_login:
                    typer.echo(f"[provider] Running: {spec.interactive_login_command}")
                    if not run_interactive_provider_login(spec, cwd=Path.cwd()):
                        typer.echo(
                            "[provider] Claude Code login did not complete successfully. Finish it later with `claude auth login`."
                        )

    statuses = detect_all_provider_statuses()
    typer.echo("[provider] Final provider summary:")
    typer.echo(summarize_provider_statuses(statuses))

    print_section(
        "Executor Backends",
        "Ensure the execute backends OperationsCenter imports are available before writing config.",
    )
    ensure_executor_backends_installed(Path.cwd())

    usable_providers = [status.key for status in statuses if status.interactive_ready]
    if not usable_providers:
        raise typer.BadParameter(
            "No usable provider backend is ready. Install/authenticate at least one provider."
        )

    existing_headless_required = (
        existing_env.get("OPERATIONS_CENTER_PROVIDER_HEADLESS_REQUIRED") == "1"
    )
    headless_required = typer.confirm(
        "Require unattended/headless provider readiness?", default=existing_headless_required
    )
    if headless_required and not any(status.headless_ready for status in statuses):
        raise typer.BadParameter(
            "Headless mode requested, but no provider has API-key/headless readiness."
        )

    preferred_smart_provider = choose_preferred_provider(
        statuses,
        "Preferred smart provider",
        default=existing_env.get("OPERATIONS_CENTER_PROVIDER_PREFERRED_SMART") or "claude",
    )
    preferred_fast_provider = choose_preferred_provider(
        statuses,
        "Preferred fast provider",
        default=existing_env.get("OPERATIONS_CENTER_PROVIDER_PREFERRED_FAST") or "codex",
    )

    print_section("Executor", "Execution defaults for the local coding engine.")
    executor_team = prompt_with_default(
        "Executor team name",
        existing_config_value(existing_config, "team_executor", "team_name") or "budget",
        note="Using saved value."
        if existing_config_value(existing_config, "team_executor", "team_name")
        else None,
    )
    executor_orchestrator = prompt_with_default(
        "Executor orchestrator",
        default_orchestrator_for_statuses(
            statuses,
            preferred_smart_provider=preferred_smart_provider,
            saved_value=existing_config_value(existing_config, "team_executor", "orchestrator"),
        ),
        note="Using saved value."
        if existing_config_value(existing_config, "team_executor", "orchestrator")
        else None,
    )
    executor_cycles = int(
        prompt_with_default(
            "Executor cycles",
            existing_config_value(existing_config, "team_executor", "cycles") or "3",
            note="Using saved value."
            if existing_config_value(existing_config, "team_executor", "cycles")
            else None,
        )
    )
    executor_exchanges = int(
        prompt_with_default(
            "Executor exchanges",
            existing_config_value(existing_config, "team_executor", "exchanges") or "20",
            note="Using saved value."
            if existing_config_value(existing_config, "team_executor", "exchanges")
            else None,
        )
    )
    executor_effort = prompt_with_default(
        "Executor effort",
        existing_config_value(existing_config, "team_executor", "effort") or "standard",
        note="Using saved value."
        if existing_config_value(existing_config, "team_executor", "effort")
        else None,
    )

    print_section(
        "Repos", "Target repositories, branch policy, validation, and repo-local bootstrap."
    )
    repos: list[RepoSetupAnswers] = []
    repo_index = 1
    while True:
        repos.append(prompt_repo_with_discovery(repo_index, existing_config, Path.cwd()))
        repo_index += 1
        if not typer.confirm("Add another repo?", default=False):
            break

    default_repo_keys = ", ".join(repo.repo_key for repo in repos)
    default_repo_key = typer.prompt(
        "Default repo key for generated task template", default=repos[0].repo_key
    )
    if default_repo_key not in {repo.repo_key for repo in repos}:
        raise typer.BadParameter(
            f"Default repo key '{default_repo_key}' must match one of the configured repos: {default_repo_keys}"
        )

    answers = SetupAnswers(
        forgejo_base_url=forgejo_base_url,
        forgejo_owner=forgejo_owner,
        forgejo_repo=forgejo_repo,
        forgejo_api_token_env=forgejo_api_token_env,
        forgejo_api_token_value=forgejo_api_token_value,
        git_provider=git_provider,
        git_token_env=git_token_env,
        git_token_value=git_token_value,
        git_author_name=git_author_name,
        git_author_email=git_author_email,
        git_sign_commits=git_sign_commits,
        git_signing_key=git_signing_key,
        executor_install_ref=executor_install_ref,
        executor_team=executor_team,
        executor_cycles=executor_cycles,
        executor_exchanges=executor_exchanges,
        executor_orchestrator=executor_orchestrator,
        executor_effort=executor_effort,
        preferred_smart_provider=preferred_smart_provider,
        preferred_fast_provider=preferred_fast_provider,
        allowed_providers=usable_providers,
        headless_required=headless_required,
        provider_versions={k: v for k, v in provider_versions.items() if v},
        repos=repos,
        default_repo_key=default_repo_key,
    )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(render_settings_yaml(answers), encoding="utf-8")
    env_path.write_text(render_env_file(answers), encoding="utf-8")
    task_template_path.parent.mkdir(parents=True, exist_ok=True)
    task_template_path.write_text(render_task_template(answers), encoding="utf-8")

    print_section("Done", "Local setup files were updated.")
    typer.echo(f"Config: {config_path}")
    typer.echo(f"Env:    {env_path}")
    typer.echo(f"Task:   {task_template_path}")
    typer.echo("")
    typer.echo(f"Next: source {env_path}")
    typer.echo(f"Then use {config_path} with worker/api/smoke commands.")


if __name__ == "__main__":
    app()
