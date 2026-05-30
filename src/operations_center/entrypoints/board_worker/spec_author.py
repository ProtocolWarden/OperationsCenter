# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Spec-author task processing (ADR 0007 Phase C)."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from ._text import summarize_prompt_diff_block
from .labels import STATE_DONE, add_label
from .outcomes import fail_task, handle_failure

logger = logging.getLogger(__name__)

# Host repo for spec files. spec_trigger writes target_path = docs/specs/<slug>.md
# and the backend operates on this repo's workspace clone.
SPEC_AUTHOR_REPO_KEY = "OperationsCenter"

# Timeout for an LLM-driven spec draft. 8 minutes is generous for the model
# + clone/commit/push overhead while still bounding runaway runs.
SPEC_AUTHOR_TIMEOUT_SECONDS = 480


def process_spec_author(
    *,
    issue: dict,
    role: str,
    settings,
    client,
    config_path: Path,
    goal_text: str,
    repo_key: str,
    clone_url: str,
    base_branch: str,
    spec_slug: str,
    target_path: str,
    trigger_source: str,
    task_phase: str,
    python: str,
    oc_root: Path,
    env: dict,
    short_id: str,
) -> bool:
    """Drive a spec-author task through planning -> ExecutionCoordinator.

    Mirrors dispatch_issue's plan-then-execute shape but with spec-specific
    constraints (allowed_paths=docs/specs/, max_changed_files=1, longer timeout)
    and a spec-author success handler that parses the committed spec file and
    spawns campaign sub-tasks via CampaignBuilder.
    """
    from ._text import task_type_from_kind

    task_id = str(issue["id"])

    with tempfile.TemporaryDirectory(prefix=f"oc-{role}-") as tmpdir:
        tmp = Path(tmpdir)

        forwarded_labels: list[str] = []
        for label in issue.get("labels", []):
            name = (label.get("name", "") if isinstance(label, dict) else str(label)).strip()
            if name.lower().startswith("source:"):
                forwarded_labels.append(name)

        plan_cmd = [
            python, "-m", "operations_center.entrypoints.worker.main",
            "--goal",               goal_text,
            "--task-type",          task_type_from_kind("spec-author"),
            "--execution-mode",     "goal",
            "--repo-key",           repo_key,
            "--clone-url",          clone_url,
            "--base-branch",        base_branch,
            "--project-id",         settings.plane.project_id,
            "--task-id",            task_id,
            "--timeout-seconds",    str(SPEC_AUTHOR_TIMEOUT_SECONDS),
            "--max-changed-files",  "1",
            "--allowed-path",       "docs/specs/",
        ]
        for lbl in forwarded_labels:
            plan_cmd.extend(["--label", lbl])

        plan_proc = subprocess.run(
            plan_cmd, cwd=oc_root, env=env, capture_output=True, text=True,
        )
        try:
            bundle = json.loads(plan_proc.stdout)
        except Exception:
            logger.error(
                "board_worker[%s]: spec-author planning produced no JSON for task_id=%s\n%s",
                role, task_id, plan_proc.stderr.strip() or plan_proc.stdout.strip(),
            )
            fail_task(client, task_id, role, "spec-author planning produced no JSON output")
            return False

        if plan_proc.returncode != 0:
            msg = bundle.get("message", "unknown planning error")
            logger.error(
                "board_worker[%s]: spec-author planning failed task_id=%s — %s",
                role, task_id, msg,
            )
            fail_task(client, task_id, role, f"spec-author planning failed: {msg}")
            return False

        bundle_file = tmp / "bundle.json"
        bundle_file.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        config_file = tmp / "ops.yaml"
        shutil.copy(config_path, config_file)
        workspace = tmp / "workspace"
        workspace.mkdir()
        result_file = tmp / "result.json"

        source_tag = f"board_worker_spec_author|spec_slug={spec_slug}|trigger={trigger_source}"
        exec_cmd = [
            python, "-m", "operations_center.entrypoints.execute.main",
            "--config",         str(config_file),
            "--bundle",         str(bundle_file),
            "--workspace-path", str(workspace),
            "--task-branch",    f"spec-author/{short_id}",
            "--output",         str(result_file),
            "--source",         source_tag,
        ]
        subprocess.run(exec_cmd, cwd=oc_root, env=env, capture_output=True, text=True)

        if not result_file.exists():
            logger.error(
                "board_worker[%s]: spec-author execute produced no result task_id=%s",
                role, task_id,
            )
            fail_task(client, task_id, role, "spec-author execute produced no result file")
            return False

        try:
            outcome = json.loads(result_file.read_text(encoding="utf-8") or "{}")
        except Exception as exc:
            fail_task(client, task_id, role, f"spec-author result.json parse failed: {exc}")
            return False

        result  = outcome.get("result", {})
        success = result.get("success", False)
        run_id  = result.get("run_id", "")

        if success:
            logger.info(
                "board_worker[spec-author]: task_id=%s succeeded run_id=%s spec_slug=%s",
                task_id, run_id, spec_slug,
            )
            handle_spec_author_success(
                client=client, issue=issue, settings=settings,
                workspace=workspace, target_path=target_path,
                spec_slug=spec_slug, run_id=run_id, task_phase=task_phase,
            )
            return True
        else:
            handle_failure(client, issue, role, "spec-author", result, settings)
            return False


def handle_spec_author_success(
    *,
    client,
    issue: dict,
    settings,
    workspace: Path,
    target_path: str,
    spec_slug: str,
    run_id: str,
    task_phase: str,
) -> None:
    """Post-success: parse committed spec, spawn campaign sub-tasks, Done.

    Phase-advance case (task_phase set): spec already exists, campaign tasks
    already exist; just transition Done with a comment.
    """
    task_id = str(issue["id"])

    if task_phase:
        edit_count, edit_parse_note = summarize_prompt_diff_block(
            workspace=workspace, target_path=target_path,
        )
        try:
            client.transition_issue(task_id, STATE_DONE)
            client.comment_issue(
                task_id,
                f"spec-author (phase-advance, task_phase={task_phase}) complete — "
                f"spec rewritten at {target_path} (run_id={run_id}).",
            )
        except Exception as exc:
            logger.warning(
                "board_worker[spec-author]: phase-advance Done transition failed task_id=%s — %s",
                task_id, exc,
            )
        logger.info(
            "board_worker[spec-author]: phase-advance task_id=%s edit_block=%s",
            task_id,
            edit_parse_note if edit_count is None else f"{edit_count} edits ({edit_parse_note})",
        )
        return

    spec_path = workspace / target_path
    if not spec_path.exists():
        logger.warning(
            "board_worker[spec-author]: spec file missing at %s after success run_id=%s",
            spec_path, run_id,
        )
        try:
            client.transition_issue(task_id, STATE_DONE)
            client.comment_issue(
                task_id,
                f"spec-author run succeeded (run_id={run_id}) but expected file "
                f"{target_path} not found in workspace — no campaign sub-tasks created.",
            )
        except Exception as exc:
            logger.warning(
                "board_worker[spec-author]: Done transition failed task_id=%s — %s",
                task_id, exc,
            )
        return

    try:
        spec_text = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("board_worker[spec-author]: read failed task_id=%s — %s", task_id, exc)
        spec_text = ""

    created_ids: list[str] = []
    try:
        from operations_center.spec_author.campaign_builder import CampaignBuilder
        from operations_center.spec_author.models import SpecFrontMatter
        try:
            fm       = SpecFrontMatter.from_spec_text(spec_text)
            repo_key = fm.repos[0] if fm.repos else SPEC_AUTHOR_REPO_KEY
        except Exception:
            repo_key = SPEC_AUTHOR_REPO_KEY
        repo_cfg    = settings.repos.get(repo_key)
        base_branch = (
            (repo_cfg.sandbox_base_branch if repo_cfg and repo_cfg.sandbox_base_branch else None)
            or (repo_cfg.default_branch if repo_cfg else "main")
        )
        builder     = CampaignBuilder(client=client, project_id=settings.plane.project_id)
        created_ids = builder.build(spec_text=spec_text, repo_key=repo_key, base_branch=base_branch)
    except Exception as exc:
        logger.warning(
            "board_worker[spec-author]: campaign build failed task_id=%s — %s", task_id, exc,
        )

    if run_id and created_ids:
        try:
            all_issues = client.list_issues()
            by_id = {str(i.get("id", "")): i for i in all_issues}
            for new_id in created_ids:
                iss = by_id.get(new_id)
                if iss is not None:
                    add_label(client, iss, f"parent_run: {run_id}")
        except Exception as exc:
            logger.debug(
                "board_worker[spec-author]: parent_run label tagging failed — %s", exc,
            )

    try:
        client.transition_issue(task_id, STATE_DONE)
        if created_ids:
            client.comment_issue(
                task_id,
                f"spec-author complete (run_id={run_id}) — wrote {target_path} and "
                f"created {len(created_ids)} campaign task(s): "
                + ", ".join(f"#{i}" for i in created_ids),
            )
        else:
            client.comment_issue(
                task_id,
                f"spec-author complete (run_id={run_id}) — wrote {target_path} but "
                "campaign-task creation produced no children (parse failed or empty goals).",
            )
    except Exception as exc:
        logger.warning(
            "board_worker[spec-author]: post-success transition failed task_id=%s — %s",
            task_id, exc,
        )
