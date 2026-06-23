# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""lineage/cli.py — human/display view of the execution-lineage projection.

This is the *display* consumer (spec §2): it renders the full chain including
edges that are NOT steerable, marking each edge's trust state honestly so a
human never mistakes a GC-truncated, unverified, or host-relative edge for
ground truth.

    python -m operations_center.lineage.cli <task_id> [--runs-root P] [--state-dir P] [--json]
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .models import LineageChain, LineageEdge
from .projection import build_all, build_chain

_DEFAULT_RUNS_ROOT = Path.home() / ".console" / "operations_center" / "runs"


def _trust_glyph(edge: LineageEdge) -> str:
    """A compact, honest trust readout: ✓ only when fully steerable."""

    if edge.trust.is_steerable():
        return "[steerable]"
    flags = []
    t = edge.trust
    flags.append(t.provenance.value)
    if t.integrity.value != "chained":
        flags.append(t.integrity.value)
    if t.completeness.value != "durable":
        flags.append(t.completeness.value)
    if t.order.value != "causal":
        flags.append(t.order.value)
    return "[display-only: " + ", ".join(flags) + "]"


def render_chain(chain: LineageChain) -> str:
    lines = [f"task {chain.task_id}"]
    attrs = {n.kind: n.attributes for n in chain.nodes}
    if "task" in attrs:
        lines.append(f"  repo: {attrs['task'].get('repo_key')}")
    for edge in chain.edges:
        lines.append(f"  {edge.src} --{edge.kind}--> {edge.dst}  {_trust_glyph(edge)}")
    steerable = chain.steerable_edges()
    lines.append(f"  steerable edges: {len(steerable)} / {len(chain.edges)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lineage", description="View execution lineage.")
    parser.add_argument("task_id", nargs="?", help="task_id to render; omit to list all")
    parser.add_argument("--runs-root", type=Path, default=_DEFAULT_RUNS_ROOT)
    parser.add_argument("--state-dir", type=Path, default=Path("state"))
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a tree")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    if args.task_id:
        chain = build_chain(
            args.task_id, runs_root=args.runs_root, state_dir=args.state_dir, now=now
        )
        if args.json:
            print(json.dumps(chain.as_dict(), indent=2, default=str))
        else:
            print(render_chain(chain))
        return 0

    chains = build_all(runs_root=args.runs_root, state_dir=args.state_dir, now=now)
    if args.json:
        print(json.dumps({k: v.as_dict() for k, v in chains.items()}, indent=2, default=str))
    else:
        for chain in chains.values():
            print(render_chain(chain))
            print()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
