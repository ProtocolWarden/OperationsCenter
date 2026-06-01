# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""PR Review Watcher — two-phase autonomous state machine for goal-lane PRs.

Phase 0 (ci_fix): when CI is failing on an autonomy PR, auto-fix the branch.
  - Runs ruff --fix + format on the local checkout and pushes.
  - Retries up to max_ci_fix_attempts; then falls through to self_review.

Phase 1 (self_review): executor reviews the diff and emits LGTM or CONCERNS.
  - LGTM → auto-merge.
  - No verdict (pipeline crash/timeout) → retry silently; auto-merge after
    max_self_review_loops to avoid stalling indefinitely.
  - CONCERNS → log, retry up to max_self_review_loops; auto-merge when
    exhausted. OC figures it out — no human escalation.

There is no human_review phase. Human escalation is removed entirely.
All PRs from autonomy branches resolve autonomously.

State per PR persisted in state/pr_reviews/<repo_key>-<pr_number>.json.
The state file is the single source of truth; Plane is updated after state is written.

CLI matches the reviewer role contract used by operations-center.sh:
    --config              path to operations_center.local.yaml
    --watch               run as a daemon (loop forever)
    --poll-interval-seconds N
    --status-dir          directory for heartbeat_review.json
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

_STATE_SUBDIR = Path("state") / "pr_reviews"


# ── State file helpers ────────────────────────────────────────────────────────


def _state_key(repo_key: str, pr_number: int) -> str:
    return f"{repo_key}-{pr_number}"


def _state_path(oc_root: Path, repo_key: str, pr_number: int) -> Path:
    return oc_root / _STATE_SUBDIR / f"{_state_key(repo_key, pr_number)}.json"


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(path: Path, state: dict) -> None:
    state = dict(state)
    state["updated_at"] = datetime.now(UTC).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _new_state(repo_key: str, pr_number: int) -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        "state_key": _state_key(repo_key, pr_number),
        "pr_number": pr_number,
        "repo_key": repo_key,
        "phase": "ci_fix",
        "ci_fix_attempts": 0,
        "ci_fix_last_push_at": None,
        "self_review_loops": 0,
        "human_review_loops": 0,
        "processed_comment_ids": [],
        "plane_task_id": None,
        "phase2_entered_at": None,
        "created_at": now,
        "updated_at": now,
    }


# ── Settings / adapter helpers ────────────────────────────────────────────────


def _load_settings(config_path: Path):
    from operations_center.config import load_settings

    return load_settings(config_path)


def _github_client(settings):
    from operations_center.adapters.github_pr import GitHubPRClient

    token = settings.git_token()
    if not token:
        raise RuntimeError("no GitHub token — set GIT_TOKEN in .env")
    return GitHubPRClient(token)


def _plane_client(settings):
    from operations_center.adapters.plane import PlaneClient

    return PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )


def _owner_repo(clone_url: str) -> tuple[str, str]:
    from operations_center.adapters.github_pr import GitHubPRClient

    return GitHubPRClient.owner_repo_from_clone_url(clone_url)


def _venv_python(oc_root: Path) -> str:
    p = oc_root / ".venv" / "bin" / "python"
    return str(p) if p.exists() else "python3"


def _build_env(oc_root: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(oc_root / "src")
    return env


def _label_value(labels: list, prefix: str) -> str:
    for lab in labels:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        if name.lower().startswith(prefix.lower() + ":"):
            return name.split(":", 1)[1].strip()
    return ""


# ── pr review pipeline ────────────────────────────────────────────────────────


def _run_pipeline(
    oc_root: Path,
    config_path: Path,
    repo_key: str,
    goal_text: str,
    settings,
    *,
    source: str,
    state_key: str,
    branch_suffix: str,
) -> dict | None:
    """Run worker.main → execute.main and return verdict.json contents, or None."""
    python = _venv_python(oc_root)
    env = _build_env(oc_root)
    repo_cfg = settings.repos.get(repo_key)
    if not repo_cfg:
        logger.error("pr_review_watcher: unknown repo_key=%s", repo_key)
        return None

    with tempfile.TemporaryDirectory(prefix="oc-review-") as tmpdir:
        tmp = Path(tmpdir)

        plan_cmd = [
            python,
            "-m",
            "operations_center.entrypoints.worker.main",
            "--goal",
            goal_text,
            "--task-type",
            "chore",
            "--execution-mode",
            "goal",
            "--repo-key",
            repo_key,
            "--clone-url",
            repo_cfg.clone_url,
            "--base-branch",
            repo_cfg.default_branch,
            "--project-id",
            settings.plane.project_id,
            "--task-id",
            state_key,
        ]
        plan_proc = subprocess.run(plan_cmd, cwd=oc_root, env=env, capture_output=True, text=True)

        try:
            bundle = json.loads(plan_proc.stdout)
        except Exception:
            logger.error(
                "pr_review_watcher: planning produced no JSON for state_key=%s\n%s",
                state_key,
                (plan_proc.stderr or plan_proc.stdout).strip(),
            )
            return None

        if plan_proc.returncode != 0:
            logger.error(
                "pr_review_watcher: planning failed state_key=%s — %s",
                state_key,
                bundle.get("message", "unknown"),
            )
            return None

        bundle_file = tmp / "bundle.json"
        config_copy = tmp / "ops.yaml"
        workspace = tmp / "workspace"
        result_file = tmp / "result.json"

        bundle_file.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        shutil.copy(config_path, config_copy)
        workspace.mkdir()

        exec_cmd = [
            python,
            "-m",
            "operations_center.entrypoints.execute.main",
            "--config",
            str(config_copy),
            "--bundle",
            str(bundle_file),
            "--workspace-path",
            str(workspace),
            "--task-branch",
            f"review/{branch_suffix}",
            "--output",
            str(result_file),
            "--source",
            source,
        ]
        try:
            exec_proc = subprocess.run(
                exec_cmd,
                cwd=oc_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=1800,  # 30 min hard cap — prevents hung executor blocking the watcher
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "pr_review_watcher: execute pipeline timed out after 30m for state_key=%s",
                state_key,
            )
            return None
        if exec_proc.returncode != 0:
            logger.warning(
                "pr_review_watcher: execute pipeline exited rc=%d for state_key=%s\nstderr: %s",
                exec_proc.returncode,
                state_key,
                (exec_proc.stderr or exec_proc.stdout or "").strip()[-2000:],
            )

        verdict_path = workspace / "verdict.json"
        if verdict_path.exists():
            try:
                return json.loads(verdict_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning(
                    "pr_review_watcher: malformed verdict.json for state_key=%s", state_key
                )
        else:
            logger.warning(
                "pr_review_watcher: no verdict.json produced for state_key=%s (rc=%d)",
                state_key,
                exec_proc.returncode,
            )
        return None


# ── GitHub helpers ─────────────────────────────────────────────────────────────


# ── Merge + Plane done ────────────────────────────────────────────────────────


def _merge_and_done(
    state: dict,
    state_path: Path,
    _pr_data: dict,
    gh_client,
    owner: str,
    repo: str,
    settings,
    *,
    reason: str,
) -> None:
    pr_number = state["pr_number"]
    # Guard: skip merge when GitHub reports a conflict — avoids 405 spam every cycle.
    # get_mergeable() returns None while GitHub is still computing; treat that as
    # "unknown, try anyway" so we don't hold up clean PRs during GitHub's lazy eval.
    if gh_client.get_mergeable(owner, repo, pr_number) is False:
        logger.warning(
            "pr_review_watcher: PR #%d has merge conflicts — skipping merge (reason=%s); "
            "branch must be rebased before auto-merge will proceed",
            pr_number,
            reason,
        )
        return
    try:
        gh_client.merge_pr(owner, repo, pr_number, merge_method="squash")
        logger.info(
            "pr_review_watcher: merged PR #%d repo=%s reason=%s",
            pr_number,
            state["repo_key"],
            reason,
        )
    except Exception as exc:
        logger.error("pr_review_watcher: merge failed PR #%d — %s", pr_number, exc)
        return  # leave state file — operator must inspect

    plane_task_id = state.get("plane_task_id")
    if plane_task_id:
        try:
            client = _plane_client(settings)
            try:
                client.transition_issue(plane_task_id, "Done")
                client.comment_issue(plane_task_id, f"PR #{pr_number} merged ({reason})")
            finally:
                client.close()
        except Exception as exc:
            logger.warning(
                "pr_review_watcher: Plane Done failed task_id=%s — %s", plane_task_id, exc
            )

    state_path.unlink(missing_ok=True)


# ── Spec + Custodian context helpers ─────────────────────────────────────────


def _load_campaign_spec(oc_root: Path, settings, plane_task_id: str | None) -> str:
    """Return the campaign spec text for a Plane task, or '' if unavailable."""
    if not plane_task_id:
        return ""
    try:
        client = _plane_client(settings)
        try:
            issue = client.fetch_issue(plane_task_id)
        finally:
            client.close()
        labels = issue.get("labels", []) or []
        campaign_id = _label_value(labels, "campaign-id")
        if not campaign_id:
            return ""
        from operations_center.spec_author.state import CampaignStateManager

        campaigns_state = CampaignStateManager().load()
        for campaign in campaigns_state.active_campaigns():
            if campaign.campaign_id == campaign_id:
                spec_path = Path(campaign.spec_file)
                if not spec_path.is_absolute():
                    spec_path = oc_root / spec_path
                if spec_path.exists():
                    return spec_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.debug("pr_review_watcher: spec lookup failed — %s", exc)
    return ""


def _custodian_findings(oc_root: Path, repo_key: str, settings) -> str:
    """Run custodian-multi on the repo's local path and return findings text.

    Silently returns '' when Custodian is unavailable or the repo has no local
    clone configured — so the review falls back to diff-only assessment.
    """
    repo_cfg = settings.repos.get(repo_key)
    local_path = getattr(repo_cfg, "local_path", None) if repo_cfg else None
    if not local_path or not Path(local_path).exists():
        return ""
    custodian_bin = oc_root / ".venv" / "bin" / "custodian-multi"
    if not custodian_bin.exists():
        return ""
    try:
        proc = subprocess.run(
            [str(custodian_bin), "--repos", str(local_path), "--json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = (proc.stdout or "").strip()
        if not output:
            return ""
        results = json.loads(output)
        findings = []
        for repo_result in results:
            for det in repo_result.get("detectors") or []:
                if det.get("findings"):
                    for f in det["findings"]:
                        findings.append(
                            f"  [{det.get('code', '?')}] {det.get('description', '?')}: "
                            f"{f.get('path', '?')}:{f.get('line', '?')} — {f.get('message', '')}"
                        )
        if findings:
            return "Custodian findings on current branch:\n" + "\n".join(findings[:50])
    except Exception as exc:
        logger.debug("pr_review_watcher: custodian check failed — %s", exc)
    return ""


# ── Phase 0: ci_fix ──────────────────────────────────────────────────────────

_MAX_CI_FIX_ATTEMPTS = 3
_CI_FIX_WAIT_SECONDS = 120  # wait after pushing before re-checking CI

# Checks whose failure we know how to auto-fix locally.
_AUTOFIX_CHECK_NAMES = {"lint (ruff)", "ruff", "lint"}


def _ci_checks_failing(
    gh_client, owner: str, repo: str, pr_number: int, ignored: list[str]
) -> list[str]:
    """Return names of currently failing (non-ignored) checks, or [] if all green."""
    try:
        return gh_client.get_failed_checks(owner, repo, pr_number, ignored_checks=ignored) or []
    except Exception:
        return []


def _phase0_ci_fix(
    state: dict,
    state_path: Path,
    pr_data: dict,
    gh_client,
    owner: str,
    repo: str,
    oc_root: Path,
    settings,
) -> None:
    """Phase 0: if CI is failing on an autonomy PR, push an auto-fix and wait.

    Transitions to self_review when CI is green or when attempts are exhausted.
    """
    pr_number = int(state["pr_number"])
    repo_key = state["repo_key"]
    repo_cfg = settings.repos.get(repo_key)
    if not repo_cfg:
        state["phase"] = "self_review"
        _save_state(state_path, state)
        return

    ignored = list(getattr(repo_cfg, "ci_ignored_checks", []) or [])
    failed = _ci_checks_failing(gh_client, owner, repo, pr_number, ignored)

    if not failed:
        # CI is green — move straight to self_review
        logger.info("pr_review_watcher: PR #%d CI green, advancing to self_review", pr_number)
        state["phase"] = "self_review"
        _save_state(state_path, state)
        return

    # If we pushed a fix recently, wait for CI to re-run before acting again.
    last_push = state.get("ci_fix_last_push_at")
    if last_push:
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(last_push)).total_seconds()
        if elapsed < _CI_FIX_WAIT_SECONDS:
            logger.debug(
                "pr_review_watcher: PR #%d CI fix pushed %.0fs ago — waiting for CI rerun",
                pr_number,
                elapsed,
            )
            return

    attempts = state.get("ci_fix_attempts", 0)
    if attempts >= _MAX_CI_FIX_ATTEMPTS:
        logger.info(
            "pr_review_watcher: PR #%d exhausted %d CI fix attempts (%s still failing) "
            "— advancing to self_review",
            pr_number,
            attempts,
            failed,
        )
        state["phase"] = "self_review"
        _save_state(state_path, state)
        return

    # Only auto-fix checks we know how to handle; skip if unknown failures dominate.
    # get_failed_checks may return names like "Lint (ruff): failure" — match by prefix.
    def _is_fixable(check_name: str) -> bool:
        cn = check_name.lower().split(":")[0].strip()
        return cn in _AUTOFIX_CHECK_NAMES or any(cn.startswith(k) for k in _AUTOFIX_CHECK_NAMES)

    fixable_failing = [c for c in failed if _is_fixable(c)]
    unfixable = [c for c in failed if not _is_fixable(c)]
    if unfixable and not fixable_failing:
        logger.info(
            "pr_review_watcher: PR #%d CI failing on non-auto-fixable checks %s "
            "— advancing to self_review",
            pr_number,
            unfixable,
        )
        state["phase"] = "self_review"
        _save_state(state_path, state)
        return

    # --- Attempt auto-fix on the local repo checkout ---
    local_path = getattr(repo_cfg, "local_path", None)
    head_ref = ((pr_data.get("head") or {}).get("ref") or "").strip()
    if not local_path or not head_ref:
        state["phase"] = "self_review"
        _save_state(state_path, state)
        return

    local_path = Path(local_path)
    venv_bin = local_path / (getattr(repo_cfg, "venv_dir", ".venv") or ".venv") / "bin"
    ruff_bin = venv_bin / "ruff"
    if not ruff_bin.exists():
        ruff_bin = Path(shutil.which("ruff") or "ruff")

    git_env = dict(os.environ)
    git_token = settings.git_token()
    author_name = getattr(settings.git, "author_name", "Operations Center Bot")
    author_email = getattr(settings.git, "author_email", "operations-center-bot@example.com")
    git_env["GIT_AUTHOR_NAME"] = author_name
    git_env["GIT_AUTHOR_EMAIL"] = author_email
    git_env["GIT_COMMITTER_NAME"] = author_name
    git_env["GIT_COMMITTER_EMAIL"] = author_email
    if git_token:
        git_env["GH_TOKEN"] = git_token

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=local_path, env=git_env, capture_output=True, text=True
        )

    try:
        # Stash any in-progress work, checkout the PR branch, pull latest.
        _git("stash", "--include-untracked")
        checkout = _git("checkout", head_ref)
        if checkout.returncode != 0:
            _git("fetch", "origin", head_ref)
            _git("checkout", "-B", head_ref, f"origin/{head_ref}")
        _git("pull", "--ff-only", "origin", head_ref)

        # Run ruff auto-fix.
        subprocess.run(
            [str(ruff_bin), "check", "--fix", "."], cwd=local_path, capture_output=True, text=True
        )
        subprocess.run(
            [str(ruff_bin), "format", "."], cwd=local_path, capture_output=True, text=True
        )

        # Check if anything changed.
        status = _git("status", "--porcelain")
        if not status.stdout.strip():
            logger.info(
                "pr_review_watcher: PR #%d ruff fix produced no changes — advancing to self_review",
                pr_number,
            )
            state["phase"] = "self_review"
            _save_state(state_path, state)
            return

        _git("add", "-A")
        _git("commit", "-m", f"fix(ci): auto-fix ruff lint violations on {head_ref}")
        push = _git("push", "origin", head_ref)
        if push.returncode != 0:
            logger.warning(
                "pr_review_watcher: PR #%d ci-fix push failed — %s",
                pr_number,
                push.stderr.strip(),
            )
            state["phase"] = "self_review"
            _save_state(state_path, state)
            return

        state["ci_fix_attempts"] = attempts + 1
        state["ci_fix_last_push_at"] = datetime.now(UTC).isoformat()
        logger.info(
            "pr_review_watcher: PR #%d ci-fix attempt %d pushed to %s — waiting for CI",
            pr_number,
            attempts + 1,
            head_ref,
        )
        _save_state(state_path, state)

    except Exception as exc:
        logger.warning("pr_review_watcher: PR #%d ci_fix error — %s", pr_number, exc)
        state["phase"] = "self_review"
        _save_state(state_path, state)
    finally:
        # Return local repo to main so other watchers aren't disrupted.
        default = getattr(repo_cfg, "default_branch", "main")
        _git("checkout", default)
        _git("stash", "pop")


# ── Phase 1: self-review ──────────────────────────────────────────────────────


def _phase1(
    state: dict,
    state_path: Path,
    pr_data: dict,
    gh_client,
    owner: str,
    repo: str,
    oc_root: Path,
    config_path: Path,
    settings,
) -> None:
    pr_number = int(state["pr_number"])
    repo_key = state["repo_key"]
    state_key = state["state_key"]
    reviewer = settings.reviewer

    # ── auto-merge-on-CI-green (fast path) ───────────────────────────────────
    # For autonomy PRs on repos that opt in, skip the self-review pipeline
    # entirely and merge the moment CI is green.  Self-review is expensive and
    # fragile; CI + ci_fix phase is the primary quality gate.
    repo_cfg = settings.repos.get(repo_key)
    if repo_cfg and getattr(repo_cfg, "auto_merge_on_ci_green", False):
        head_ref = ((pr_data.get("head") or {}).get("ref") or "").lower()
        is_autonomy = head_ref.startswith(("goal/", "test/", "improve/", "spec-author/"))
        if is_autonomy:
            try:
                ignored = list(getattr(repo_cfg, "ci_ignored_checks", []) or [])
                failed = gh_client.get_failed_checks(
                    owner,
                    repo,
                    pr_number,
                    pr_data=pr_data,
                    ignored_checks=ignored,
                )
                if not failed:
                    logger.info(
                        "pr_review_watcher: PR #%d auto-merging — CI green, "
                        "auto_merge_on_ci_green=True",
                        pr_number,
                    )
                    _merge_and_done(
                        state,
                        state_path,
                        pr_data,
                        gh_client,
                        owner,
                        repo,
                        settings,
                        reason="auto_merge_on_ci_green",
                    )
                    return
                logger.debug(
                    "pr_review_watcher: PR #%d CI not green (%d failed) — proceeding to self-review",
                    pr_number,
                    len(failed),
                )
            except Exception as exc:
                logger.debug("pr_review_watcher: CI check failed PR #%d — %s", pr_number, exc)

    diff = gh_client.get_pr_diff(owner, repo, pr_number)
    if not diff:
        logger.warning("pr_review_watcher: empty diff PR #%d, skipping", pr_number)
        return

    diff_excerpt = diff[:8000] + ("\n...[diff truncated]" if len(diff) > 8000 else "")
    title = pr_data.get("title", "")

    # Load optional campaign spec and Custodian findings for spec-aware review
    spec_text = _load_campaign_spec(oc_root, settings, state.get("plane_task_id"))
    custodian_text = _custodian_findings(oc_root, repo_key, settings)

    spec_section = (
        f"\n\n## Campaign spec (review against this — violations are CONCERNS)\n\n{spec_text}"
        if spec_text
        else ""
    )
    custodian_section = (
        f"\n\n## Custodian static analysis\n\n{custodian_text}" if custodian_text else ""
    )

    goal_text = (
        f"Review the following pull-request diff for correctness, style, and spec compliance.\n\n"
        f"PR #{pr_number}: {title}\n\n"
        f"```diff\n{diff_excerpt}\n```"
        f"{spec_section}"
        f"{custodian_section}\n\n"
        f"**Review checklist** (raise CONCERNS for any failure):\n"
        f"1. If a campaign spec is provided above, verify the diff implements EXACTLY what the spec\n"
        f"   requires — correct filenames, member names, member count, exports, tests, version bumps.\n"
        f"2. If Custodian findings are listed above, each finding is a CONCERN unless already fixed.\n"
        f"3. Standard code quality: correctness, style, potential bugs.\n"
        f"4. No tooling artifacts (.baseline-validation.json, run-status.md) in the diff.\n\n"
        f"Write your verdict as JSON to a file named `verdict.json` in the current working directory:\n"
        f'{{"result": "LGTM", "summary": "..."}}\n'
        f"or\n"
        f'{{"result": "CONCERNS", "summary": "bullet list of specific issues"}}\n\n'
        "Use LGTM only if ALL checklist items pass. "
        "Use CONCERNS when anything fails — be specific and actionable. "
        "Do NOT push any code changes to the repository."
    )

    logger.info(
        "pr_review_watcher: self-review PR #%d repo=%s loop=%d",
        pr_number,
        repo_key,
        state["self_review_loops"],
    )

    verdict = _run_pipeline(
        oc_root,
        config_path,
        repo_key,
        goal_text,
        settings,
        source="reviewer_self",
        state_key=state_key,
        branch_suffix=f"{state_key[:12]}",
    )

    state["self_review_loops"] += 1

    if verdict is None:
        # Pipeline produced no verdict (crash/timeout/rate-limit).
        # Retry silently up to max_self_review_loops, then auto-merge rather
        # than stalling the queue. No comment posted — don't spam the PR.
        logger.warning(
            "pr_review_watcher: no verdict PR #%d (loop=%d/%d) — %s",
            pr_number,
            state["self_review_loops"],
            reviewer.max_self_review_loops,
            "will retry"
            if state["self_review_loops"] < reviewer.max_self_review_loops
            else "auto-merging",
        )
        if state["self_review_loops"] >= reviewer.max_self_review_loops:
            _merge_and_done(
                state,
                state_path,
                pr_data,
                gh_client,
                owner,
                repo,
                settings,
                reason="no_verdict_auto_merge",
            )
        else:
            _save_state(state_path, state)
        return

    result = (verdict.get("result") or "CONCERNS").upper()
    summary = verdict.get("summary", "(no summary)")

    logger.info("pr_review_watcher: PR #%d self-review verdict=%s", pr_number, result)

    if result == "LGTM":
        _merge_and_done(
            state, state_path, pr_data, gh_client, owner, repo, settings, reason="self_review_lgtm"
        )
        return

    # CONCERNS — post once on the first pass, then retry silently.
    # Auto-merge when loops exhausted rather than escalating to humans.
    if state["self_review_loops"] == 1:
        concern_body = (
            f"{reviewer.bot_comment_marker}\n"
            f"**Self-review concerns (will auto-merge after {reviewer.max_self_review_loops} passes):**\n\n{summary}"
        )
        try:
            gh_client.post_comment(owner, repo, pr_number, concern_body)
        except Exception as exc:
            logger.warning(
                "pr_review_watcher: failed to post concern comment PR #%d — %s", pr_number, exc
            )
    else:
        logger.info(
            "pr_review_watcher: PR #%d still CONCERNS on loop %d — %s",
            pr_number,
            state["self_review_loops"],
            "retrying"
            if state["self_review_loops"] < reviewer.max_self_review_loops
            else "auto-merging",
        )

    if state["self_review_loops"] >= reviewer.max_self_review_loops:
        _merge_and_done(
            state,
            state_path,
            pr_data,
            gh_client,
            owner,
            repo,
            settings,
            reason="self_review_auto_merge",
        )
        return

    _save_state(state_path, state)


# ── Plane task lookup ─────────────────────────────────────────────────────────


def _find_plane_task_id(settings, repo_key: str, pr_number: int, _pr_data: dict) -> str | None:
    """Attempt to find a Plane 'In Review' task matching this PR. Best-effort."""
    try:
        client = _plane_client(settings)
        try:
            issues = client.list_issues()
        finally:
            client.close()
        for issue in issues:
            state_obj = issue.get("state")
            state_name = (state_obj.get("name", "") if isinstance(state_obj, dict) else "").strip()
            if state_name != "In Review":
                continue
            labels = issue.get("labels", [])
            if _label_value(labels, "repo") != repo_key:
                continue
            desc = issue.get("description") or issue.get("description_stripped") or ""
            if f"#{pr_number}" in desc or f"/{pr_number}" in desc:
                return str(issue["id"])
    except Exception as exc:
        logger.debug("pr_review_watcher: Plane task lookup failed — %s", exc)
    return None


# ── Heartbeat ──────────────────────────────────────────────────────────────────


def _write_heartbeat(status_dir: Path) -> None:
    try:
        status_dir.mkdir(parents=True, exist_ok=True)
        hb = status_dir / "heartbeat_review.json"
        hb.write_text(
            json.dumps(
                {
                    "role": "review",
                    "at": datetime.now(UTC).isoformat(),
                    "status": "active",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


# ── Poll cycle ────────────────────────────────────────────────────────────────


def _poll_once(oc_root: Path, config_path: Path, settings) -> None:
    gh_client = _github_client(settings)

    repos_to_watch = {key: repo for key, repo in settings.repos.items() if repo.await_review}

    if not repos_to_watch:
        logger.debug("pr_review_watcher: no repos with await_review=true, nothing to do")
        return

    for repo_key, repo_cfg in repos_to_watch.items():
        try:
            owner, repo = _owner_repo(repo_cfg.clone_url)
        except Exception as exc:
            logger.warning("pr_review_watcher: bad clone_url for %s — %s", repo_key, exc)
            continue

        try:
            open_prs = gh_client.list_open_prs(owner, repo)
        except Exception as exc:
            logger.warning("pr_review_watcher: failed to list PRs %s/%s — %s", owner, repo, exc)
            continue

        for pr_data in open_prs:
            if pr_data.get("draft"):
                continue

            pr_number = int(pr_data["number"])
            sp = _state_path(oc_root, repo_key, pr_number)

            if not sp.exists():
                state = _new_state(repo_key, pr_number)
                state["plane_task_id"] = _find_plane_task_id(settings, repo_key, pr_number, pr_data)
                _save_state(sp, state)
                logger.info("pr_review_watcher: discovered PR #%d repo=%s", pr_number, repo_key)

            state = _load_state(sp)
            if not state:
                continue

            phase = state.get("phase", "ci_fix")

            # human_review is removed — any state files left over from the old
            # schema drop back to self_review so they finish autonomously.
            if phase == "human_review":
                phase = "self_review"
                state["phase"] = "self_review"

            if phase == "ci_fix":
                _phase0_ci_fix(state, sp, pr_data, gh_client, owner, repo, oc_root, settings)
            elif phase == "self_review":
                _phase1(state, sp, pr_data, gh_client, owner, repo, oc_root, config_path, settings)


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OperationsCenter PR review watcher — two-phase state machine"
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--poll-interval-seconds", type=int, default=60, dest="poll_interval")
    parser.add_argument("--status-dir", type=Path, default=None, dest="status_dir")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [review] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    oc_root = Path(__file__).resolve().parents[4]
    status_dir = args.status_dir or (oc_root / "logs" / "local" / "watch-all")

    if not args.watch:
        try:
            settings = _load_settings(args.config)
            _poll_once(oc_root, args.config, settings)
        except Exception as exc:
            logger.error("pr_review_watcher: error — %s", exc, exc_info=True)
            return 1
        _write_heartbeat(status_dir)
        return 0

    logger.info("pr_review_watcher: starting — poll_interval=%ds", args.poll_interval)
    while True:
        try:
            settings = _load_settings(args.config)
            _poll_once(oc_root, args.config, settings)
        except Exception as exc:
            logger.error("pr_review_watcher: unhandled error — %s", exc, exc_info=True)
        _write_heartbeat(status_dir)
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    sys.exit(main())
