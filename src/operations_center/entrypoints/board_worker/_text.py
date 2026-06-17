# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Text extraction and prompt-building helpers for board_worker."""

from __future__ import annotations

_PROMPT_DIFF_OPEN = "<!-- prompt_diff_edits -->"
_PROMPT_DIFF_CLOSE = "<!-- /prompt_diff_edits -->"


def desc_text(issue: dict) -> str:
    """Return best-available plain text from a Plane work-item dict.

    The Plane list endpoint only populates description_html (not description or
    description_stripped). Strip HTML tags so downstream text processing sees
    plain text instead of raw markup.
    """
    text = issue.get("description") or issue.get("description_stripped") or ""
    if not text:
        html_val = issue.get("description_html") or ""
        if html_val:
            import html as _html
            import re as _re

            text = _re.sub(r"<br\s*/?>", "\n", html_val)
            text = _re.sub(r"<[^>]+>", "", text)
            text = _html.unescape(text)
    return text


def extract_goal(description: str, title: str) -> str:
    """Pull goal text from ## Goal section, fall back to title."""
    import re

    m = re.search(r"##\s+Goal\s*\n(.*?)(?=##|\Z)", description, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1).strip()
        if text:
            return text
    return title


def task_type_from_kind(task_kind: str) -> str:
    return {
        "goal": "feature",
        "test": "test",
        "test_campaign": "test",
        "improve": "refactor",
        "improve_campaign": "refactor",
        "spec-author": "chore",
    }.get(task_kind, "chore")


def parse_spec_author_payload(description: str) -> dict | None:
    """Extract the YAML payload spec_trigger embeds in the task description."""
    import html as _html
    import re as _re

    import yaml as _yaml

    if "<" in description:
        description = _re.sub(r"<br\s*/?>", "\n", description)
        description = _re.sub(r"<[^>]+>", "", description)
        description = _html.unescape(description)
    m = _re.search(r"```yaml\s*\n(.*?)\n```", description, _re.DOTALL)
    if not m:
        return None
    try:
        data = _yaml.safe_load(m.group(1))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def summarize_prompt_diff_block(*, workspace, target_path: str) -> tuple[int | None, str]:
    """Soft-validate the prompt_diff_edits fence in a committed spec.

    Returns (edit_count, note):
    - (N, "parsed") — fence present and YAML deserialized to N Edit objects.
    - (None, "absent") — no fence in the file.
    - (None, "<reason>") — fence present but failed to parse (logged, not fatal).
    """
    from pathlib import Path

    try:
        spec_text = Path(workspace / target_path).read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"read failed: {exc}"

    if _PROMPT_DIFF_OPEN not in spec_text or _PROMPT_DIFF_CLOSE not in spec_text:
        return None, "absent"

    try:
        body = spec_text.split(_PROMPT_DIFF_OPEN, 1)[1].split(_PROMPT_DIFF_CLOSE, 1)[0]
    except IndexError:
        return None, "fence malformed"

    try:
        import yaml

        from operations_center.prompt_diff import Edit

        doc = yaml.safe_load(body) or {}
        raw_edits = doc.get("edits") if isinstance(doc, dict) else None
        if not isinstance(raw_edits, list):
            return None, "edits key missing or not a list"
        parsed = [Edit.model_validate(e) for e in raw_edits]
        return len(parsed), "parsed"
    except Exception as exc:  # noqa: BLE001
        return None, f"parse failed: {type(exc).__name__}: {exc}"


def build_phase_advance_goal_text(
    *,
    spec_slug: str,
    target_path: str,
    task_phase: str,
    seed_text: str,
    ctx: dict,
    run_id_placeholder: str,
) -> str:
    """Phase-advance spec rewrite prompt (ADR 0007 Phase D + follow-up C)."""
    parts: list[str] = []
    parts.append(
        f"# Spec phase advance — {spec_slug} -> {task_phase}\n\n"
        f"The campaign spec at `{target_path}` is currently between phases. "
        f"Its predecessor phase has finished; the spec needs **minimal, "
        f"targeted edits** so it describes the **{task_phase}** phase "
        f"concretely. Do NOT rewrite the spec from scratch — emit a "
        f"structured diff and apply it."
    )
    parts.append(
        "## Required actions\n"
        f"1. Read the existing spec at `{target_path}` (it is already in the "
        f"workspace — this repository is `OperationsCenter` and the file is "
        f"committed on the current branch).\n"
        f"2. Decide the smallest set of edits that updates `## Goals` and "
        f"`## Success Criteria` for the `{task_phase}` phase. Leave everything "
        f"else alone — front-matter, prior decisions, completed-phase notes, "
        f"the `<!-- generated_by_run: ... -->` provenance line on line 1.\n"
        f"3. Emit those edits as a YAML list inside the fenced block "
        f"described below.\n"
        f"4. Apply the edits to produce the new spec contents, then write "
        f"the result to `{target_path}`. Touch no other file.\n"
        f"5. The fenced ``prompt_diff_edits`` block MUST remain in the "
        f"committed spec — it is the audit trail of what changed and why.\n"
    )
    parts.append(
        "## Edit schema\n"
        "Each entry in the YAML list is one ``Edit`` object. Shape:\n"
        "\n"
        "```\n"
        "- op: replace | insert_before | insert_after | delete | append\n"
        "  anchor: <exact substring from the current spec — REQUIRED except for append>\n"
        "  new_text: <text to insert / replace with — REQUIRED except for delete>\n"
        "  reason: <one short sentence; operator reads this in audit>\n"
        "  targets_criterion: <optional rubric name; null is fine>\n"
        "```\n"
        "\n"
        "Hard rules:\n"
        "- Each ``anchor`` MUST appear EXACTLY ONCE in the current spec. If a string "
        "occurs multiple times, anchor on a longer surrounding substring that is unique.\n"
        "- Anchors match by exact substring (whitespace- and case-sensitive).\n"
        "- Keep edits MINIMAL — touch only what must change for the new phase. "
        'No stylistic cleanup, no "while I\'m here" rewrites.\n'
        "- Preserve the `<!-- generated_by_run: {{RUN_ID}} -->` provenance line "
        "on line 1 unchanged. Phase advances do not overwrite authorship provenance.\n"
        "- Preserve front-matter keys (campaign_id, slug, repos, area_keywords, "
        "created_at). Only ``status:`` may change if relevant.\n"
    )
    parts.append(
        "## Output fence\n"
        f"Emit your edits between these two markers (literal, including the "
        f"angle brackets and dashes), placed at the END of the rewritten spec:\n"
        "\n"
        f"```\n{_PROMPT_DIFF_OPEN}\n"
        "edits:\n"
        "  - op: replace\n"
        '    anchor: "## Goals\\n1. Implement the parser.\\n"\n'
        '    new_text: "## Goals\\n1. Add unit coverage for the parser.\\n"\n'
        '    reason: "advance from implement to test phase"\n'
        "    targets_criterion: null\n"
        "  - op: insert_after\n"
        '    anchor: "## Success Criteria\\n"\n'
        f'    new_text: "- {task_phase} phase: coverage report attached to the campaign run.\\n"\n'
        '    reason: "add phase-specific done criterion"\n'
        "    targets_criterion: null\n"
        f"{_PROMPT_DIFF_CLOSE}\n```\n"
        "\n"
        "The example above is illustrative — substitute anchors and text that "
        "actually exist in THIS spec.\n"
    )
    if seed_text:
        parts.append(
            "## Phase state (from spec_hygiene)\n"
            "Use this to ground the rewrite — it is the orchestrator's view of "
            "what just finished and what should come next.\n\n"
            f"```\n{seed_text}\n```"
        )
    repos = ctx.get("recent_git_log_repos") or {}
    if isinstance(repos, dict):
        for repo_key, log_text in repos.items():
            if log_text:
                parts.append(f"## Recent Git Activity ({repo_key})\n```\n{log_text}\n```")
    parts.append(
        "## Boundaries\n"
        f"- Touch exactly one file: `{target_path}`.\n"
        "- Do not create new files.\n"
        "- Do not modify the campaign_id, slug, repos, or area_keywords.\n"
        "- Do not regenerate the spec from scratch — the edit block is the contract.\n"
        f"- The committed spec must include the ``{_PROMPT_DIFF_OPEN}`` ... "
        f"``{_PROMPT_DIFF_CLOSE}`` block as its audit trail.\n"
        f"- Output is the edited spec written back to `{target_path}`, "
        f"committed and pushed by the backend (run id `{run_id_placeholder}`).\n"
    )
    return "\n\n".join(parts)


def build_spec_author_goal_text(payload: dict, run_id_placeholder: str) -> str:
    """Compose the spec-authoring prompt the backend will execute."""
    spec_slug = str(payload.get("spec_slug", "")).strip()
    target_path = str(payload.get("target_path", "")).strip()
    trigger = str(payload.get("trigger_source", "")).strip()
    task_phase = str(payload.get("task_phase", "")).strip()
    seed_text = str(payload.get("seed_text") or "").strip()
    ctx = payload.get("context_bundle") or {}

    if task_phase:
        return build_phase_advance_goal_text(
            spec_slug=spec_slug,
            target_path=target_path,
            task_phase=task_phase,
            seed_text=seed_text,
            ctx=ctx if isinstance(ctx, dict) else {},
            run_id_placeholder=run_id_placeholder,
        )

    parts: list[str] = []
    parts.append(
        f"# Spec: {spec_slug}\n\n"
        f"Write a focused improvement-campaign spec at `{target_path}` in this "
        f"repository (`OperationsCenter`). The spec drives a multi-task Plane "
        f"campaign; keep its goals concrete and bounded."
    )
    parts.append(
        "## Required output\n"
        f"Create exactly one file: `{target_path}`. Do not modify any other file.\n\n"
        f"The first line of the file MUST be an HTML comment recording provenance:\n"
        f"`<!-- generated_by_run: {run_id_placeholder} -->`\n\n"
        "Then a YAML front-matter block in this exact format:\n"
        "```\n"
        "---\n"
        "campaign_id: <UUID v4 you generate>\n"
        f"slug: {spec_slug}\n"
        "phases:\n"
        "  - implement\n"
        "  - test\n"
        "  - improve\n"
        "repos:\n"
        "  - <one repo from the Available Repos list below>\n"
        "area_keywords:\n"
        "  - <directory prefix or topic keyword>\n"
        "status: active\n"
        "created_at: <ISO 8601 UTC timestamp>\n"
        "---\n"
        "```\n\n"
        "Then markdown sections:\n"
        "- `## Overview` (2-3 sentences)\n"
        "- `## Goals` (numbered list of 2-4 concrete, bounded tasks; each completable "
        "in one executor run under 1 hour)\n"
        "- `## Constraints` (approach decisions, allowed paths, things to avoid)\n"
        "- `## Success Criteria` (how to know it is done)\n"
    )
    repos = ctx.get("recent_git_log_repos") or {}
    if isinstance(repos, dict) and repos:
        parts.append("## Available Repos\n" + "\n".join(f"- {r}" for r in sorted(repos)))
    if seed_text:
        parts.append(f"## Operator Direction\n{seed_text}")
    if isinstance(repos, dict):
        for repo_key, log_text in repos.items():
            if log_text:
                parts.append(f"## Recent Git Activity ({repo_key})\n```\n{log_text}\n```")
    existing = ctx.get("existing_specs") or []
    if existing:
        parts.append(
            "## Existing Specs (do not duplicate)\n" + "\n".join(f"- {s}" for s in existing)
        )
    snap = ctx.get("board_snapshot") or {}
    if isinstance(snap, dict) and snap:
        parts.append(
            "## Board Summary\n"
            f"- ready: {snap.get('ready', '?')}\n"
            f"- running: {snap.get('running', '?')}\n"
            f"- drained: {snap.get('drained', '?')}\n"
            f"- trigger_source: {trigger}\n"
        )
    parts.append(
        "## Boundaries\n"
        f"- Touch exactly one file: `{target_path}`.\n"
        "- Do not modify any other repo content.\n"
        "- Pick exactly one repo for the `repos:` field from Available Repos.\n"
        "- Prefer 2-4 small goals over one large one.\n"
    )
    return "\n\n".join(parts)
