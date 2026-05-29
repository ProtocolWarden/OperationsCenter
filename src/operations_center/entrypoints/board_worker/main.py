# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Board worker — Plane-polling watcher for goal, test, and improve roles.

Polls the Plane board for "Ready for AI" issues with a matching task-kind label,
claims one, drives the planning → execution pipeline (identical to intake), then
transitions board state and creates follow-up tasks per the lifecycle contract.

Each role runs as a separate process. The shell launcher passes:
    --config              path to operations_center.local.yaml
    --role                goal | test | improve
    --poll-interval-seconds N
    --status-dir          directory for heartbeat_{role}.json

Task-kind label mapping:
    goal    → task-kind: goal
    test    → task-kind: test  OR  task-kind: test_campaign
    improve → task-kind: improve  OR  task-kind: improve_campaign

Follow-up creation per lifecycle contract:
    goal success + verification needed → creates task-kind: test (Ready for AI)
    goal success + no verification     → transitions to Review (or Done)
    goal failure                       → transitions to Blocked
    test success                       → transitions to Done
    test failure                       → creates task-kind: goal (Ready for AI)
    improve any outcome                → creates bounded follow-up or Blocked
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_GITHUB_DIR = Path.home() / "Documents" / "GitHub"

# Plane states
_STATE_READY      = "Ready for AI"
_STATE_RUNNING    = "Running"
_STATE_DONE       = "Done"
_STATE_BLOCKED    = "Blocked"
_STATE_REVIEW     = "In Review"

# Lifecycle marker — applied to a task whose work has been delegated to
# spawned children (scope-split, future decomposition modes). Any service
# that re-processes Blocked tasks (spec_director's blocked-rewrite, the
# auto-promote loop, future recovery services) must skip tasks carrying
# this label so we don't generate ghost work on a meta-task whose real
# work is already happening downstream.
_LIFECYCLE_EXPANDED = "lifecycle: expanded"

# task-kind labels claimed per role
# ADR 0007 Phase C: `spec-author` is its own role — distinct from goal/test/improve
# because the planning prompt + _handle_success branch are spec-specific
# (parse spec front-matter, spawn campaign sub-tasks). Folding it into `goal`
# would force the goal planner to special-case spec_slug parsing.
_ROLE_KINDS: dict[str, list[str]] = {
    "goal":        ["goal"],
    "test":        ["test", "test_campaign"],
    "improve":     ["improve", "improve_campaign"],
    "spec-author": ["spec-author"],
}


# ── Plane helpers ─────────────────────────────────────────────────────────────

def _label_value(labels: list, prefix: str) -> str:
    """Extract value from a 'prefix: value' label, or ''."""
    for lab in labels:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        if name.lower().startswith(prefix.lower() + ":"):
            return name.split(":", 1)[1].strip()
    return ""


def _has_label(labels: list, value: str) -> bool:
    for lab in labels:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        if name.lower() == value.lower():
            return True
    return False


def _load_settings(config_path: Path):
    from operations_center.config import load_settings
    return load_settings(config_path)


def _plane_client(settings):
    from operations_center.adapters.plane import PlaneClient
    return PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )


def _repo_local_path(settings, repo_key: str) -> str:
    repo = settings.repos.get(repo_key)
    if repo and repo.local_path:
        return repo.local_path
    return str(_GITHUB_DIR / repo_key)


def _claim_next(client, role: str, settings) -> dict | None:
    """
    Find the oldest Ready-for-AI issue matching this role's task-kinds and a
    known repo. Immediately transition to Running to claim it.
    Returns the raw Plane issue dict, or None if nothing is available.
    """
    kinds = _ROLE_KINDS[role]
    managed_repos = set(settings.repos.keys())

    try:
        issues = client.list_issues()
    except Exception:
        logger.warning("board_worker[%s]: failed to list issues", role)
        return None

    # Daily-execution counter per repo: count tasks the worker has touched
    # today (anything currently in Running / In Review / Done / Blocked
    # whose updated_at is within the last 24h). Used to enforce
    # RepoSettings.max_daily_executions.
    _exec_today: dict[str, int] = {}
    _now_utc = datetime.now(UTC)
    _touched_states = {"running", "in review", "done", "blocked"}
    for issue in issues:
        st = issue.get("state")
        st_name = (st.get("name", "") if isinstance(st, dict) else str(st or "")).strip().lower()
        if st_name not in _touched_states:
            continue
        ts_raw = issue.get("updated_at") or ""
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except Exception:
            continue
        if (_now_utc - ts).total_seconds() > 86400:
            continue
        rk = _label_value(issue.get("labels", []), "repo")
        if rk:
            _exec_today[rk] = _exec_today.get(rk, 0) + 1

    candidates = []
    for issue in issues:
        state_obj = issue.get("state")
        state_name = (state_obj.get("name", "") if isinstance(state_obj, dict) else str(state_obj or "")).strip()
        if state_name != _STATE_READY:
            continue
        labels = issue.get("labels", [])
        task_kind = _label_value(labels, "task-kind")
        if task_kind not in kinds:
            continue
        repo_key = _label_value(labels, "repo")
        # ADR 0007 Phase C: spec-author tasks target the OC host repo and
        # carry no `repo:` label (spec_trigger leaves it off — see ADR
        # spec-author payload). Repo is fixed in _process_spec_author.
        if task_kind == "spec-author":
            repo_key = _SPEC_AUTHOR_REPO_KEY
        if repo_key not in managed_repos:
            continue
        # Per-repo quota gate. RepoSettings.max_daily_executions is None /
        # 0 by default (no cap); a positive int enforces a daily ceiling
        # against the count we computed above.
        repo_cfg = settings.repos.get(repo_key)
        cap = getattr(repo_cfg, "max_daily_executions", None) if repo_cfg else None
        if cap and _exec_today.get(repo_key, 0) >= int(cap):
            logger.info(
                "board_worker[%s]: skipping repo %s — daily quota %d reached (today=%d)",
                role, repo_key, cap, _exec_today[repo_key],
            )
            continue
        candidates.append(issue)

    if not candidates:
        return None

    # Priority order:
    #   1. improve-suggestions first — they represent recent partially-complete
    #      analysis that was just identified as worth doing. Picking
    #      them up while the context is fresh keeps related changes coherent
    #      and prevents stale suggestions piling up at the bottom of the queue.
    #   2. then by Plane priority field if set (urgent, high, medium, low, none)
    #   3. then by created_at — oldest first as a stable tiebreaker.
    _PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
    def _sort_key(issue: dict) -> tuple:
        labs = [
            (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
            for lab in issue.get("labels", [])
        ]
        is_improve_suggestion = 0 if "source: improve-suggestion" in labs else 1
        plane_priority = str(issue.get("priority") or "none").lower()
        plane_rank = _PRIORITY_ORDER.get(plane_priority, 4)
        return (is_improve_suggestion, plane_rank, issue.get("created_at", ""))

    candidates.sort(key=_sort_key)
    issue = candidates[0]

    # Ghost-work guard G7: skip tasks whose goal text is too thin for the
    # executor to do anything meaningful. A 16-minute run on an empty description is pure
    # quota-burn. We mark the task Blocked with a clear reason so the operator
    # (or spec_director) can fill in details and re-promote.
    desc = issue.get("description") or issue.get("description_stripped") or ""
    title = issue.get("name", "")
    # ADR 0007 Phase C: spec-author tasks carry their intent in a YAML payload,
    # not a `## Goal` block. The thin-goal-text guard would reject them on
    # title length; skip the guard since the payload body is always present
    # (spec_trigger fails the create if it isn't).
    candidate_kind = _label_value(issue.get("labels", []), "task-kind")
    candidate_goal = _extract_goal(desc, title).strip()
    _MIN_GOAL_TEXT_CHARS = 40
    if candidate_kind != "spec-author" and len(candidate_goal) < _MIN_GOAL_TEXT_CHARS:
        try:
            client.transition_issue(str(issue["id"]), _STATE_BLOCKED)
            client.comment_issue(
                str(issue["id"]),
                f"board_worker[{role}] refused to claim — goal text too thin "
                f"({len(candidate_goal)} chars; minimum {_MIN_GOAL_TEXT_CHARS}). "
                "Add concrete description and re-promote to Ready for AI.",
            )
        except Exception as exc:
            logger.warning("board_worker[%s]: empty-goal block failed task_id=%s — %s",
                            role, issue.get("id"), exc)
        logger.info(
            "board_worker[%s]: refused thin task_id=%s title=%r",
            role, issue.get("id"), title,
        )
        return None
    task_id = str(issue["id"])

    # P3 OPEN_PR_GATE (ADR 0009): refuse to claim goal tasks while an open PR
    # exists for the same repo — prevents simultaneous PRs that conflict on merge.
    if role == "goal":
        _gate_labels   = issue.get("labels", [])
        _gate_repo_key = _label_value(_gate_labels, "repo")
        _gate_repo_cfg = settings.repos.get(_gate_repo_key) if _gate_repo_key else None
        _gate_clone_url = _gate_repo_cfg.clone_url if _gate_repo_cfg else None
        _gh_token = settings.git_token()
        if _gh_token and _gate_clone_url:
            try:
                from operations_center.adapters.github_pr import GitHubPRClient
                _gh = GitHubPRClient(_gh_token)
                _owner, _repo_name = GitHubPRClient.owner_repo_from_clone_url(_gate_clone_url)
                _open_prs = _gh.list_open_prs(_owner, _repo_name)
                if _open_prs:
                    _pr_nums = [pr.get("number") for pr in _open_prs[:5]]
                    logger.info(
                        "board_worker[goal]: OPEN_PR_GATE — repo=%s has %d open PR(s) %s; "
                        "skipping task_id=%s until merged",
                        _gate_repo_key, len(_open_prs), _pr_nums, task_id,
                    )
                    _add_label(client, issue, "OPEN_PR_GATE")
                    return None
            except Exception as _gate_exc:
                logger.warning(
                    "board_worker[goal]: OPEN_PR_GATE check failed repo=%s — %s; proceeding",
                    _gate_repo_key, _gate_exc,
                )

    try:
        client.transition_issue(task_id, _STATE_RUNNING)
        logger.info("board_worker[%s]: claimed task_id=%s title=%r", role, task_id, issue.get("name", ""))
    except Exception as exc:
        logger.warning("board_worker[%s]: failed to claim task_id=%s — %s", role, task_id, exc)
        return None

    return issue


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _build_env(oc_root: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(oc_root / "src")
    return env


def _venv_python(oc_root: Path) -> str:
    p = oc_root / ".venv" / "bin" / "python"
    return str(p) if p.exists() else "python3"


_TRANSIENT_CATEGORIES = {"backend_error", "timeout"}
_TRANSIENT_REASON_PATTERNS = (
    "connection refused", "connection reset", "timed out", "timeout",
    "502", "503", "504", "bad gateway", "gateway timeout", "service unavailable",
    "remote disconnected", "network is unreachable", "temporary failure",
)


def _is_transient_failure(result: dict) -> bool:
    """Return True when an execution failure looks like a transient blip.

    Conservative match: requires category to be backend_error or timeout
    AND the reason text to contain a network-shaped phrase. Avoids
    over-retrying genuine bugs (which surface as backend_error too but
    with a Python traceback in the reason).
    """
    cat = (result.get("failure_category") or "").lower()
    if cat not in _TRANSIENT_CATEGORIES:
        return False
    reason = (result.get("failure_reason") or "").lower()
    return any(p in reason for p in _TRANSIENT_REASON_PATTERNS)


def _process_issue(issue: dict, role: str, config_path: Path, settings, client) -> bool:
    """
    Drive one claimed Plane issue through planning → execution.
    Transitions board state and creates follow-ups on completion.
    Returns True on success.
    """
    task_id   = str(issue["id"])
    title     = issue.get("name", "Untitled")
    labels    = issue.get("labels", [])
    repo_key  = _label_value(labels, "repo")
    task_kind = _label_value(labels, "task-kind")

    description = issue.get("description") or issue.get("description_stripped") or issue.get("description_html") or ""

    # ADR 0007 Phase C: spec-author tasks carry a YAML payload (parsed below)
    # rather than a `## Goal` block. The whole path is distinct: planning text
    # is composed from the payload, repo is fixed to OC, scope is one file.
    spec_payload: dict | None = None
    if task_kind == "spec-author":
        spec_payload = _parse_spec_author_payload(description)
        if spec_payload is None:
            logger.error(
                "board_worker[%s]: spec-author task_id=%s has no parseable YAML payload",
                role, task_id,
            )
            _fail_task(client, task_id, role, "spec-author payload missing or malformed YAML block")
            return False

    # Extract goal text from description
    goal_text = _extract_goal(description, title)

    # ADR 0007 Phase C: short-circuit the standard goal/test/improve prompt
    # pipeline for spec-author. We have a YAML payload, not a `## Goal` block
    # to enrich; the spec-author prompt is fully composed below.
    if task_kind == "spec-author" and spec_payload is not None:
        repo_key  = _SPEC_AUTHOR_REPO_KEY
        # ADR 0007 follow-up B: emit the {{RUN_ID}} placeholder in the prompt;
        # the execute subprocess substitutes the real run_id into goal_text
        # before backend dispatch (see ExecutionRequestBuilder.build), so the
        # agent sees the actual id at prompt time and writes the correct
        # provenance comment directly — no post-success rewrite needed.
        run_id_placeholder = "{{RUN_ID}}"
        goal_text = _build_spec_author_goal_text(spec_payload, run_id_placeholder)
        target_path = str(spec_payload.get("target_path", "")).strip()
        spec_slug   = str(spec_payload.get("spec_slug", "")).strip()
        trigger_source = str(spec_payload.get("trigger_source", "")).strip()
        execution_mode = "goal"
        repo_cfg = settings.repos.get(repo_key)
        clone_url = repo_cfg.clone_url if repo_cfg else f"file://{_repo_local_path(settings, repo_key)}"
        base_branch = (
            _label_value(labels, "base-branch")
            or (repo_cfg.sandbox_base_branch if repo_cfg and repo_cfg.sandbox_base_branch else None)
            or (repo_cfg.default_branch if repo_cfg else "main")
        )
        oc_root  = Path(__file__).resolve().parents[4]
        python   = _venv_python(oc_root)
        env      = _build_env(oc_root)
        short_id = task_id[:8]

        logger.info(
            "board_worker[%s]: processing spec-author task_id=%s spec_slug=%s target=%s trigger=%s",
            role, task_id, spec_slug, target_path, trigger_source,
        )

        return _process_spec_author(
            issue=issue, role=role, settings=settings, client=client,
            config_path=config_path, goal_text=goal_text, repo_key=repo_key,
            clone_url=clone_url, base_branch=base_branch, spec_slug=spec_slug,
            target_path=target_path, trigger_source=trigger_source,
            task_phase=str(spec_payload.get("task_phase", "")).strip(),
            python=python, oc_root=oc_root, env=env, short_id=short_id,
        )

    # Rejection-pattern hint: if recent proposals in this repo were rejected
    # for a recurring reason, prepend a "common rejection patterns to avoid"
    # block to the goal text. Best-effort — no hint when the catalog is
    # empty or unreadable. Helps the backend avoid the same mistake twice.
    try:
        from operations_center.quality_alerts import _load_rejection_patterns_for_proposal
        patterns = _load_rejection_patterns_for_proposal(repo_key=repo_key)
        if patterns:
            goal_text = (
                f"{goal_text}\n\n"
                f"## Rejection patterns to avoid\n"
                "Recent proposals in this repo were rejected for these reasons; "
                f"do not repeat them:\n"
                + "\n".join(f"- {p}" for p in patterns[:5])
            )
    except Exception:
        pass

    # Improve mode: emit structured suggestions we can turn into concrete
    # follow-up tasks for the propose lane. Without this prompt, the improve
    # run is a pure-side-effect analysis that produces no downstream
    # signal — the duplicate-PR problem we saw with PR #55/#60.
    if role == "improve":
        goal_text = (
            f"{goal_text}\n\n"
            f"## Output\n"
            f"Write your analysis to `improve-output.json` in the project root with:\n"
            f"```json\n"
            f"{{\n"
            f'  "summary": "1-2 sentence high-level finding",\n'
            f'  "suggestions": [\n'
            f'    {{"title": "concrete actionable change",\n'
            f'      "rationale": "why this matters",\n'
            f'      "files": ["path/to/file"],\n'
            f'      "complexity": "small|medium|large"}}\n'
            f"  ]\n"
            f"}}\n"
            f"```\n"
            "Each suggestion should be small enough to implement in a focused PR "
            "(complexity:small ≈ <50 LOC, medium ≈ <200 LOC, large flagged for split). "
            "Limit to 5 suggestions; pick the highest-impact ones."
        )

    # Derive execution_mode from task_kind (test_campaign → test_campaign, etc.)
    execution_mode = task_kind if task_kind in {"goal", "test_campaign", "improve_campaign"} else task_kind

    repo_path  = _repo_local_path(settings, repo_key)
    repo_cfg   = settings.repos.get(repo_key)
    clone_url  = repo_cfg.clone_url if repo_cfg else f"file://{repo_path}"
    # Precedence for base_branch:
    #   1. explicit per-task override label "base-branch: X"
    #   2. repo's sandbox_base_branch (autonomy work targets staging, not main)
    #   3. repo's default_branch
    base_branch = (
        _label_value(labels, "base-branch")
        or (repo_cfg.sandbox_base_branch if repo_cfg and repo_cfg.sandbox_base_branch else None)
        or (repo_cfg.default_branch if repo_cfg else "main")
    )

    oc_root  = Path(__file__).resolve().parents[4]
    python   = _venv_python(oc_root)
    env      = _build_env(oc_root)
    short_id = task_id[:8]

    logger.info(
        "board_worker[%s]: processing task_id=%s repo=%s kind=%s",
        role, task_id, repo_key, task_kind,
    )

    with tempfile.TemporaryDirectory(prefix=f"oc-{role}-") as tmpdir:
        tmp = Path(tmpdir)

        # ── Step 1: Planning ──────────────────────────────────────────────
        # Forward source labels so the policy engine can recognise pre-authorised
        # lanes (autonomy tier, spec campaigns) and skip its review-by-task-type
        # check. Without this, every goal/improve task gets policy-blocked.
        forwarded_labels: list[str] = []
        # C-K6: when the repo opts in via require_explicit_approval, we DO
        # NOT forward trusted-source labels — every task on that repo must
        # pass the full review gate even if its label set would otherwise
        # qualify for the bypass. Per-repo override of the trust default.
        explicit_required = bool(getattr(repo_cfg, "require_explicit_approval", False))
        for label in labels:
            name = (label.get("name", "") if isinstance(label, dict) else str(label)).strip()
            low = name.lower()
            if low == "review_required":
                forwarded_labels.append(name)
                continue
            if low.startswith("source:"):
                if explicit_required and low in {
                    "source: autonomy", "source: spec-campaign", "source: board_worker",
                }:
                    # Drop the trusted-source label so policy treats the
                    # task as untrusted. The original Plane labels stay on
                    # the issue itself — only the proposal carries the
                    # filtered set.
                    continue
                forwarded_labels.append(name)
        if explicit_required:
            # Tag the proposal so policy explicitly requires review even
            # when no other rule would fire (defence in depth).
            forwarded_labels.append("review_required")

        plan_cmd = [
            python, "-m", "operations_center.entrypoints.worker.main",
            "--goal",             goal_text,
            "--task-type",        _task_type_from_kind(task_kind),
            "--execution-mode",   execution_mode,
            "--repo-key",         repo_key,
            "--clone-url",        clone_url,
            "--base-branch",      base_branch,
            "--project-id",       settings.plane.project_id,
            "--task-id",          task_id,
            "--timeout-seconds",  str(settings.team_executor.timeout_seconds),
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
                "board_worker[%s]: planning produced no JSON for task_id=%s\n%s",
                role, task_id, plan_proc.stderr.strip() or plan_proc.stdout.strip(),
            )
            _fail_task(client, task_id, role, "planning produced no JSON output")
            return False

        if plan_proc.returncode != 0:
            msg = bundle.get("message", "unknown planning error")
            logger.error("board_worker[%s]: planning failed for task_id=%s — %s", role, task_id, msg)
            _fail_task(client, task_id, role, f"planning failed: {msg}")
            return False

        # ── Step 2: Execution ─────────────────────────────────────────────
        bundle_file = tmp / "bundle.json"
        bundle_file.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")

        config_file = tmp / "ops.yaml"
        shutil.copy(config_path, config_file)

        # CI branch: when the proposal carries ContinuousImprovementSpec and
        # the task is an improve_campaign, delegate to the refinement loop.
        ci_spec_raw = bundle.get("proposal", {}).get("continuous_improvement")
        if ci_spec_raw and execution_mode == "improve_campaign":
            return _run_ci_loop(
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
            python, "-m", "operations_center.entrypoints.execute.main",
            "--config",         str(config_file),
            "--bundle",         str(bundle_file),
            "--workspace-path", str(workspace),
            "--task-branch",    f"{role}/{short_id}",
            "--output",         str(result_file),
            "--source",         f"board_worker_{role}",
        ]

        proc = subprocess.run(exec_cmd, cwd=oc_root, env=env, capture_output=True, text=True)

        if not result_file.exists():
            logger.error("board_worker[%s]: execute produced no result for task_id=%s", role, task_id)
            _fail_task(client, task_id, role, "execute produced no result file")
            return False

        result_text = result_file.read_text(encoding="utf-8").strip()
        if not result_text:
            # write_text truncates the file before writing; a SIGKILL between
            # those two operations leaves an empty file. Treat as executor kill
            # so the SIGKILL guard in board_unblock fires correctly.
            rc = proc.returncode
            logger.error(
                "board_worker[%s]: empty result.json for task_id=%s (returncode=%s) — treating as executor kill",
                role, task_id, rc,
            )
            _add_label(client, issue, f"executor-exit-code: {rc}")
            _add_label(client, issue, "executor-signal: SIGKILL")
            _increment_retry_count(client, issue)
            _fail_task(
                client, task_id, role,
                f"execute wrote empty result.json (returncode={rc}) — treated as executor kill",
            )
            return False

        outcome = json.loads(result_text)
        result  = outcome.get("result", {})
        success = result.get("success", False)
        status  = result.get("status", "unknown")
        needs_verification = result.get("needs_verification", False)

        # D1: transient backend retry. Network blips and 502s shouldn't sink a
        # task — they're not authoritative outcomes. Detect by failure
        # category + reason shape; retry once with a fresh workspace before
        # giving up. Capped at 1 to avoid infinite loops.
        if (not success
            and _is_transient_failure(result)
            and not outcome.get("retried")):
            logger.info(
                "board_worker[%s]: task_id=%s transient failure (%s) — retrying once",
                role, task_id, result.get("failure_reason", "")[:80],
            )
            # Fresh workspace for the retry — the previous one may have a
            # half-clone or partial commit state.
            shutil.rmtree(workspace, ignore_errors=True)
            workspace.mkdir()
            retry_result_file = tmp / "result.retry.json"
            retry_cmd = list(exec_cmd)
            retry_cmd[retry_cmd.index("--output") + 1] = str(retry_result_file)
            retry_cmd[retry_cmd.index("--source")  + 1] = f"board_worker_{role}_retry"
            subprocess.run(retry_cmd, cwd=oc_root, env=env, capture_output=True, text=True)
            if retry_result_file.exists():
                outcome = json.loads(retry_result_file.read_text(encoding="utf-8"))
                outcome["retried"] = True
                result  = outcome.get("result", {})
                success = result.get("success", False)
                status  = result.get("status", "unknown")
                needs_verification = result.get("needs_verification", False)
                # Persist the new outcome for downstream readers (artifact
                # writer, observability) — overwrite original so retry is
                # the recorded outcome, not the transient blip.
                result_file.write_text(json.dumps(outcome, ensure_ascii=False), encoding="utf-8")

        # Improve mode: harvest structured suggestions from the executor workspace
        # before the tempdir is cleaned. _handle_success uses these to spawn
        # focused Plane tasks that the propose lane can refine and prioritise.
        improve_suggestions: list[dict] = []
        if role == "improve" and success:
            improve_suggestions = _read_improve_output(workspace)

        # Scope-too-wide: WorkspaceManager wrote scope-too-wide.json with the
        # file list. Read it before tempdir cleanup so _handle_failure can
        # spawn focused split tasks.
        scope_files: list[str] = []
        scope_file = workspace / "scope-too-wide.json"
        if scope_file.exists():
            try:
                scope_files = json.loads(scope_file.read_text(encoding="utf-8")).get("files") or []
            except Exception:
                pass

        # The execution run can succeed but produce nothing shippable — e.g. the
        # workspace's diff exceeded the soft cap and WorkspaceManager refused
        # to push. In that case we want the task to be Blocked with the
        # actionable reason, not silently moved to In Review with no PR.
        scope_too_wide = (
            success
            and result.get("branch_pushed") is False
            and result.get("failure_category") == "scope_too_wide"
        )

        if success and not scope_too_wide:
            logger.info("board_worker[%s]: task_id=%s completed status=%s", role, task_id, status)
            _handle_success(
                client, issue, role, task_kind, needs_verification, settings,
                improve_suggestions=improve_suggestions,
                pr_url=result.get("pull_request_url") or None,
            )
        else:
            log_reason = "scope_too_wide" if scope_too_wide else status
            logger.warning("board_worker[%s]: task_id=%s failed status=%s", role, task_id, log_reason)
            _handle_failure(
                client, issue, role, task_kind, result, settings,
                scope_files=scope_files if scope_too_wide else [],
            )

        return success and not scope_too_wide


# ── Spec-author pipeline (ADR 0007 Phase C) ───────────────────────────────────

def _process_spec_author(
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

    Mirrors ``_process_issue``'s plan-then-execute shape but with spec-specific
    constraints (allowed_paths=docs/specs/, max_changed_files=1, longer
    timeout) and a spec-author success handler that parses the committed spec
    file and spawns campaign sub-tasks via CampaignBuilder.

    Does NOT call _claude_cli, BrainstormService, or any direct-Claude path.
    All LLM work flows through the planning subprocess -> ExecutionCoordinator
    -> backend adapter chain — the whole point of the ADR 0007 refactor.
    """
    task_id  = str(issue["id"])

    with tempfile.TemporaryDirectory(prefix=f"oc-{role}-") as tmpdir:
        tmp = Path(tmpdir)

        # Forward source labels so the policy engine sees spec-director as a
        # trusted lane (no review_required default for autonomy-class work).
        forwarded_labels: list[str] = []
        for label in issue.get("labels", []):
            name = (label.get("name", "") if isinstance(label, dict) else str(label)).strip()
            if name.lower().startswith("source:"):
                forwarded_labels.append(name)

        plan_cmd = [
            python, "-m", "operations_center.entrypoints.worker.main",
            "--goal",               goal_text,
            "--task-type",          _task_type_from_kind("spec-author"),
            "--execution-mode",     "goal",
            "--repo-key",           repo_key,
            "--clone-url",          clone_url,
            "--base-branch",        base_branch,
            "--project-id",         settings.plane.project_id,
            "--task-id",            task_id,
            "--timeout-seconds",    str(_SPEC_AUTHOR_TIMEOUT_SECONDS),
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
            _fail_task(client, task_id, role, "spec-author planning produced no JSON output")
            return False
        if plan_proc.returncode != 0:
            msg = bundle.get("message", "unknown planning error")
            logger.error("board_worker[%s]: spec-author planning failed task_id=%s — %s", role, task_id, msg)
            _fail_task(client, task_id, role, f"spec-author planning failed: {msg}")
            return False

        bundle_file = tmp / "bundle.json"
        bundle_file.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")

        config_file = tmp / "ops.yaml"
        shutil.copy(config_path, config_file)

        workspace = tmp / "workspace"
        workspace.mkdir()
        result_file = tmp / "result.json"

        # `--source` carries spec_slug + trigger_source into run_metadata.json
        # via RunArtifactWriter's extra_metadata path. This is the ADR 0007
        # audit-trail wiring point: every spec-author run on disk can be
        # traced back to the trigger that fired it.
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
            logger.error("board_worker[%s]: spec-author execute produced no result task_id=%s", role, task_id)
            _fail_task(client, task_id, role, "spec-author execute produced no result file")
            return False

        try:
            outcome = json.loads(result_file.read_text(encoding="utf-8") or "{}")
        except Exception as exc:
            _fail_task(client, task_id, role, f"spec-author result.json parse failed: {exc}")
            return False
        result  = outcome.get("result", {})
        success = result.get("success", False)
        run_id  = result.get("run_id", "")

        if success:
            logger.info(
                "board_worker[spec-author]: task_id=%s succeeded run_id=%s spec_slug=%s",
                task_id, run_id, spec_slug,
            )
            _handle_spec_author_success(
                client=client, issue=issue, settings=settings,
                workspace=workspace, target_path=target_path,
                spec_slug=spec_slug, run_id=run_id, task_phase=task_phase,
            )
            return True
        else:
            _handle_failure(client, issue, role, "spec-author", result, settings)
            return False


def _handle_spec_author_success(
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
        # ADR 0007 Phase D + follow-up C path: phase-advance applied a
        # prompt-diff edit block; no new campaign sub-tasks needed because
        # the campaign was created on first authoring.
        #
        # Soft sanity-check: parse the prompt_diff_edits fence out of the
        # committed spec and confirm it deserializes as list[Edit]. This
        # is observational only — a failed parse logs at WARNING but does
        # NOT fail the task. The hard contract is "spec committed"; edit-
        # block hygiene is feedback for prompt tuning.
        edit_count, edit_parse_note = _summarize_prompt_diff_block(
            workspace=workspace, target_path=target_path
        )
        try:
            client.transition_issue(task_id, _STATE_DONE)
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
        # The backend reported success but the spec file isn't on disk where
        # we expected. Mark Done with a warning rather than Blocked — the run
        # itself succeeded, the campaign sub-tasks just can't be spawned.
        logger.warning(
            "board_worker[spec-author]: spec file missing at %s after success run_id=%s",
            spec_path, run_id,
        )
        try:
            client.transition_issue(task_id, _STATE_DONE)
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

    # ADR 0007 follow-up B: run_id is substituted at execute-time via
    # ExecutionRequestBuilder.build; the spec already contains the real id
    # in its `<!-- generated_by_run: ... -->` provenance comment when this
    # handler runs. No post-hoc rewrite needed.

    # Parse + create campaign sub-tasks via the existing CampaignBuilder.
    # Importing here (not at module top) keeps the spec_author dependency
    # cleanly scoped to the spec-author code path — board_worker stays
    # importable even if the spec_author package is restructured. (Package
    # was renamed from spec_director to spec_author in ADR 0007 Phase F.)
    created_ids: list[str] = []
    try:
        from operations_center.spec_author.campaign_builder import CampaignBuilder
        # Spec front-matter declares repos:[...]; CampaignBuilder takes
        # repo_key/base_branch as arguments. Pull the chosen repo from
        # the spec; fall back to OperationsCenter if missing.
        from operations_center.spec_author.models import SpecFrontMatter
        try:
            fm = SpecFrontMatter.from_spec_text(spec_text)
            repo_key = fm.repos[0] if fm.repos else _SPEC_AUTHOR_REPO_KEY
        except Exception:
            repo_key = _SPEC_AUTHOR_REPO_KEY
        repo_cfg = settings.repos.get(repo_key)
        base_branch = (
            (repo_cfg.sandbox_base_branch if repo_cfg and repo_cfg.sandbox_base_branch else None)
            or (repo_cfg.default_branch if repo_cfg else "main")
        )
        builder = CampaignBuilder(
            client=client,
            project_id=settings.plane.project_id,
        )
        created_ids = builder.build(spec_text=spec_text, repo_key=repo_key, base_branch=base_branch)
    except Exception as exc:
        logger.warning(
            "board_worker[spec-author]: campaign build failed task_id=%s — %s",
            task_id, exc,
        )

    # Tag each newly-created campaign task with `parent_run: <run_id>` per
    # ADR 0007's audit-trail invariant. CampaignBuilder already attaches
    # `source: spec-campaign` and `campaign-id: <id>`. We need a label-merge
    # (not a replace) so we re-list issues once, then merge labels per id.
    if run_id and created_ids:
        try:
            all_issues = client.list_issues()
            by_id = {str(i.get("id", "")): i for i in all_issues}
            for new_id in created_ids:
                iss = by_id.get(new_id)
                if iss is None:
                    continue
                _add_label(client, iss, f"parent_run: {run_id}")
        except Exception as exc:
            logger.debug(
                "board_worker[spec-author]: parent_run label tagging failed — %s",
                exc,
            )

    try:
        client.transition_issue(task_id, _STATE_DONE)
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


# ── Outcome handlers ──────────────────────────────────────────────────────────

def _run_ci_loop(
    *,
    ci_spec_raw: dict,
    client,
    issue: dict,
    role: str,
    task_kind: str,
    task_id: str,
    repo_key: str,
    settings,
    python: str,
    oc_root: Path,
    env: dict,
    bundle_file: Path,
    config_file: Path,
    tmp: Path,
    short_id: str,
) -> bool:
    """
    Drive a ContinuousImprovementSpec refinement loop for an improve_campaign task.

    Builds a CiRunContext, dispatches the CiCoordinator, and maps the
    final RefinementStatus to _handle_success / _handle_failure.
    Returns True on ACCEPTED, False otherwise.
    """
    from operations_center.contracts.ci import ContinuousImprovementSpec
    from operations_center.contracts.enums import RefinementStatus
    from operations_center.execution.ci_coordinator import CiCoordinator, CiRunContext
    from operations_center.execution.ci_store import CiStore
    from pydantic import ValidationError

    try:
        ci_spec = ContinuousImprovementSpec.model_validate(ci_spec_raw)
    except (ValidationError, Exception) as exc:
        logger.error(
            "board_worker[%s]: task_id=%s invalid ContinuousImprovementSpec — %s",
            role, task_id, exc,
        )
        _fail_task(client, task_id, role, f"invalid continuous_improvement spec: {exc}")
        return False

    repo_path = Path(_repo_local_path(settings, repo_key))
    lineage_id = f"lin-{task_id[:12]}"
    validation_commands = list(
        ci_spec.evaluation.evaluation_command
        and [] or []
    )
    # Prefer validation_commands from the repo config if available
    repo_cfg = settings.repos.get(repo_key)
    if repo_cfg and hasattr(repo_cfg, "validation_commands"):
        validation_commands = list(getattr(repo_cfg, "validation_commands", []))

    store_path = oc_root / "state" / "ci_lineage.json"
    ctx = CiRunContext(
        proposal_id=bundle_file.parent.name,  # tmp dir name as ephemeral ID
        repo_path=repo_path,
        lineage_id=lineage_id,
        spec=ci_spec,
        validation_commands=validation_commands,
        store_path=store_path,
        eval_output_dir=tmp / "eval_output",
        timeout_seconds=120,
    )

    last_attempt_result: dict = {}

    def execute(*, attempt_number: int, strategy, proposal_id: str):
        attempt_workspace = tmp / f"workspace-ci-{attempt_number}"
        attempt_workspace.mkdir(exist_ok=True)
        attempt_result_file = tmp / f"result-ci-{attempt_number}.json"

        exec_cmd = [
            python, "-m", "operations_center.entrypoints.execute.main",
            "--config",         str(config_file),
            "--bundle",         str(bundle_file),
            "--workspace-path", str(attempt_workspace),
            "--task-branch",    f"{role}/{short_id}-ci{attempt_number}",
            "--output",         str(attempt_result_file),
            "--source",         f"board_worker_{role}_ci_{attempt_number}",
        ]

        logger.info(
            "board_worker[%s]: CI attempt %d for task_id=%s",
            role, attempt_number, task_id,
        )
        subprocess.run(exec_cmd, cwd=oc_root, env=env, capture_output=True, text=True)

        run_id = f"ci-{task_id[:8]}-attempt-{attempt_number}"
        changed_files: list[str] = []
        success = False

        if attempt_result_file.exists():
            try:
                attempt_outcome = json.loads(attempt_result_file.read_text(encoding="utf-8"))
                r = attempt_outcome.get("result", {})
                success = r.get("success", False)
                run_id = r.get("run_id", run_id)
                changed_files = [f["path"] for f in r.get("changed_files", []) if "path" in f]
                last_attempt_result.update(r)
            except Exception as exc:
                logger.warning(
                    "board_worker[%s]: CI attempt %d result parse failed — %s",
                    role, attempt_number, exc,
                )

        return run_id, changed_files, success

    try:
        coordinator = CiCoordinator(store=CiStore(path=store_path))
        ci_result = coordinator.run(ctx, execute)
    except Exception as exc:
        logger.error(
            "board_worker[%s]: CI coordinator failed task_id=%s — %s",
            role, task_id, exc,
        )
        _fail_task(client, task_id, role, f"CI coordinator error: {exc}")
        return False

    logger.info(
        "board_worker[%s]: CI loop done task_id=%s status=%s attempts=%d",
        role, task_id, ci_result.final_status.value, ci_result.total_attempts,
    )

    _add_label(client, issue, f"ci-status: {ci_result.final_status.value}")
    _add_label(client, issue, f"ci-attempts: {ci_result.total_attempts}")

    if ci_result.final_status == RefinementStatus.ACCEPTED:
        _handle_success(client, issue, role, task_kind, False, settings)
        return True

    if ci_result.final_status == RefinementStatus.ESCALATED:
        _fail_task(
            client, task_id, role,
            f"CI loop escalated after {ci_result.total_attempts} attempt(s) — "
            "inconclusive outcome requires operator decision",
        )
        return False

    # BUDGET_EXHAUSTED or ABANDONED
    failure_result = dict(last_attempt_result)
    failure_result.setdefault("status", ci_result.final_status.value)
    failure_result.setdefault(
        "failure_reason",
        f"CI refinement loop ended with status={ci_result.final_status.value} "
        f"after {ci_result.total_attempts} attempt(s)",
    )
    _handle_failure(client, issue, role, task_kind, failure_result, settings)
    return False


def _fail_task(client, task_id: str, role: str, reason: str) -> None:
    try:
        client.transition_issue(task_id, _STATE_BLOCKED)
        client.comment_issue(task_id, f"board_worker[{role}] blocked — {reason}")
    except Exception as exc:
        logger.warning("board_worker[%s]: failed to mark task_id=%s blocked — %s", role, task_id, exc)


def _read_improve_output(workspace: Path) -> list[dict]:
    """Pull structured suggestions written by the executor to improve-output.json.

    Returns [] when the file is missing or malformed — a missing output is
    common when improve mode runs against a healthy module and finds nothing
    actionable. Callers treat that as "no follow-up tasks needed".
    """
    out_file = workspace / "improve-output.json"
    if not out_file.exists():
        return []
    try:
        data = json.loads(out_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("board_worker[improve]: malformed improve-output.json — %s", exc)
        return []
    raw = data.get("suggestions") or []
    if not isinstance(raw, list):
        return []
    valid = []
    for item in raw[:5]:  # cap at 5 — same limit stated in the improve prompt
        if isinstance(item, dict) and item.get("title"):
            valid.append(item)
    return valid


def _handle_success(client, issue: dict, role: str, _task_kind: str, needs_verification: bool, settings,
                    *, improve_suggestions: list[dict] | None = None,
                    pr_url: str | None = None) -> None:
    task_id = str(issue["id"])
    labels  = issue.get("labels", [])
    repo_key = _label_value(labels, "repo")
    await_review = (settings.repos.get(repo_key) and settings.repos[repo_key].await_review) if repo_key else False

    try:
        if role == "goal":
            if needs_verification:
                follow_id = _create_follow_up(client, issue, settings, follow_kind="test",
                                               reason="verification_needed")
                client.comment_issue(task_id,
                    f"Implementation complete — created verification task #{follow_id}")
                client.transition_issue(task_id, _STATE_DONE)
            elif await_review:
                client.transition_issue(task_id, _STATE_REVIEW)
                if pr_url:
                    _add_label(client, issue, f"pr-url: {pr_url}")
                client.comment_issue(task_id, "Implementation complete — moved to In Review")
            else:
                client.transition_issue(task_id, _STATE_DONE)
                client.comment_issue(task_id, "Implementation complete")

        elif role == "test":
            client.transition_issue(task_id, _STATE_DONE)
            client.comment_issue(task_id, "Verification passed")

        elif role == "improve":
            # Improve is analysis-only. Instead of mirroring the parent title
            # as a "follow-up goal" (the duplicate-PR problem), we read the
            # structured suggestions written to improve-output.json and
            # create one focused goal task per suggestion. The propose lane
            # picks them up like any other autonomy work.
            client.transition_issue(task_id, _STATE_DONE)
            if improve_suggestions:
                created_ids = []
                for suggestion in improve_suggestions:
                    follow_id = _create_improve_follow_up(
                        client, issue, settings, suggestion,
                    )
                    if follow_id:
                        created_ids.append(follow_id)
                client.comment_issue(
                    task_id,
                    f"Improvement analysis complete — created {len(created_ids)} "
                    f"focused follow-up task(s): {', '.join('#' + i for i in created_ids)}"
                    if created_ids
                    else "Improvement analysis complete — backend wrote suggestions but none could be enqueued",
                )
            else:
                client.comment_issue(
                    task_id,
                    "Improvement analysis complete — no actionable suggestions emitted "
                    "(executor found nothing concrete, or improve-output.json was missing)",
                )

    except Exception as exc:
        logger.warning("board_worker[%s]: post-success transition failed task_id=%s — %s", role, task_id, exc)

    # If this task is a scope-split child, check whether all siblings are now
    # Done — if so, close the parent that's been sitting in Blocked since the
    # split fired. Without this hook the parent never reaches a clean
    # terminal state even after all the work it represents has shipped.
    try:
        _maybe_close_split_parent(client, issue)
    except Exception as exc:
        logger.warning("board_worker: close-parent check failed for task_id=%s — %s", task_id, exc)


def _maybe_close_split_parent(client, completed_issue: dict) -> None:
    """Close the parent of a scope-split when its last child completes.

    No-op when the just-completed task isn't a scope-split child, the parent
    can't be found, the parent isn't Blocked (already terminal in some way),
    or any sibling besides this one is still pending.
    """
    labels = completed_issue.get("labels", [])
    label_names_lower = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
        for lab in labels
    ]
    if "source: scope-split" not in label_names_lower:
        return
    parent_id = _label_value(labels, "original-task-id")
    if not parent_id:
        return

    try:
        all_issues = client.list_issues()
    except Exception:
        return

    this_task_id = str(completed_issue.get("id", ""))
    parent: dict | None = None
    siblings: list[dict] = []
    for iss in all_issues:
        iss_id = str(iss.get("id", ""))
        if iss_id == parent_id:
            parent = iss
            continue
        if _label_value(iss.get("labels", []), "original-task-id") == parent_id:
            # Restrict to scope-split siblings — other follow-ups (improve
            # suggestions etc.) may share the parent_id and shouldn't gate
            # closure.
            sib_labels_lower = [
                (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
                for lab in iss.get("labels", [])
            ]
            if "source: scope-split" in sib_labels_lower:
                siblings.append(iss)

    if parent is None:
        return
    parent_state = (parent.get("state") or {}).get("name", "")
    if parent_state.lower() != "blocked":
        return  # Already closed, or in some other state we shouldn't touch

    # Every other split sibling must already be in Done (the just-completed
    # one we treat as Done since we just transitioned it; but we still verify
    # via Plane state to be defensive against a missed transition).
    other = [s for s in siblings if str(s.get("id", "")) != this_task_id]
    other_done = all(
        (s.get("state") or {}).get("name", "").strip().lower() == "done"
        for s in other
    )
    if not other_done:
        return
    # Verify this one too — defensive, in case the prior transition_issue
    # call lost the race somehow.
    this_state = (completed_issue.get("state") or {}).get("name", "").strip().lower()
    # `completed_issue` was hydrated *before* the success transition so it
    # may still show "running"; trust the side effect we just performed.
    if this_state and this_state not in ("done", "running", "ready for ai"):
        return

    n_total = len(siblings) + 1  # +1 for the just-completed task itself
    try:
        client.transition_issue(parent_id, _STATE_DONE)
        client.comment_issue(
            parent_id,
            f"Auto-closed: all {n_total} scope-split children completed.",
        )
        logger.info(
            "board_worker: closed parent task_id=%s after %d split children Done",
            parent_id, n_total,
        )
    except Exception as exc:
        logger.warning(
            "board_worker: failed to close parent task_id=%s — %s", parent_id, exc,
        )


def _split_files_into_chunks(files: list[str], chunk_size: int = 15, max_chunks: int = 6) -> list[list[str]]:
    """Group files into roughly-equal chunks, capped at max_chunks total.

    Files are first grouped by top-level directory (so related code stays
    together), then split into chunks. If grouping produces more than
    max_chunks, we merge the smallest groups together.
    """
    if not files:
        return []
    by_top: dict[str, list[str]] = {}
    for f in files:
        top = f.split("/", 1)[0] if "/" in f else "."
        by_top.setdefault(top, []).append(f)
    # ty narrows sorted(..., key=len) to list[Sized]; we know better — values are list[str].
    groups: list[list[str]] = sorted(by_top.values(), key=len, reverse=True)  # ty:ignore[invalid-assignment]
    chunks: list[list[str]] = []
    for group in groups:
        for i in range(0, len(group), chunk_size):
            chunks.append(group[i : i + chunk_size])
    while len(chunks) > max_chunks and len(chunks) >= 2:
        # Merge the two smallest chunks until we're under the cap
        chunks.sort(key=len)
        merged = chunks[0] + chunks[1]
        chunks = [merged] + chunks[2:]
    return chunks


def _retry_count_from_labels(labels: list) -> int:
    for lab in labels:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
        if name.startswith("retry-count:"):
            try:
                return int(name.split(":", 1)[1].strip())
            except ValueError:
                return 0
    return 0


def _create_split_followups(client, parent: dict, _settings, file_list: list[str], reason: str) -> list[str]:
    """Spawn smaller goal tasks scoped to file subsets after a scope_too_wide block.

    Caps total split depth at 2 (parent retry-count >= 2 → no further split,
    just block) so the backend can't fork unboundedly.
    """
    parent_id     = str(parent["id"])
    parent_title  = parent.get("name", "")
    parent_labels = parent.get("labels", [])
    repo_key      = _label_value(parent_labels, "repo")
    retry_count   = _retry_count_from_labels(parent_labels)

    if retry_count >= 2:
        logger.info(
            "board_worker: not splitting task_id=%s — retry-count=%d already exhausted",
            parent_id, retry_count,
        )
        return []

    chunks = _split_files_into_chunks(file_list)
    if not chunks:
        return []

    inherited_sources = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in parent_labels
    ]
    inherited_sources = [
        s for s in inherited_sources
        if s.lower().startswith("source:") and s.lower() != "source: board_worker"
    ]

    created: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        title = f"[split {idx}/{len(chunks)}] {parent_title}"[:80]
        files_block = "allowed_paths:\n" + "\n".join(f"  - {f}" for f in chunk) + "\n"
        description = (
            f"## Goal\n{parent_title}\n\n"
            f"This is split {idx} of {len(chunks)} from a scope_too_wide retry of "
            f"task #{parent_id}. Restrict changes to the listed files.\n\n"
            f"## Execution\n"
            f"repo: {repo_key}\n"
            f"mode: goal\n"
            f"{files_block}"
        )
        labels = [
            "task-kind: goal",
            f"repo: {repo_key}",
            "source: board_worker",
            "source: scope-split",
            *inherited_sources,
            f"original-task-id: {parent_id}",
            f"handoff-reason: {reason}",
            f"retry-count: {retry_count + 1}",
        ]
        try:
            new_issue = client.create_issue(
                name=title, description=description,
                state=_STATE_READY, label_names=labels,
            )
            new_id = str(new_issue.get("id", ""))
            if new_id:
                created.append(new_id)
        except Exception as exc:
            logger.warning("board_worker: split create_issue failed — %s", exc)
    # Mark the parent so triage / rewrite loops don't pick at it. Without
    # this, spec_director.phase_orchestrator._handle_blocked would later
    # ask the backend to "rewrite" the parent description and re-queue it,
    # producing exactly the kind of ghost work this audit is trying to
    # eliminate.
    if created:
        _add_label(client, parent, _LIFECYCLE_EXPANDED)

    logger.info(
        "board_worker: split task_id=%s into %d chunks (retry-count=%d → %d)",
        parent_id, len(created), retry_count, retry_count + 1,
    )
    return created


def _add_label(client, issue: dict, new_label: str) -> None:
    """Append `new_label` to an issue's label set if not already present.

    Plane's update_issue_labels replaces the set, so we read existing
    labels first. Failures are non-fatal — at worst the parent stays
    un-marked and a downstream service might re-process; the next cycle
    of board_worker can re-apply.
    """
    existing = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in issue.get("labels", [])
    ]
    existing = [name for name in existing if name]
    if new_label in existing:
        return
    try:
        client.update_issue_labels(str(issue["id"]), existing + [new_label])
    except Exception as exc:
        logger.warning(
            "board_worker: failed to add label %r to task_id=%s — %s",
            new_label, issue.get("id"), exc,
        )


def _increment_retry_count(client, issue: dict) -> None:
    """Bump retry-count label by 1 (adds 'retry-count: 1' if absent).

    Removes the old retry-count: N label and adds retry-count: N+1 so
    board_unblock Rule 1 can cancel tasks that SIGKILL repeatedly.
    """
    existing = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in issue.get("labels", [])
    ]
    existing = [name for name in existing if name]
    current = 0
    filtered = []
    for label in existing:
        if label.lower().startswith("retry-count:"):
            try:
                current = int(label.split(":", 1)[1].strip())
            except ValueError:
                pass
        else:
            filtered.append(label)
    filtered.append(f"retry-count: {current + 1}")
    try:
        client.update_issue_labels(str(issue["id"]), filtered)
    except Exception as exc:
        logger.warning(
            "board_worker: failed to increment retry-count for task_id=%s — %s",
            issue.get("id"), exc,
        )


def _handle_failure(
    client, issue: dict, role: str, _task_kind: str, result: dict, settings,
    *, scope_files: list[str] | None = None,
) -> None:
    task_id  = str(issue["id"])
    status   = result.get("status", "unknown")
    category = result.get("failure_category") or "unknown"
    reason   = result.get("failure_reason") or "(no reason provided)"

    # Scope-too-wide auto-recovery: if we have the file list, spawn focused
    # split tasks instead of leaving the operator to do it manually. The
    # parent task itself still moves to Blocked so the original entry has a
    # clean terminal state.
    split_ids: list[str] = []
    if category == "scope_too_wide" and scope_files:
        try:
            split_ids = _create_split_followups(
                client, issue, settings, scope_files, reason="scope_too_wide_split",
            )
        except Exception as exc:
            logger.warning("board_worker: scope-split spawn failed — %s", exc)

    # Log the full reason — operators want to see this in the worker logs even
    # when the Plane comment is truncated.
    logger.warning(
        "board_worker[%s]: task_id=%s blocked status=%s category=%s reason=%s",
        role, task_id, status, category, reason,
    )

    executor_exit_code: int | None = result.get("executor_exit_code")
    executor_signal: str | None = result.get("executor_signal")

    try:
        if role == "test":
            follow_id = _create_follow_up(client, issue, settings, follow_kind="goal",
                                           reason="verification_failed")
            client.transition_issue(task_id, _STATE_BLOCKED)
            client.comment_issue(
                task_id,
                f"Verification failed — created follow-up goal task #{follow_id}\n"
                f"\n"
                f"- status: {status}\n"
                f"- category: {category}\n"
                f"- reason: {reason}",
            )
        else:
            client.transition_issue(task_id, _STATE_BLOCKED)
            split_block = ""
            if split_ids:
                split_block = f"\n\nAuto-split into {len(split_ids)} focused task(s): {', '.join('#' + i for i in split_ids)}"
            exec_block = ""
            if executor_exit_code is not None:
                exec_block = f"\n- executor-exit-code: {executor_exit_code}"
                if executor_signal:
                    exec_block += f"\n- executor-signal: {executor_signal}"
            client.comment_issue(
                task_id,
                f"board_worker[{role}] failed\n"
                f"\n"
                f"- status: {status}\n"
                f"- category: {category}\n"
                f"- reason: {reason}"
                f"{exec_block}"
                f"{split_block}",
            )
        # Attach executor telemetry as machine-readable labels so triage_scan
        # can surface them in structured output without log inference.
        if executor_exit_code is not None:
            _add_label(client, issue, f"executor-exit-code: {executor_exit_code}")
        if executor_signal:
            _add_label(client, issue, f"executor-signal: {executor_signal}")
            if "sigkill" in executor_signal.lower():
                _increment_retry_count(client, issue)
    except Exception as exc:
        logger.warning("board_worker[%s]: post-failure transition failed task_id=%s — %s", role, task_id, exc)


def _create_improve_follow_up(
    client, parent: dict, _settings, suggestion: dict,
) -> str | None:
    """Create a focused goal task from one improve suggestion.

    Carries forward the parent's repo + source provenance, embeds the
    suggestion's rationale and file scope into the task description so the
    backend's next run has concrete context. Returns the new task id, or None on error.
    """
    parent_id     = str(parent["id"])
    parent_labels = parent.get("labels", [])
    repo_key      = _label_value(parent_labels, "repo")

    title       = str(suggestion.get("title", "")).strip()[:80] or "Improve follow-up"
    rationale   = str(suggestion.get("rationale", "")).strip()
    files       = suggestion.get("files") or []
    complexity  = str(suggestion.get("complexity", "")).strip().lower()

    files_block = ""
    if isinstance(files, list) and files:
        files_block = "allowed_paths:\n" + "\n".join(f"  - {f}" for f in files[:10] if isinstance(f, str)) + "\n"

    description = (
        f"## Goal\n{title}\n\n"
        f"## Rationale\n{rationale or '(none provided)'}\n\n"
        f"## Execution\n"
        f"repo: {repo_key}\n"
        f"mode: goal\n"
        f"{files_block}"
    )

    inherited_sources = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in parent_labels
    ]
    inherited_sources = [
        s for s in inherited_sources
        if s.lower().startswith("source:") and s.lower() != "source: board_worker"
    ]
    label_names = [
        "task-kind: goal",
        f"repo: {repo_key}",
        "source: board_worker",
        "source: improve-suggestion",  # distinct so the propose lane can recognise it
        *inherited_sources,
        f"original-task-id: {parent_id}",
        "handoff-reason: improve_suggestion",
    ]
    if complexity in {"small", "medium", "large"}:
        label_names.append(f"complexity: {complexity}")

    try:
        issue = client.create_issue(
            name=title,
            description=description,
            state=_STATE_READY,
            label_names=label_names,
        )
        new_id = str(issue.get("id", ""))
        logger.info(
            "board_worker[improve]: spawned follow-up task_id=%s title=%r complexity=%s",
            new_id, title, complexity,
        )
        return new_id or None
    except Exception as exc:
        logger.warning(
            "board_worker[improve]: failed to create follow-up for %r — %s",
            title, exc,
        )
        return None


_MAX_FOLLOW_UP_RETRIES = 3


def _create_follow_up(client, parent: dict, _settings, follow_kind: str, reason: str) -> str:
    """Create a follow-up Plane task with full lineage metadata. Returns the new task id.

    Refuses past `_MAX_FOLLOW_UP_RETRIES` (default 3): a chain of test_failure →
    goal → test_failure → goal would otherwise burn quota on the same goal
    text indefinitely. When the cap is reached, the parent is left in its
    current state and we return "" so the caller can short-circuit.
    """
    parent_id    = str(parent["id"])
    parent_title = parent.get("name", "")
    parent_labels = parent.get("labels", [])
    repo_key     = _label_value(parent_labels, "repo")
    base_branch  = _label_value(parent_labels, "base-branch")
    retry_count  = _retry_count_from_labels(parent_labels)

    if retry_count >= _MAX_FOLLOW_UP_RETRIES:
        logger.info(
            "board_worker: refusing follow-up — parent task_id=%s already at retry-count=%d",
            parent_id, retry_count,
        )
        return ""

    description = (
        f"## Goal\n{parent_title} — {reason.replace('_', ' ')}\n\n"
        f"## Execution\n"
        f"repo: {repo_key}\n"
        f"mode: {follow_kind}\n"
        + (f"base_branch: {base_branch}\n" if base_branch else "")
    )

    # Inherit the parent's `source: ...` provenance so policy can recognise the
    # follow-up as part of an already-trusted lane (autonomy, spec-campaign).
    # Without this the policy engine review-blocks every follow-up because
    # `source: board_worker` alone isn't in the trusted set.
    inherited_sources = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        for lab in parent_labels
    ]
    inherited_sources = [
        s for s in inherited_sources
        if s.lower().startswith("source:") and s.lower() != "source: board_worker"
    ]
    label_names = [
        f"task-kind: {follow_kind}",
        f"repo: {repo_key}",
        "source: board_worker",
        *inherited_sources,
        f"original-task-id: {parent_id}",
        f"handoff-reason: {reason}",
        f"retry-count: {retry_count + 1}",
    ]

    issue = client.create_issue(
        name=f"[{follow_kind}] {parent_title}",
        description=description,
        state=_STATE_READY,
        label_names=label_names,
    )
    new_id = str(issue.get("id", "?"))
    logger.info("board_worker: created follow-up task_id=%s kind=%s reason=%s", new_id, follow_kind, reason)
    return new_id


# ── Heartbeat ─────────────────────────────────────────────────────────────────

def _write_heartbeat(status_dir: Path, role: str, status: str = "idle") -> None:
    try:
        status_dir.mkdir(parents=True, exist_ok=True)
        hb = status_dir / f"heartbeat_{role}.json"
        hb.write_text(json.dumps({
            "role": role,
            "at":   datetime.now(UTC).isoformat(),
            "status": status,
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _heartbeat_loop(status_dir: Path, role: str, stop_event) -> None:
    """Write 'executing' heartbeat every 60 s while a task runs."""
    while not stop_event.is_set():
        _write_heartbeat(status_dir, role, status="executing")
        stop_event.wait(60)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_goal(description: str, title: str) -> str:
    """Pull goal text from ## Goal section, fall back to title."""
    import re
    m = re.search(r"##\s+Goal\s*\n(.*?)(?=##|\Z)", description, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1).strip()
        if text:
            return text
    return title


def _task_type_from_kind(task_kind: str) -> str:
    return {
        "goal":              "feature",
        "test":              "test",
        "test_campaign":     "test",
        "improve":           "refactor",
        "improve_campaign":  "refactor",
        # ADR 0007 Phase C: spec-author writes a markdown spec under docs/.
        # Closest canonical TaskType bucket is "chore" — it's a docs-shaped
        # edit, not a feature/refactor/test.
        "spec-author":       "chore",
    }.get(task_kind, "chore")


# ── Spec-author payload parsing (ADR 0007 Phase C) ───────────────────────────

# Host repo for spec files. spec_trigger writes target_path = docs/specs/<slug>.md
# and the backend operates on this repo's workspace clone.
_SPEC_AUTHOR_REPO_KEY = "OperationsCenter"

# Timeout for an LLM-driven spec draft. Spec authoring is a single-file write
# with a tightly-scoped prompt — 8 minutes is generous for the model + the
# clone/commit/push overhead, while still bounded enough that a runaway run
# gets killed before it can burn the rest of the cycle.
_SPEC_AUTHOR_TIMEOUT_SECONDS = 480


def _parse_spec_author_payload(description: str) -> dict | None:
    """Extract the YAML payload spec_trigger embeds in the task description.

    Returns the parsed dict (with spec_slug, target_path, trigger_source,
    task_phase, seed_text, context_bundle) or None if the body doesn't carry
    a parseable spec-author block.

    The body shape is fixed by ``spec_trigger._render_task_body``:
        ## Spec Authoring

        ```yaml
        task-kind: spec-author
        ...
        ```
    """
    import html as _html
    import re as _re
    import yaml as _yaml
    # Normalize HTML descriptions returned by Plane's API (description_html
    # uses <br> for newlines; strip other tags so the fenced YAML block is
    # readable by the regex below).
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


_PROMPT_DIFF_OPEN  = "<!-- prompt_diff_edits -->"
_PROMPT_DIFF_CLOSE = "<!-- /prompt_diff_edits -->"


def _summarize_prompt_diff_block(
    *, workspace: Path, target_path: str
) -> tuple[int | None, str]:
    """Soft-validate the prompt_diff_edits fence in a committed spec.

    Returns ``(edit_count, note)``:

    * ``(N, "parsed")``  — fence present and YAML deserialized to N ``Edit`` objects.
    * ``(None, "absent")`` — no fence in the file.
    * ``(None, "<reason>")`` — fence present but failed to parse (logged, not fatal).

    Pure observation: the hard contract is "spec committed". This function
    never raises and never affects task transition. Per ADR 0007 follow-up C.
    """
    try:
        spec_text = (workspace / target_path).read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"read failed: {exc}"

    if _PROMPT_DIFF_OPEN not in spec_text or _PROMPT_DIFF_CLOSE not in spec_text:
        return None, "absent"

    try:
        body = spec_text.split(_PROMPT_DIFF_OPEN, 1)[1].split(_PROMPT_DIFF_CLOSE, 1)[0]
    except IndexError:
        return None, "fence malformed"

    try:
        import yaml  # local — keeps board_worker import path lean for non-spec kinds.

        from operations_center.prompt_diff import Edit

        doc = yaml.safe_load(body) or {}
        raw_edits = doc.get("edits") if isinstance(doc, dict) else None
        if not isinstance(raw_edits, list):
            return None, "edits key missing or not a list"
        parsed = [Edit.model_validate(e) for e in raw_edits]
        return len(parsed), "parsed"
    except Exception as exc:  # noqa: BLE001 — soft signal, log everything.
        return None, f"parse failed: {type(exc).__name__}: {exc}"


def _build_phase_advance_goal_text(
    *,
    spec_slug: str,
    target_path: str,
    task_phase: str,
    seed_text: str,
    ctx: dict,
    run_id_placeholder: str,
) -> str:
    """Phase-advance spec rewrite prompt (ADR 0007 Phase D + follow-up C).

    Surgical-edit template: the agent reads the existing spec, emits a
    structured ``list[Edit]`` patch under a fenced block, applies it via
    the in-tree ``operations_center.prompt_diff`` primitive, and writes the
    result back. Replaces the previous full-regen prompt — preserves prior
    decisions and structure across phase advances rather than rolling them
    forward by hope.
    """
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
        "No stylistic cleanup, no \"while I'm here\" rewrites.\n"
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
        "    anchor: \"## Goals\\n1. Implement the parser.\\n\"\n"
        "    new_text: \"## Goals\\n1. Add unit coverage for the parser.\\n\"\n"
        "    reason: \"advance from implement to test phase\"\n"
        "    targets_criterion: null\n"
        "  - op: insert_after\n"
        "    anchor: \"## Success Criteria\\n\"\n"
        f"    new_text: \"- {task_phase} phase: coverage report attached to the campaign run.\\n\"\n"
        "    reason: \"add phase-specific done criterion\"\n"
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
                parts.append(
                    f"## Recent Git Activity ({repo_key})\n```\n{log_text}\n```"
                )

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


def _build_spec_author_goal_text(payload: dict, run_id_placeholder: str) -> str:
    """Compose the spec-authoring prompt the backend will execute.

    Mirrors the structure of ``spec_director.brainstorm.BrainstormService``'s
    user prompt (available repos, operator seed, recent git activity, existing
    specs, board summary) but is delivered as a single goal_text the backend
    can act on directly — no separate Claude subprocess, no _claude_cli, no
    BrainstormService.

    The ``task_phase`` branch is reserved for Phase D (phase orchestration via
    spec-author with task_phase set). For now we emit a TODO so the prompt
    structure is in place and the Phase D handler only has to swap the body.
    """
    spec_slug    = str(payload.get("spec_slug", "")).strip()
    target_path  = str(payload.get("target_path", "")).strip()
    trigger      = str(payload.get("trigger_source", "")).strip()
    task_phase   = str(payload.get("task_phase", "")).strip()
    seed_text    = str(payload.get("seed_text") or "").strip()
    ctx          = payload.get("context_bundle") or {}

    if task_phase:
        # ADR 0007 Phase D: phase-advance rewrite prompt.
        #
        # The agent runs in a workspace where the OC repo is checked out;
        # the existing spec is on disk at `target_path`. The task is to
        # rewrite the spec in-place for the next phase, preserving prior
        # decisions and structure.
        #
        # NOTE: this is the naive "full regen" approach the ADR's follow-up
        # section calls out. When the prompt-diff primitive (cloned from
        # temm1e-labs/promptlabs) lands, ONLY the body of this branch
        # changes — the payload shape, board interaction, and
        # _handle_spec_author_success all stay identical.
        return _build_phase_advance_goal_text(
            spec_slug=spec_slug,
            target_path=target_path,
            task_phase=task_phase,
            seed_text=seed_text,
            ctx=ctx if isinstance(ctx, dict) else {},
            run_id_placeholder=run_id_placeholder,
        )

    parts: list[str] = []
    parts.append(
        f"# Spec authoring task\n\n"
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
        parts.append("## Existing Specs (do not duplicate)\n"
                     + "\n".join(f"- {s}" for s in existing))

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


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="OperationsCenter board worker — polls Plane and executes tasks by role"
    )
    parser.add_argument("--config",                 required=True, type=Path)
    parser.add_argument("--role",                   required=True, choices=list(_ROLE_KINDS))
    parser.add_argument("--poll-interval-seconds",  type=int, default=30, dest="poll_interval")
    parser.add_argument("--status-dir",             type=Path, default=None, dest="status_dir")
    parser.add_argument("--once",                   action="store_true")
    parser.add_argument("--log-level",              default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format=f"%(asctime)s [{args.role}] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    role       = args.role
    status_dir = args.status_dir or (Path(__file__).resolve().parents[4] / "logs" / "local" / "watch-all")

    logger.info("board_worker[%s]: starting — poll_interval=%ds", role, args.poll_interval)

    while True:
        try:
            settings = _load_settings(args.config)
            client   = _plane_client(settings)
            try:
                issue = _claim_next(client, role, settings)
                if issue:
                    import threading
                    _stop = threading.Event()
                    _hb_thread = threading.Thread(
                        target=_heartbeat_loop,
                        args=(status_dir, role, _stop),
                        daemon=True,
                    )
                    _hb_thread.start()
                    try:
                        _process_issue(issue, role, args.config, settings, client)
                    finally:
                        _stop.set()
                        _hb_thread.join(timeout=5)
                else:
                    logger.debug("board_worker[%s]: nothing ready", role)
                _write_heartbeat(status_dir, role)
            finally:
                client.close()
        except Exception as exc:
            logger.error("board_worker[%s]: unhandled error — %s", role, exc, exc_info=True)

        if args.once:
            return 0

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    sys.exit(main())
