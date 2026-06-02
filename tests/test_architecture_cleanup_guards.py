# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_executor_stack_removed_from_source_tree() -> None:
    assert not (REPO_ROOT / "src" / "operations_center" / "adapters" / "executor").exists()


def test_legacy_execution_removed_from_source_tree() -> None:
    assert not (REPO_ROOT / "src" / "operations_center" / "legacy_execution").exists()


def test_removed_modules_cannot_be_imported(assert_module_unavailable) -> None:
    assert_module_unavailable("operations_center.adapters.executor.factory")
    assert_module_unavailable("operations_center.legacy_execution")


def test_routing_client_no_longer_supports_in_process_bypass() -> None:
    source = (REPO_ROOT / "src" / "operations_center" / "routing" / "client.py").read_text(
        encoding="utf-8"
    )
    assert "LocalLaneRoutingClient" not in source
    assert 'os.environ.get("SWITCHBOARD_URL")' not in source


_ADR_0007_FORBIDDEN_MSG = (
    "ADR 0007 forbids direct Claude CLI calls. Route LLM work through a "
    "spec-author Plane task. See PlatformDeployment/docs/architecture/adr/"
    "0007-spec-director-refactor.md."
)


def _iter_source_files(root: Path):
    for path in root.rglob("*.py"):
        # Skip __pycache__ and egg-info build artefacts
        if "__pycache__" in path.parts or any(p.endswith(".egg-info") for p in path.parts):
            continue
        yield path


def test_adr_0007_no_claude_cli_module_in_source_tree() -> None:
    """ADR 0007 Phase E: ``spec_director/_claude_cli.py`` was deleted and may
    not be reintroduced. All LLM work must flow through the backend executor
    via a spec-author Plane task. Phase F renamed the package to ``spec_author``;
    this guard checks both the historical and current paths."""
    legacy_forbidden = REPO_ROOT / "src" / "operations_center" / "spec_director" / "_claude_cli.py"
    current_forbidden = REPO_ROOT / "src" / "operations_center" / "spec_author" / "_claude_cli.py"
    assert not legacy_forbidden.exists(), _ADR_0007_FORBIDDEN_MSG
    assert not current_forbidden.exists(), _ADR_0007_FORBIDDEN_MSG


def test_adr_0007_no_claude_cli_imports_in_source_tree() -> None:
    """ADR 0007 Phase E: nothing in ``src/`` may import the deleted
    ``_claude_cli`` module or call ``call_claude`` as an executable reference.
    String / comment / docstring mentions of the historical bypass are
    permitted (intentional breadcrumbs) — this guard inspects the parsed AST
    rather than the raw text."""
    import ast

    src_root = REPO_ROOT / "src" / "operations_center"
    offenders: list[str] = []
    for path in _iter_source_files(src_root):
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # `from operations_center.spec_author._claude_cli import ...`
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if "_claude_cli" in mod:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: import from {mod}"
                    )
            # `import operations_center.spec_author._claude_cli`
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "_claude_cli" in alias.name:
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}:{node.lineno}: import {alias.name}"
                        )
            # `call_claude(...)`
            elif isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name == "call_claude":
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: call_claude(...) invocation"
                    )
    assert not offenders, _ADR_0007_FORBIDDEN_MSG + "\nOffenders:\n" + "\n".join(offenders)


def test_no_execution_proxy_env_injection_in_canonical_backends() -> None:
    paths = [
        REPO_ROOT / "src" / "operations_center" / "backends" / "openclaw" / "invoke.py",
        REPO_ROOT / "src" / "operations_center" / "backends" / "direct_local" / "adapter.py",
        REPO_ROOT / "src" / "operations_center" / "backends" / "aider_local" / "adapter.py",
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "OPENAI_API_BASE" not in source
