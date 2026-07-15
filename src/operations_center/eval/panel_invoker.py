# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""C3 — live cross-family invoker for the EVAL panel (COUNCIL_VERDICT.md C3).

``panel_critic.run_panel_drift_monitor`` is pure: it takes a ``CheckExtractor``
per family and never touches a subprocess. This module is the impure seam that
wires a real family to a real CLI — one per configured family tag (e.g.
``"claude_code"``, ``"codex_cli"``) — reusing the exact argv shape the C1
council already runs in production (``member_runner.build_member_argv``)
instead of inventing a bespoke invocation for the grading lane.

Each call runs the family's CLI once, bounded, in a fresh empty tmpdir (mirrors
``pr_review_watcher._run_member_review``): reads ``verdict.json`` if the
backend wrote it, else falls back to the last balanced JSON object on stdout
(``verdict.last_json_object`` — codex answers on stdout instead of the file
contract). Any failure — missing binary, timeout, crash, unparseable output —
returns ``[]`` (no checks), which ``compute_verdict`` turns into CONCERNS: an
invoker hiccup must read as drift, never as a silent pass.

Kept deliberately thin: the only logic here is "run one CLI, get the checks
array out of it." Genuinely untestable subprocess glue is marked
``# pragma: no cover``; nothing else in this module should be."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from operations_center.entrypoints.pr_review_watcher.member_runner import build_member_argv
from operations_center.entrypoints.pr_review_watcher.verdict import last_json_object
from operations_center.eval.check_extractors import build_extraction_prompt
from operations_center.eval.corpus import Case
from operations_center.eval.critic import CheckExtractor

DEFAULT_TIMEOUT_SECONDS = 300

# The default (backend, model) for each family tag — mirrors the family-level
# seat the C1 council already runs (verdict._COUNCIL_PANEL): cheapest model
# that still represents that family's judgment, not a specific council lens.
DEFAULT_MODEL_BY_FAMILY: dict[str, str] = {
    "claude_code": "sonnet",
    "codex_cli": "codex",
}

# The CLI binary that backs each family tag — used only to PROBE availability
# (shutil.which) at wiring time, mirroring worker_backend_probe's own binary
# names. Not a full PATH-fallback resolver (that's the probe module's job);
# this is a cheap, deterministic "is it even on PATH" gate so a missing binary
# is caught BEFORE the panel runs, as a degraded-panel skip, rather than
# surfacing as an empty-checks "drift" from a subprocess that never started.
_FAMILY_BINARY: dict[str, str] = {
    "claude_code": "claude",
    "codex_cli": "codex",
}


def resolve_available_families(families: list[str]) -> list[str]:
    """Return the subset of ``families`` whose CLI binary is resolvable on PATH.

    Called at wiring time (spec_hygiene/main.py) to compute the runnable
    subset of the *configured* panel BEFORE building any extractors. The
    caller passes the full configured panel to ``DriftMonitorTask`` as
    ``panel_families`` and only the resolvable subset's extractors as
    ``family_extractors`` — the task compares the two and skips loudly on any
    gap instead of silently grading with whatever's left (COUNCIL_VERDICT.md
    C3's "never collapse to a smaller/same-family panel" rule).
    """
    return [f for f in families if shutil.which(_FAMILY_BINARY.get(f, f)) is not None]


class LiveFamilyExtractor:
    """A :class:`critic.CheckExtractor` for one family, backed by a live CLI call.

    Not unit-tested beyond construction — exercising it means actually
    spawning ``claude``/``codex``, which is exactly the "genuinely untestable"
    glue the pure ``panel_critic`` module was split out to avoid needing."""

    def __init__(
        self, backend: str, model: str, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self._backend = backend
        self._model = model
        self._timeout = timeout_seconds

    def __call__(self, case: Case, *, vote: int) -> object:  # pragma: no cover — live subprocess glue
        prompt = build_extraction_prompt(case)
        argv = build_member_argv(self._backend, self._model, prompt)
        if argv is None:
            return []
        try:
            with tempfile.TemporaryDirectory(prefix="oc-eval-panel-") as tmpdir:
                tmp = Path(tmpdir)
                proc = subprocess.run(
                    argv,
                    cwd=str(tmp),
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                )
                verdict_path = tmp / "verdict.json"
                raw: dict | None = None
                if verdict_path.exists():
                    try:
                        raw = json.loads(verdict_path.read_text(encoding="utf-8"))
                    except Exception:
                        raw = None
                if raw is None:
                    # Fallback for a backend that answers on stdout instead of
                    # writing the file (observed with codex).
                    raw = last_json_object(proc.stdout)
        except Exception:
            # Missing binary, timeout, crash — an invoker failure reads as
            # drift (empty checks -> CONCERNS), never a silent pass.
            return []
        checks = raw.get("checks") if isinstance(raw, dict) else None
        return checks if isinstance(checks, list) else []


def build_family_extractor(
    family: str,
    *,
    model: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> CheckExtractor:
    """Build a live :class:`critic.CheckExtractor` for one family tag.

    Raises ``ValueError`` for a family with no known default model and none
    given explicitly — the caller (``DriftMonitorTask``) treats a build
    failure the same as a degraded/unavailable family: skip the whole panel
    run with a loud reason, never silently drop to a smaller (possibly
    same-family) panel.
    """
    resolved_model = model or DEFAULT_MODEL_BY_FAMILY.get(family)
    if resolved_model is None:
        raise ValueError(f"no default model for family {family!r}; pass model explicitly")
    return LiveFamilyExtractor(family, resolved_model, timeout_seconds=timeout_seconds)


def build_panel_extractors(
    families: list[str], *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
) -> dict[str, CheckExtractor]:
    """Build one live ``CheckExtractor`` per configured family tag.

    Raises (propagates ``build_family_extractor``'s ``ValueError``) on an
    unrecognized family rather than silently omitting it — an omitted family
    would shrink the panel without the caller noticing, which is exactly the
    same-family-collapse failure mode C3 exists to prevent.
    """
    return {
        family: build_family_extractor(family, timeout_seconds=timeout_seconds)
        for family in families
    }


__all__ = [
    "DEFAULT_MODEL_BY_FAMILY",
    "DEFAULT_TIMEOUT_SECONDS",
    "LiveFamilyExtractor",
    "build_family_extractor",
    "build_panel_extractors",
    "resolve_available_families",
]
