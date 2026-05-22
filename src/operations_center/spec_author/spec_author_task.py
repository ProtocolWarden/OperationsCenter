# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
# src/operations_center/spec_author/spec_author_task.py
"""Shared helpers for building ``spec-author`` Plane tasks (ADR 0007).

Originally lived inside ``entrypoints/spec_trigger/main.py``. Hoisted here in
Phase D so ``spec_hygiene`` can emit phase-advance spec-author tasks with the
same body shape — ``board_worker._parse_spec_author_payload`` works against a
single canonical layout regardless of which watcher created the task.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from operations_center.adapters.plane import PlaneClient


# Labels that mark in-flight spec-author work. Dedupe key for watchers:
# if any non-Done issue carries BOTH labels, skip creating a new task.
LABEL_SOURCE = "source: spec-director"
LABEL_TASK_KIND = "task-kind: spec-author"

# Initial Plane state for spec-author tasks.
INITIAL_STATE = "Ready for AI"


@dataclass
class SpecAuthorPayload:
    spec_slug: str
    trigger_source: str
    target_path: str
    seed_text: str = ""
    # Optional — set ONLY for phase-advance tasks emitted by spec_hygiene.
    # When unset, board_worker treats this as a fresh draft (initial authoring).
    task_phase: str = ""
    recent_git_log_repos: dict[str, str] = field(default_factory=dict)
    existing_specs: list[str] = field(default_factory=list)
    ready_count: int = 0
    running_count: int = 0
    drained: bool = False


def render_task_body(p: SpecAuthorPayload) -> str:
    """Compose the Plane task description (markdown wrapping one YAML block).

    The ``## Spec Authoring`` heading delimits the YAML block from any future
    operator commentary appended in Plane's UI. ``board_worker`` parses the
    YAML via ``_parse_spec_author_payload``.
    """
    git_block = (
        "\n".join(
            f"    {repo}: |\n      {log.replace(chr(10), chr(10) + '      ')}"
            for repo, log in p.recent_git_log_repos.items()
        )
        if p.recent_git_log_repos
        else "    {}"
    )
    specs_block = (
        "\n".join(f"    - {slug}" for slug in p.existing_specs)
        if p.existing_specs
        else "    []"
    )
    seed_block = (
        "  " + p.seed_text.replace("\n", "\n  ") if p.seed_text else "  ''"
    )
    task_phase_line = f"task_phase: {p.task_phase}\n" if p.task_phase else ""
    return f"""## Spec Authoring

```yaml
task-kind: spec-author
source: spec-director
spec_slug: {p.spec_slug}
trigger_source: {p.trigger_source}
{task_phase_line}target_path: {p.target_path}
seed_text: |
{seed_block}
context_bundle:
  recent_git_log_repos:
{git_block}
  existing_specs:
{specs_block}
  board_snapshot:
    ready: {p.ready_count}
    running: {p.running_count}
    drained: {str(p.drained).lower()}
```
"""


def create_spec_author_task(client: PlaneClient, payload: SpecAuthorPayload) -> str:
    """Create the Plane task and return its id."""
    title = f"[Spec] {payload.spec_slug}"
    if payload.task_phase:
        title = f"[Spec:{payload.task_phase}] {payload.spec_slug}"
    body = render_task_body(payload)
    labels = [
        LABEL_TASK_KIND,
        LABEL_SOURCE,
        f"trigger: {payload.trigger_source}",
        f"spec-slug: {payload.spec_slug}",
    ]
    if payload.task_phase:
        labels.append(f"task-phase: {payload.task_phase}")
    issue = client.create_issue(
        name=title,
        description=body,
        label_names=labels,
        state=INITIAL_STATE,
    )
    return str(issue["id"])


def find_in_flight_phase_advance(
    issues: list[dict], spec_slug: str, task_phase: str,
) -> str | None:
    """Dedupe key for spec_hygiene's phase-advance emit: return the id of any
    non-Done spec-author task with the same ``spec_slug`` and ``task_phase``,
    else None."""
    src = LABEL_SOURCE.lower()
    kind = LABEL_TASK_KIND.lower()
    slug_label = f"spec-slug: {spec_slug}".lower()
    phase_label = f"task-phase: {task_phase}".lower()
    for issue in issues:
        state_name = str((issue.get("state") or {}).get("name", "")).lower()
        if state_name == "done":
            continue
        names: list[str] = []
        for label in issue.get("labels", []) or []:
            if isinstance(label, dict):
                names.append(str(label.get("name", "")).lower())
            else:
                names.append(str(label).lower())
        if (
            src in names
            and kind in names
            and slug_label in names
            and phase_label in names
        ):
            return str(issue.get("id", ""))
    return None
