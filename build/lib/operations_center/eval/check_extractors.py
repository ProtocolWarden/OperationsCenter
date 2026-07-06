# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Production check-extractor for the drift monitor (§4.2, D-EVAL-5).

The non-blocking drift monitor (:mod:`operations_center.eval.critic`) needs a model
to re-derive the typed ``checks`` for a case from its diff/context, so it can detect
when the reviewer model's *extraction* behavior drifts from the signed answer.

``BackendCheckExtractor`` is that model adapter. It builds the same review prompt the
reviewer uses (``verdict_schema_prompt``), asks an injected model invoker, and parses
the ``checks`` array out of the response — the same shape the reviewer writes to
``verdict.json``. The invoker is injected so the extractor is testable without a live
backend; production wires it to a **different model family than the implementer**
(N copies of one family is N=1 — a same-weights clone shares blindspots), per
D-EVAL-5.

Note: this needs *extraction-kind* corpus cases (input carries a ``diff``/``context``
for the model to review) to be exercised live. The current seed corpus is
verdict-kind (pre-filled ``checks``), so this adapter is wired and tested but waits
on an extraction corpus layer + a configured non-implementer backend."""

from __future__ import annotations

import json
from typing import Callable

from operations_center.entrypoints.pr_review_watcher.verdict import verdict_schema_prompt
from operations_center.eval.corpus import Case

# invoke(prompt, vote) -> raw model text. ``vote`` lets the caller vary sampling per
# N-of-M vote (seed/temperature) so repeated calls are genuinely independent.
ModelInvoker = Callable[[str, int], str]


def build_extraction_prompt(case: Case) -> str:
    """The review prompt for a case: its change context + the typed-verdict schema."""
    inp = case.input
    context = inp.get("diff") or inp.get("context")
    if not isinstance(context, str) or not context:
        context = json.dumps(inp, sort_keys=True, ensure_ascii=False)
    return (
        "Review the following change and report a status for each enumerated "
        f"check.\n\n--- change ---\n{context}\n--- end change ---\n\n"
        + verdict_schema_prompt()
    )


def parse_checks(raw: str) -> list:
    """Extract the ``checks`` list from a model response (JSON, possibly prose-wrapped).

    Fail-safe: an unparseable/absent ``checks`` returns ``[]`` — which
    ``compute_verdict`` turns into CONCERNS, so a model that stops emitting valid
    output reads as drift (a signal), never as a silent pass."""
    if not isinstance(raw, str):
        return []
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        obj = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    checks = obj.get("checks") if isinstance(obj, dict) else None
    return checks if isinstance(checks, list) else []


class BackendCheckExtractor:
    """A :class:`operations_center.eval.critic.CheckExtractor` backed by a model."""

    def __init__(self, invoke: ModelInvoker) -> None:
        self._invoke = invoke

    def __call__(self, case: Case, *, vote: int) -> object:
        prompt = build_extraction_prompt(case)
        try:
            raw = self._invoke(prompt, vote)
        except Exception:  # noqa: BLE001 — a backend hiccup reads as drift, never a pass
            return []
        return parse_checks(raw)


__all__ = [
    "BackendCheckExtractor",
    "ModelInvoker",
    "build_extraction_prompt",
    "parse_checks",
]
