# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Task dispatch — drive a claimed Plane issue through plan → execute → outcome."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from operations_center.injection import wrap_untrusted_goal

from ._subprocess import build_allowlist_env, git_token_passthrough, run_executor
from ._subprocess import is_transient_failure, persist_failure_diagnostics, venv_python
from .wheelhouse import provision_env
from ._text import append_rejection_patterns, desc_text, extract_goal, task_type_from_kind
from .labels import GITHUB_DIR, add_label, label_value
from .outcomes import (
    fail_task,
    handle_failure,
    handle_success,
    read_improve_output,
    run_ci_loop,
)
from .spec_author import SPEC_AUTHOR_REPO_KEY, process_spec_author

logger = logging.getLogger(__name__)


def dispatch_issue(
    issue: dict,
    role: str,
    config_path: Path,
    settings: Any,
    client: Any,
) -> bool:
    """Drive one claimed Plane issue through planning → execution.

    Transitions board state and creates follow-ups on completion.
    Returns True on success.
    """
    task_id = str(issue["id"])
    labels = issue.get("labels", [])
    repo_key = label_value(labels, "repo")
    task_kind = label_value(labels, "task-kind")
    title = issue.get("name", "Untitled")

    description = desc_text(issue)

    # ── Spec-author short-circuit ─────────────────────────────────────────────
    if task_kind == "spec-author":
        return _dispatch_spec_author(
            issue=issue,
            role=role,
            settings=settings,
            client=client,
            config_path=config_path,
            description=description,
            labels=labels,
            task_id=task_id,
        )

    # ── Goal text + mode ──────────────────────────────────────────────────────
    # The issue title/body is attacker-controllable. Fence it BEFORE appending any
    # trusted scaffolding, so embedded meta-instructions reach the token-holding
    # backend as data, not a control channel (see operations_center.injection).
    goal_text = wrap_untrusted_goal(extract_goal(description, title))
    goal_text = append_rejection_patterns(goal_text, repo_key)

    if role == "improve":
        goal_text = _append_improve_output_prompt(goal_text)
    else:
        # First-pass depth: implement + self-verify before the PR opens.
        goal_text = _append_definition_of_done(goal_text)

    execution_mode = task_kind  # historically a no-op ternary; kept flat for C29

    repo_cfg = settings.repos.get(repo_key)
    repo_path = _repo_local_path(settings, repo_key)
    clone_url = repo_cfg.clone_url if repo_cfg else f"file://{repo_path}"
    base_branch = (
        label_value(labels, "base-branch")
        or (repo_cfg.sandbox_base_branch if repo_cfg and repo_cfg.sandbox_base_branch else None)
        or (repo_cfg.default_branch if repo_cfg else "main")
    )

    oc_root = Path(__file__).resolve().parents[4]
    python = venv_python(oc_root)
    env = build_allowlist_env(oc_root, passthrough=git_token_passthrough(settings, repo_cfg))
    env.update(provision_env(repo_key, repo_path, python_bin=python))  # offline deps
    short_id = task_id[:8]

    logger.info(
        "board_worker[%s]: processing task_id=%s repo=%s kind=%s",
        role,
        task_id,
        repo_key,
        task_kind,
    )

    with tempfile.TemporaryDirectory(prefix=f"oc-{role}-") as tmpdir:
        tmp = Path(tmpdir)

        # ── Step 1: Planning ──────────────────────────────────────────────────
        forwarded_labels = _build_forwarded_labels(labels, repo_cfg)
        plan_cmd = [
            python,
            "-m",
            "operations_center.entrypoints.worker.main",
            "--goal",
            goal_text,
            "--task-type",
            task_type_from_kind(task_kind),
            "--execution-mode",
            execution_mode,
            "--repo-key",
            repo_key,
            "--clone-url",
            clone_url,
            "--base-branch",
            base_branch,
            "--project-id",
            settings.plane.project_id,
            "--task-id",
            task_id,
            "--timeout-seconds",
            str(settings.team_executor.timeout_seconds),
        ]
        for lbl in forwarded_labels:
            plan_cmd.extend(["--label", lbl])

        plan_proc = subprocess.run(
            plan_cmd,
            cwd=oc_root,
            env=env,
            capture_output=True,
            text=True,
        )

        try:
            bundle = json.loads(plan_proc.stdout)
        except Exception:
            logger.error(
                "board_worker[%s]: planning produced no JSON for task_id=%s\n%s",
                role,
                task_id,
                plan_proc.stderr.strip() or plan_proc.stdout.strip(),
            )
            fail_task(client, task_id, role, "planning produced no JSON output")
            return False

        if plan_proc.returncode != 0:
            msg = bundle.get("message", "unknown planning error")
            logger.error(
                "board_worker[%s]: planning failed for task_id=%s — %s",
                role,
                task_id,
                msg,
            )
            fail_task(client, task_id, role, f"planning failed: {msg}")
            return False

        # ── Step 2: Execution ─────────────────────────────────────────────────
        bundle_file = tmp / "bundle.json"
        bundle_file.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        config_file = tmp / "ops.yaml"
        shutil.copy(config_path, config_file)

        # CI branch: delegate improve_campaign with a ContinuousImprovementSpec.
        ci_spec_raw = bundle.get("proposal", {}).get("continuous_improvement")
        if ci_spec_raw and execution_mode == "improve_campaign":
            return run_ci_loop(
                ci_spec_raw=ci_spec_raw,
                client=client,
                issue=issue,
                role=role,
                task_kind=task_kind,
                task_id=task_id,
                repo_key=repo_key,
                settings=settings,
                python=python,
                oc_root=oc_root,
                env=env,
                bundle_file=bundle_file,
                config_file=config_file,
                tmp=tmp,
                short_id=short_id,
            )

        workspace = tmp / "workspace"
        workspace.mkdir()
        result_file = tmp / "result.json"

        exec_cmd = [
            python,
            "-m",
            "operations_center.entrypoints.execute.main",
            "--config",
            str(config_file),
            "--bundle",
            str(bundle_file),
            "--workspace-path",
            str(workspace),
            "--task-branch",
            f"{role}/{short_id}",
            "--output",
            str(result_file),
            "--source",
            f"board_worker_{role}",
        ]

        proc = run_executor(exec_cmd, oc_root=oc_root, rw_root=tmp, workspace=workspace, env=env)

        if not result_file.exists():
            logger.error(
                "board_worker[%s]: execute produced no result for task_id=%s",
                role,
                task_id,
            )
            fail_task(client, task_id, role, "execute produced no result file")
            return False

        result_text = result_file.read_text(encoding="utf-8").strip()
        if not result_text:
            rc = proc.returncode
            logger.error(
                "board_worker[%s]: empty result.json for task_id=%s (returncode=%s) "
                "— treating as executor kill",
                role,
                task_id,
                rc,
            )
            add_label(client, issue, f"executor-exit-code: {rc}")
            add_label(client, issue, "executor-signal: SIGKILL")
            from .labels import increment_retry_count

            increment_retry_count(client, issue)
            fail_task(
                client,
                task_id,
                role,
                f"execute wrote empty result.json (returncode={rc}) — treated as executor kill",
            )
            return False

        outcome = json.loads(result_text)
        result = outcome.get("result", {})
        success = result.get("success", False)
        status = result.get("status", "unknown")
        needs_verification = result.get("needs_verification", False)

        # D1: transient retry — one attempt on network blips.
        if not success and is_transient_failure(result) and not outcome.get("retried"):
            logger.info(
                "board_worker[%s]: task_id=%s transient failure (%s) — retrying once",
                role,
                task_id,
                result.get("failure_reason", "")[:80],
            )
            shutil.rmtree(workspace, ignore_errors=True)
            workspace.mkdir()
            retry_result_file = tmp / "result.retry.json"
            retry_cmd = list(exec_cmd)
            retry_cmd[retry_cmd.index("--output") + 1] = str(retry_result_file)
            retry_cmd[retry_cmd.index("--source") + 1] = f"board_worker_{role}_retry"
            proc = run_executor(
                retry_cmd, oc_root=oc_root, rw_root=tmp, workspace=workspace, env=env
            )
            if retry_result_file.exists():
                outcome = json.loads(retry_result_file.read_text(encoding="utf-8"))
                outcome["retried"] = True
                result = outcome.get("result", {})
                success = result.get("success", False)
                status = result.get("status", "unknown")
                needs_verification = result.get("needs_verification", False)
                result_file.write_text(
                    json.dumps(outcome, ensure_ascii=False),
                    encoding="utf-8",
                )

        improve_suggestions: list[dict] = []
        if role == "improve" and success:
            improve_suggestions = read_improve_output(workspace)

        scope_files: list[str] = []
        scope_file = workspace / "scope-too-wide.json"
        if scope_file.exists():
            try:
                scope_files = json.loads(scope_file.read_text(encoding="utf-8")).get("files") or []
            except Exception:
                pass

        scope_too_wide = (
            success
            and result.get("branch_pushed") is False
            and result.get("failure_category") == "scope_too_wide"
        )

        if success and not scope_too_wide:
            logger.info(
                "board_worker[%s]: task_id=%s completed status=%s",
                role,
                task_id,
                status,
            )
            handle_success(
                client,
                issue,
                role,
                task_kind,
                needs_verification,
                settings,
                improve_suggestions=improve_suggestions,
                pr_url=result.get("pull_request_url") or None,
            )
        else:
            log_reason = "scope_too_wide" if scope_too_wide else status
            logger.warning(
                "board_worker[%s]: task_id=%s failed status=%s",
                role,
                task_id,
                log_reason,
            )
            if not scope_too_wide:
                persist_failure_diagnostics(result, oc_root, role, short_id, proc, result_text)
            handle_failure(
                client,
                issue,
                role,
                task_kind,
                result,
                settings,
                scope_files=scope_files if scope_too_wide else [],
            )

        return success and not scope_too_wide


# ── Internal helpers ──────────────────────────────────────────────────────────


def _repo_local_path(settings, repo_key: str) -> str:
    repo = settings.repos.get(repo_key)
    if repo and repo.local_path:
        return repo.local_path
    return str(GITHUB_DIR / repo_key)


def _append_definition_of_done(goal_text: str) -> str:
    """Append an explicit definition-of-done so the first pass ships a complete,
    self-verified change rather than a partial one the review loop must finish.

    The downstream review loop only merges on an LGTM verdict and re-queues PRs
    it can't get clean, so a thorough first pass directly reduces fix cycles."""
    return (
        f"{goal_text}\n\n"
        "## Definition of done (complete ALL before finishing)\n"
        "1. Complete the task in its ENTIRETY — every acceptance criterion and every\n"
        "   file the task implies (implementation, tests, and docs as applicable). Do\n"
        "   not leave TODOs, stubs, or 'follow-up' gaps; a partial change is rejected\n"
        "   in review.\n"
        "2. Add or update tests/checks that prove the work is correct.\n"
        "3. Run the repository's test suite and linters/formatters and make them\n"
        "   pass locally. If anything fails, fix it before finishing — do not hand\n"
        "   off a red build.\n"
        "4. Only consider the task done when the full change is in place AND verified\n"
        "   green. The PR you open should be mergeable as-is.\n"
    )


def _append_improve_output_prompt(goal_text: str) -> str:
    return (
        f"{goal_text}\n\n"
        "## Output\n"
        "Write your analysis to `improve-output.json` in the project root with:\n"
        "```json\n"
        "{\n"
        '  "summary": "1-2 sentence high-level finding",\n'
        '  "suggestions": [\n'
        '    {"title": "concrete actionable change",\n'
        '      "rationale": "why this matters",\n'
        '      "files": ["path/to/file"],\n'
        '      "complexity": "small|medium|large"}\n'
        "  ]\n"
        "}\n"
        "```\n"
        "Each suggestion should be small enough to implement in a focused PR "
        "(complexity:small ≈ <50 LOC, medium ≈ <200 LOC, large flagged for split). "
        "Limit to 5 suggestions; pick the highest-impact ones."
    )


def _build_forwarded_labels(labels: list, repo_cfg) -> list[str]:
    """Build the label list to forward to the planning subprocess.

    Filters source labels based on the repo's require_explicit_approval setting.
    """
    explicit_required = bool(getattr(repo_cfg, "require_explicit_approval", False))
    forwarded: list[str] = []
    for label in labels:
        name = (label.get("name", "") if isinstance(label, dict) else str(label)).strip()
        low = name.lower()
        if low == "review_required":
            forwarded.append(name)
            continue
        if low.startswith("source:"):
            if explicit_required and low in {
                "source: autonomy",
                "source: spec-campaign",
                "source: board_worker",
            }:
                continue
            forwarded.append(name)
    if explicit_required:
        forwarded.append("review_required")
    return forwarded


def _dispatch_spec_author(
    *,
    issue: dict,
    role: str,
    settings,
    client,
    config_path: Path,
    description: str,
    labels: list,
    task_id: str,
) -> bool:
    from ._text import build_spec_author_goal_text, parse_spec_author_payload

    spec_payload = parse_spec_author_payload(description)
    if spec_payload is None:
        logger.error(
            "board_worker[%s]: spec-author task_id=%s has no parseable YAML payload",
            role,
            task_id,
        )
        fail_task(client, task_id, role, "spec-author payload missing or malformed YAML block")
        return False

    repo_key = SPEC_AUTHOR_REPO_KEY
    run_id_placeholder = "{{RUN_ID}}"
    goal_text = build_spec_author_goal_text(spec_payload, run_id_placeholder)
    target_path = str(spec_payload.get("target_path", "")).strip()
    spec_slug = str(spec_payload.get("spec_slug", "")).strip()
    trigger_source = str(spec_payload.get("trigger_source", "")).strip()

    repo_cfg = settings.repos.get(repo_key)
    clone_url = repo_cfg.clone_url if repo_cfg else f"file://{_repo_local_path(settings, repo_key)}"
    base_branch = (
        label_value(labels, "base-branch")
        or (repo_cfg.sandbox_base_branch if repo_cfg and repo_cfg.sandbox_base_branch else None)
        or (repo_cfg.default_branch if repo_cfg else "main")
    )
    oc_root = Path(__file__).resolve().parents[4]
    python = venv_python(oc_root)
    env = build_allowlist_env(oc_root, passthrough=git_token_passthrough(settings, repo_cfg))
    short_id = task_id[:8]

    logger.info(
        "board_worker[%s]: processing spec-author task_id=%s spec_slug=%s target=%s trigger=%s",
        role,
        task_id,
        spec_slug,
        target_path,
        trigger_source,
    )

    return process_spec_author(
        issue=issue,
        role=role,
        settings=settings,
        client=client,
        config_path=config_path,
        goal_text=goal_text,
        repo_key=repo_key,
        clone_url=clone_url,
        base_branch=base_branch,
        spec_slug=spec_slug,
        target_path=target_path,
        trigger_source=trigger_source,
        task_phase=str(spec_payload.get("task_phase", "")).strip(),
        python=python,
        oc_root=oc_root,
        env=env,
        short_id=short_id,
    )
