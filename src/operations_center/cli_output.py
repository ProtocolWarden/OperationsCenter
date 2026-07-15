# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Shared structured-output rendering for CLI commands.

Every CLI entrypoint that supports a machine-readable ``--json``/
``--format json`` mode owns its own ``rich.console.Console`` and, before
this module existed, serialized that mode's payload independently —
some via ``typer.echo(json.dumps(...))`` (bypassing ``Console``
entirely), others via ``console.print(json.dumps(...))`` (losing syntax
highlighting and risking soft-wrap on long values). ``print_structured``
is the one call every such command should make instead: it normalizes
the payload and always renders it through ``Console.print_json``, which
does not soft-wrap and emits no ANSI codes on non-tty output.

See ``.console/STAGE1_PRINT_STRUCTURED_DESIGN.md`` for the design
rationale and the per-file migration table.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from rich.console import Console


def print_structured(console: Console, output: Any, *, sort_keys: bool = False) -> None:
    """Render `output` as syntax-highlighted, unwrapped JSON via `console`.

    `output` may be a `dict`, a Pydantic `BaseModel`, a `dataclass`
    instance, another `Mapping`, or any other JSON-serializable value
    (list, primitive). It is normalized to a plain JSON-native value and
    passed to `Console.print_json`, which never soft-wraps regardless of
    `console.width` and never emits ANSI escapes when `console.file` is
    not a tty.

    Pre-serialized JSON strings are not accepted specially — a `str`
    argument is rendered as a JSON string scalar, not parsed and
    re-rendered as an object. Callers must pass data, not
    `model.model_dump_json()`.

    Args:
        console: the caller's own `Console` instance.
        output: the structured payload to render.
        sort_keys: forwarded to the underlying JSON encoder; set `True`
            for output consumed by automation/diffing that depends on a
            deterministic key order.
    """
    if isinstance(output, BaseModel):
        payload: Any = output.model_dump(mode="json")
    elif dataclasses.is_dataclass(output) and not isinstance(output, type):
        payload = dataclasses.asdict(output)
    elif isinstance(output, Mapping) and not isinstance(output, dict):
        payload = dict(output)
    else:
        payload = output

    console.print_json(data=payload, indent=2, ensure_ascii=False, default=str, sort_keys=sort_keys)
