#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""DEPRECATED shim — the OC watchdog loop moved to ContextLifecycle.

The engine is `context_lifecycle.pseudo_operator` (Track B of the grounded
audit: one config-parameterized harness instead of two copy-paste
controllers). Policy lives in `.console/workers.yaml` under `pseudo_operator:`.

This shim keeps every existing launch path working unchanged —
`scripts/operations-center.sh loop-start|loop-stop|loop-status` all
invoke `python3 tools/loop/controller.py [...]` — by exec'ing the matching
`cl loop` subcommand. Remove once the wrappers call `cl loop` directly.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".console" / "workers.yaml"


def _resolve_cl() -> str:
    found = shutil.which("cl")
    if found:
        return found
    cl_home = os.environ.get("CL_HOME", "")
    if not cl_home:
        try:
            settings = Path.home() / ".claude" / "settings.json"
            if settings.exists():
                cl_home = (
                    json.loads(settings.read_text(encoding="utf-8"))
                    .get("env", {})
                    .get("CL_HOME", "")
                )
        except (OSError, json.JSONDecodeError):
            cl_home = ""
    candidate = Path(cl_home) / "bin" / "cl" if cl_home else None
    if candidate and candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    raise SystemExit(
        "cl CLI not found (PATH, CL_HOME, or ~/.claude/settings.json env.CL_HOME). "
        "The loop engine lives in ContextLifecycle — install it first."
    )


def main() -> None:
    args = sys.argv[1:]
    if "--stop" in args:
        sub = ["stop"]
    elif "--status" in args:
        sub = ["status"]
    elif "--signal" in args:
        idx = args.index("--signal")
        if idx + 1 >= len(args):
            raise SystemExit("--signal requires a TASK argument")
        sub = ["signal", args[idx + 1]]
    else:
        sub = ["run"]
    cl = _resolve_cl()
    os.execv(cl, [cl, "loop", *sub, "--config", str(CONFIG)])


if __name__ == "__main__":
    main()
