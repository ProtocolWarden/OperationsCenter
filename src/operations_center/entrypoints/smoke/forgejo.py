# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Smoke-test the live board through the seam.

Read-only by default: proves auth, pagination, and state derivation against the
configured backend. `--write` runs the full round-trip the fleet performs —
create, transition, comment, prioritise, Done — leaving one conforming issue in
a terminal state.

Replaces `smoke/plane.py`, which smoked the Plane API specifically. This one
goes through `make_board_client`, so it smokes whichever backend the config
names — which is the property the fleet actually depends on.
"""

from __future__ import annotations

import argparse

SMOKE_BODY = """## Goal
Smoke-verify the board adapter end to end against the live instance.
Created by `operations-center.sh smoke --write`; safe to ignore.

## Execution
repo: OperationsCenter
mode: goal
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--write",
        action="store_true",
        help="also run the create/transition/comment/priority round-trip",
    )
    args = parser.parse_args()

    from operations_center.adapters.board import make_board_client
    from operations_center.config import load_settings

    settings = load_settings(args.config)
    board = make_board_client(settings)
    print(f"backend: {type(board).__name__}")

    states = [state["name"] for state in board.list_states()]
    print(f"states: {states}")

    issues = board.list_issues()
    print(f"issues on the board: {len(issues)}")

    if not args.write:
        print("read-only smoke OK (pass --write for the full round-trip)")
        return 0

    created = board.create_issue(
        name="smoke: adapter round-trip", description=SMOKE_BODY
    )
    task_id = str(created.get("number") or created.get("id"))
    print(f"created #{task_id}")

    board.transition_issue(task_id, "Ready for AI")
    task = board.to_board_task(board.fetch_issue(task_id))
    if task.status != "Ready for AI":
        raise RuntimeError(f"transition failed: expected 'Ready for AI', board says {task.status!r}")
    print(f"transitioned -> {task.status!r}")

    board.comment_issue(task_id, "smoke: comment round-trip")
    board.set_priority(task_id, "low")
    board.transition_issue(task_id, "Done")
    task = board.to_board_task(board.fetch_issue(task_id))
    if task.status != "Done":
        raise RuntimeError(f"transition failed: expected 'Done', board says {task.status!r}")
    print(f"finished -> {task.status!r}")
    print("write smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
