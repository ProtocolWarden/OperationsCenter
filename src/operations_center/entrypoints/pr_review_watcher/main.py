# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""PR Review Watcher — two-phase autonomous state machine for goal-lane PRs.

Phase 0 (ci_fix): when CI is failing on an autonomy PR, auto-fix the branch.
  - Runs ruff --fix + format on the local checkout and pushes.
  - Retries up to max_ci_fix_attempts; then falls through to self_review.

Phase 1 (self_review): executor reviews the diff and emits LGTM or CONCERNS.
  - LGTM → merge. This is the ONLY merge path on the self-review track
    (verdict-gated — a PR is never merged while concerns are unresolved).
  - CONCERNS → dispatch a fix pass that resolves the concerns on the PR's own
    branch (updating the open PR), then re-review next cycle. After
    max_fix_attempts without reaching LGTM, the PR is CLOSED and the issue is
    re-queued for a fresh attempt — a half-finished PR is never shipped.
  - No verdict (pipeline crash/timeout/rate-limit) → retry; after
    max_self_review_loops with no parseable verdict the PR is left OPEN and
    flagged needs-human (a reviewer outage must not destroy a good PR), and
    polling continues so a recovered backend reviews it later.

Green CI is a precondition for merge, not a trigger: a red-CI PR defers; a
green-CI PR still must pass the verdict gate. Re-queuing is bounded by
_MAX_REQUEUES (its own label); once exhausted the issue is left Blocked for a
human. There is no human_review phase — autonomy PRs reach LGTM and merge, are
closed + re-queued (concerns unresolvable), or are left open for a human
(unreviewable).

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
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from operations_center.close_invariants import (
    branch_delete_allowed_after_close,
    close_without_receipt_allowed,
)
from operations_center.reviewer.instrumentation import (
    get_instrumenter,
    record_decision_outcome,
    record_ci_gate_defer,
    record_escalation,
)

logger = logging.getLogger(__name__)

_STATE_SUBDIR = Path("state") / "pr_reviews"


# ── State file helpers ────────────────────────────────────────────────────────


def _state_key(repo_key: str, pr_number: int) -> str:
    return f"{repo_key}-{pr_number}"


def _state_path(oc_root: Path, repo_key: str, pr_number: int) -> Path:
    return oc_root / _STATE_SUBDIR / f"{_state_key(repo_key, pr_number)}.json"


def _prune_orphan_state_files(oc_root: Path, repo_key: str, open_numbers: set[int]) -> None:
    """Delete review-state files for PRs that are no longer open.

    The per-merge/close unlinks in _merge_and_done / _close_and_requeue only fire
    when THIS watcher terminates a PR. PRs merged or closed by any other means
    (a manual ``gh pr merge``, another host, or while this watcher was down/stale)
    leave their state/pr_reviews/<repo>-<n>.json behind, and they accumulate
    forever. Callers MUST pass a set built from a SUCCESSFUL list_open_prs — any
    state file for this repo whose PR is not in that set is for a terminated PR.
    A false prune (PR open but missing from a partial fetch) is self-healing: the
    next poll re-discovers the open PR and re-creates its state.
    """
    state_dir = oc_root / _STATE_SUBDIR
    if not state_dir.is_dir():
        return
    prefix = f"{repo_key}-"
    for f in state_dir.glob(f"{repo_key}-*.json"):
        if not f.stem.startswith(prefix):
            continue
        num_part = f.stem[len(prefix) :]
        if not num_part.isdigit() or int(num_part) in open_numbers:
            continue
        # A state file surviving to prune time means THIS watcher did not
        # terminate the PR — its own merge/close paths unlink first. A plain
        # orphan conflates several causes (a human gh pr merge, another host,
        # this watcher being down), so it is NOT a clean intervention signal.
        # But an orphan that was *escalated for human attention* is: the worker
        # explicitly handed the PR to a human, and the PR has now left the open
        # set, so a human resolved the escalation. That is a genuine operator
        # intervention — capture an (unjudged) ledger candidate before pruning.
        orphan = _load_state(f)
        if orphan.get("escalated_needs_human"):
            _capture_human_intervention(
                "worker-escalation-resolved-by-human", f"{repo_key}#{num_part}"
            )
        try:
            f.unlink(missing_ok=True)
            logger.info("pr_review_watcher: pruned orphan review-state %s (PR not open)", f.name)
        except Exception as exc:
            logger.debug("pr_review_watcher: prune failed for %s — %s", f.name, exc)


def _capture_human_intervention(signal: str, context: str) -> None:
    """Best-effort: append a candidate to the operator-interventions ledger.

    Calls ``cl ledger capture`` (ContextLifecycle), which appends an *unjudged*
    candidate to the ledger in the private manifest and dedups on signal+context.
    Fail-soft by design: if ``cl`` is not on PATH or no private manifest resolves,
    this is a silent no-op — capture must never wedge, slow, or fail the poll
    loop. Promotion of the candidate (the judgment line) stays manual.
    """
    try:
        subprocess.run(
            ["cl", "ledger", "capture", signal, context],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001 — capture is best-effort telemetry
        logger.debug("pr_review_watcher: ledger capture failed (%s) — %s", signal, exc)


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


def _pr_head_sha(pr_data: dict[str, Any]) -> str:
    """Return the PR head SHA when GitHub provided one, else empty string."""
    return str(((pr_data.get("head") or {}).get("sha") or "")).strip()


def _normalize_concerns_summary(summary: str) -> str:
    """Normalize a reviewer summary for stable no-progress comparisons."""
    return " ".join(str(summary).split())


# A line that begins an enumerated review concern: a bullet (-, *, +) or an
# "N." / "N)" ordinal marker. Used to split a reviewer summary into individually
# addressable concerns. See docs/design/SELF_HEAL_LADDER.md.
_CONCERN_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")


def _structure_concerns(summary: str) -> list[str]:
    """Split a freeform reviewer summary into individually-addressable concerns.

    Most reviewer summaries enumerate concerns as a bulleted or numbered list.
    Returning one string per concern lets the fix pass be told to resolve EACH
    (and, at the decompose rung, be dispatched one pass per concern). Falls back
    to paragraph splitting, then to the whole summary as a single concern —
    never returns an empty list for non-empty input."""
    text = str(summary or "").strip()
    if not text:
        return []
    items: list[str] = []
    current: list[str] = []
    saw_bullet = False
    for line in text.splitlines():
        m = _CONCERN_BULLET_RE.match(line)
        if m:
            saw_bullet = True
            if current:
                items.append("\n".join(current).strip())
            current = [m.group(1)]
        elif current:
            current.append(line.strip())  # continuation of the current item
    if current:
        items.append("\n".join(current).strip())
    if saw_bullet:
        out = [it for it in items if it]
        if out:
            return out
    # No list markers — split on blank-line paragraphs, else the whole text.
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paras if len(paras) > 1 else [text]


# The acceptance bar handed to EVERY fix pass. "Tests pass" is necessary but NOT
# sufficient: #313 shipped a symbol that was unit-tested in isolation and never
# wired into production. The bar is RESOLVING the concern — proven by the
# production call path — not by another green test. See SELF_HEAL_LADDER.md.
_FIX_ACCEPTANCE_BAR = (
    "## How each concern must be resolved\n\n"
    "- Resolve EVERY concern listed above. A pass that changes nothing, or that "
    "only addresses the concerns you find easy, is a failed pass.\n"
    "- Passing tests and linters is NECESSARY BUT NOT SUFFICIENT. If a concern is "
    "that something is defined, declared, or tested but never called/wired in "
    "production, you MUST connect it to its production call path and point to "
    "where it is invoked. Do NOT resolve such a concern by adding another test — "
    "that is exactly the defect the reviewer flagged.\n"
    "- Before finishing, run the repository's incomplete-integration gate and "
    "clear anything it reports:\n"
    "    custodian-multi --repos . --only D12,DC10 --include-deprecated --fail-on-findings\n"
    "  A D12 finding means a public symbol is tested but never wired into "
    "production; a DC10 finding means a doc claims an integration is complete "
    "while the wiring is deferred. Either means a concern is not actually "
    "resolved — fix the code/doc until the gate is clean.\n"
    "- Push to the existing branch (do NOT open a new pull request) so the open "
    "PR updates in place."
)


def _build_fix_goal(concerns: str, *, extra_context: str = "") -> str:
    """Construct the fix-pass goal: enumerated concerns + the anti-no-op
    acceptance bar, plus optional ladder enrichment (``extra_context``)."""
    items = _structure_concerns(concerns)
    if len(items) > 1:
        enumerated = "\n".join(f"{i}. {c}" for i, c in enumerate(items, 1))
        concern_block = (
            "A self-review of the currently open pull request raised "
            f"{len(items)} concerns:\n\n{enumerated}"
        )
    else:
        body = items[0] if items else str(concerns)
        concern_block = (
            "A self-review of the currently open pull request raised the "
            f"following concern:\n\n{body}"
        )
    parts = [concern_block, _FIX_ACCEPTANCE_BAR]
    if extra_context.strip():
        parts.append(extra_context.strip())
    return "\n\n".join(parts)


# How much of the PR diff to fold into the L1 enrichment for orientation. The
# fix worker clones the branch and can run `git diff` itself, so this is a
# pointer, not the source of truth — keep it bounded so the prompt stays small.
_LADDER_DIFF_CAP = 8_000


def _ladder_enrichment(level: int, *, pr_diff: str = "") -> str:
    """Per-rung enrichment handed to ``_run_fix_pass`` as ``extra_context``.

    Level 0 is the standard pass (no enrichment). Each higher rung adds
    resolving power instead of conceding to a human:

    - **L1** — the previous pass changed nothing; do not repeat it. Fold in a
      bounded slice of the PR diff for orientation.
    - **L2+** — decompose: resolve ONE concern per pass (narrower scope, higher
      resolve rate); the rest are picked up on following passes.

    See docs/design/SELF_HEAL_LADDER.md."""
    if level <= 0:
        return ""
    parts = [
        "## Earlier passes did not resolve these concerns",
        "A previous automated fix pass on this same branch changed nothing (or "
        "left the concerns above unresolved). Do NOT repeat the same approach — "
        "read the actual code paths involved and take a concretely different one.",
    ]
    if level >= 2:
        parts.append(
            "Resolving every concern at once has failed. This pass, pick the "
            "SINGLE most important still-unresolved concern and fully resolve "
            "just that one — wire it end to end and show the call path. The "
            "remaining concerns are handled on the following passes."
        )
    diff = (pr_diff or "").strip()
    if diff:
        if len(diff) > _LADDER_DIFF_CAP:
            diff = diff[:_LADDER_DIFF_CAP] + "\n...[diff truncated for orientation]"
        parts.append("## The PR diff under review (for orientation)\n\n```diff\n" + diff + "\n```")
    return "\n\n".join(parts)


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


class OCSourceTreeUncleanError(RuntimeError):
    """The OC source tree used to RUN the reviewer is broken — e.g. a concurrent
    session (watchdog merge, fix pass) left git conflict markers in a tracked
    source file. This is an ENVIRONMENT failure, not a PR-quality failure: the
    planning subprocess imports the ``operations_center`` package from
    ``oc_root/src`` and would crash with SyntaxError at import time *for every
    PR*, regardless of the diff under review. Surfaced distinctly so it never
    burns a PR's review budget or reads as "no verdict"."""


class ReviewerBackendError(RuntimeError):
    """The reviewer backend (``claude`` CLI) crashed, was killed, or timed out.

    This is an INFRA failure, not a PR-quality failure.  The PR may be perfectly
    good; the backend just can't review it right now (e.g. a transient rate-limit,
    OOM kill, or process timeout).  Surfaced distinctly so crashes never burn the
    PR's ``no_verdict_passes`` budget — only a clean exit with no verdict.json
    written counts against that budget."""


def _oc_source_conflict_markers(oc_root: Path) -> list[str]:
    """Tracked ``src/`` Python files containing git conflict markers.

    Empty list means the import path is clean. A non-empty result means the
    reviewer cannot run until the tree is repaired — see OCSourceTreeUncleanError.
    Cheap (single ``git grep``); fail-open (returns [] if git is unavailable)
    so this guard can never itself wedge the reviewer."""
    try:
        out = subprocess.run(
            ["git", "grep", "-lE", r"^(<<<<<<< |>>>>>>> |=======$)", "--", "src/"],
            cwd=oc_root,
            capture_output=True,
            text=True,
        )
    except Exception:  # noqa: BLE001 — guard must never raise from detection
        return []
    if out.returncode not in (0, 1):  # 1 = no matches (clean); >1 = git error
        return []
    return [line for line in out.stdout.splitlines() if line.strip().endswith(".py")]


def _label_value(labels: list, prefix: str) -> str:
    for lab in labels:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        if name.lower().startswith(prefix.lower() + ":"):
            return name.split(":", 1)[1].strip()
    return ""


def _durable_pr_head_ref(pr_number: int) -> str:
    return f"refs/pull/{pr_number}/head"


def _spec_file_from_plane_issue(issue: dict[str, Any]) -> str:
    labels = issue.get("labels", []) or []
    desc = str(issue.get("description") or issue.get("description_stripped") or "").strip()
    if desc:
        try:
            from operations_center.application.task_parser import TaskParser

            parsed = TaskParser().parse(desc, labels=_label_names(labels))
            spec_file = str(parsed.execution_metadata.get("spec_file") or "").strip()
            if spec_file:
                return spec_file
        except Exception:
            pass

    campaign_id = _label_value(labels, "campaign-id")
    if not campaign_id:
        return ""
    try:
        from operations_center.spec_author.state import CampaignStateManager

        campaigns_state = CampaignStateManager().load()
        for campaign in campaigns_state.campaigns:
            if campaign.campaign_id == campaign_id:
                return str(campaign.spec_file).strip()
    except Exception:
        pass
    return ""


def _record_close_receipt(
    settings,
    plane_task_id: str,
    *,
    pr_number: int,
    pr_data: dict[str, Any],
    reason: str,
) -> str:
    """Record a durable salvage receipt on the Plane task before closing."""
    client = _plane_client(settings)
    try:
        issue = client.fetch_issue(plane_task_id)
        spec_file = _spec_file_from_plane_issue(issue)
        if not spec_file:
            return ""
        branch_ref = str(((pr_data.get("head") or {}).get("ref") or "")).strip()
        lines = [
            f"Close receipt for PR #{pr_number} (`{reason}`)",
            f"durable_head_ref: `{_durable_pr_head_ref(pr_number)}`",
            f"spec_file: `{spec_file}`",
        ]
        if branch_ref:
            lines.append(f"closed_branch: `{branch_ref}`")
        client.comment_issue(plane_task_id, "\n".join(lines))
        return spec_file
    finally:
        client.close()


# ── pr review pipeline ────────────────────────────────────────────────────────


def _run_direct_review(
    oc_root: Path,
    goal_text: str,
    state_key: str,
) -> dict | None:
    """Run a self-review via a direct ``claude -p`` call in an empty temp directory.

    Bypasses the TeamExecutor pipeline so the workspace's CLAUDE.md cannot
    override the review goal.  The diff is already embedded in ``goal_text``
    so no repo clone is needed.  Returns the parsed verdict dict or None.
    """
    conflicted = _oc_source_conflict_markers(oc_root)
    if conflicted:
        raise OCSourceTreeUncleanError(
            f"OC source tree at {oc_root} has git conflict markers in "
            f"{len(conflicted)} tracked file(s) "
            f"({', '.join(conflicted[:3])}{'…' if len(conflicted) > 3 else ''}) "
            "— a concurrent session left the shared checkout dirty; refusing "
            "to run the reviewer (it would crash at import)."
        )

    with tempfile.TemporaryDirectory(prefix="oc-review-direct-") as tmpdir:
        tmp = Path(tmpdir)
        verdict_path = tmp / "verdict.json"
        try:
            proc = subprocess.run(
                [
                    "claude",
                    "--model",
                    "claude-haiku-4-5-20251001",
                    "-p",
                    "--effort",
                    "low",
                    goal_text,
                ],
                cwd=str(tmp),
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            raise ReviewerBackendError(f"direct review timed out (300s) for state_key={state_key}")
        if verdict_path.exists():
            try:
                return json.loads(verdict_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning(
                    "pr_review_watcher: malformed verdict.json from direct review for %s",
                    state_key,
                )
                return None  # clean exit, bad JSON — genuine no-verdict
        # No verdict.json written.
        if proc.returncode != 0:
            # Non-zero exit = crash, signal kill, or rate-limit — infra failure,
            # not a PR quality problem.  Don't charge the no_verdict budget.
            stdout_tail = (proc.stdout or "").strip()[-500:]
            raise ReviewerBackendError(
                f"reviewer process exited with rc={proc.returncode} "
                f"for state_key={state_key} (stdout_tail={stdout_tail!r})"
            )
        # returncode == 0, no verdict.json — the reviewer ran cleanly but chose
        # not to write a file.  Genuine no-verdict; charge the budget.
        stdout_tail = (proc.stdout or "").strip()[-500:]
        logger.warning(
            "pr_review_watcher: no verdict.json from direct review for %s (rc=0, stdout_tail=%r)",
            state_key,
            stdout_tail,
        )
        return None


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
    task_branch: str | None = None,
    return_result: bool = False,
) -> dict | None:
    """Run worker.main → execute.main.

    By default returns the parsed ``verdict.json`` (review pass). When
    ``return_result`` is True, returns the parsed ``result.json`` execution
    outcome instead (fix pass — no verdict is produced). ``task_branch``
    overrides the branch the executor commits to; when None a throwaway
    ``review/<suffix>`` branch is used. Returns None on failure."""
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
        # Pre-flight: the planning subprocess imports operations_center from
        # oc_root/src. If a concurrent session left conflict markers there, the
        # import crashes with SyntaxError → "produced no JSON" for every PR.
        # Detect it here and surface it as a distinct ENVIRONMENT failure so the
        # caller skips the review (retry next sweep) instead of charging it to
        # the PR's no-verdict budget. (Root cause of the 2026-06-07 reviewer
        # outage: a marker in cxrp_mapper.py blocked all verdicts for ~4h.)
        conflicted = _oc_source_conflict_markers(oc_root)
        if conflicted:
            raise OCSourceTreeUncleanError(
                f"OC source tree at {oc_root} has git conflict markers in "
                f"{len(conflicted)} tracked file(s) "
                f"({', '.join(conflicted[:3])}{'…' if len(conflicted) > 3 else ''}) "
                "— a concurrent session left the shared checkout dirty; refusing "
                "to run the reviewer (it would crash at import)."
            )

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
            task_branch or f"review/{branch_suffix}",
            "--output",
            str(result_file),
            "--source",
            source,
        ]
        # Use Popen + start_new_session so the entire process group (including
        # grandchildren like pytest-spawned claude subprocesses) can be killed on
        # timeout.  subprocess.run with capture_output=True only kills the direct
        # child on TimeoutExpired; grandchildren keep the pipe open and
        # communicate() blocks indefinitely.
        _exec_popen = subprocess.Popen(
            exec_cmd,
            cwd=oc_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            _stdout, _stderr = _exec_popen.communicate(
                timeout=1800,  # 30 min hard cap — prevents hung executor blocking the watcher
            )
            exec_proc = subprocess.CompletedProcess(
                exec_cmd, _exec_popen.returncode, stdout=_stdout, stderr=_stderr
            )
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(_exec_popen.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            _exec_popen.wait()
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

        if return_result:
            if result_file.exists():
                try:
                    return json.loads(result_file.read_text(encoding="utf-8"))
                except Exception:
                    logger.warning(
                        "pr_review_watcher: malformed result.json for state_key=%s", state_key
                    )
            return None

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


# How many auto-rebase attempts before a CONFLICTING PR is escalated for a human.
# Orthogonal to fix_attempts — a rebase is infrastructure work, not a fix, and must
# never consume the fix budget (that would wrongly close a good PR).
_MAX_REBASE_ATTEMPTS = 3
# Grace window after a rebase push: main moves constantly, so re-rebasing within
# this window would thrash (rebase → push → main moves → rebase …). Defer instead.
_REBASE_GRACE_SECONDS = 120


def _attempt_auto_rebase(repo_cfg, head_ref: str, settings, pr_number: int) -> str:
    """Merge the base branch into a CONFLICTING PR's branch and push, in the
    repo's persistent clone (never oc_root).

    Returns one of:
      "clean"         — base merged with no real conflict; merge commit pushed.
      "conflict"      — real (non-log) conflict remained; merge aborted, nothing pushed.
      "push_rejected" — push lost a race (branch moved); reset, nothing landed.
      "noop"          — branch already current with base; nothing to do.
      "unavailable"   — no local clone / token configured; cannot rebase here.
      "error"         — anything else; defensive, never raises.

    Safety: only ever creates a *merge commit* (branch moves forward only — no
    force-push, no history rewrite). `.console/log.md` auto-resolves via a
    union driver injected through .git/info/attributes (works even when the PR
    branch predates the committed .gitattributes). A textually-clean-but-wrong
    merge is NOT trusted here — the caller does not merge the result this cycle;
    CI re-runs on the pushed commit and the next review re-validates it."""
    local_path = getattr(repo_cfg, "local_path", None) if repo_cfg else None
    if not local_path or not Path(local_path).exists():
        return "unavailable"
    local_path = Path(local_path)
    default_branch = getattr(repo_cfg, "default_branch", "main") or "main"

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
        # Inject the union driver for the append-only journal so concurrent log
        # entries auto-keep-both instead of conflicting. .git/info/attributes is
        # local and always applied — no dependency on the branch's committed copy.
        info_dir = local_path / ".git" / "info"
        info_dir.mkdir(parents=True, exist_ok=True)
        (info_dir / "attributes").write_text(".console/log.md merge=union\n", encoding="utf-8")

        _git("stash", "--include-untracked")
        _git("fetch", "origin", head_ref, default_branch)
        if _git("checkout", "-B", head_ref, f"origin/{head_ref}").returncode != 0:
            return "error"

        merge = _git("merge", "--no-edit", f"origin/{default_branch}")
        if merge.returncode == 0:
            if "Already up to date" in (merge.stdout or ""):
                return "noop"
            # Merged cleanly per git — but a real (non-log) conflict path would
            # have left the merge unfinished; double-check there are none.
            if _git("diff", "--diff-filter=U", "--name-only").stdout.strip():
                _git("merge", "--abort")
                return "conflict"
            push = _git("push", "origin", f"HEAD:{head_ref}")
            if push.returncode == 0:
                return "clean"
            _git("reset", "--hard", f"origin/{head_ref}")
            return "push_rejected"

        # Non-zero merge: conflicts. If every unmerged path is the log (union
        # should have handled it but be defensive), there are none here → real
        # conflict. Abort; never force a resolution.
        _git("merge", "--abort")
        return "conflict"
    except Exception as exc:  # noqa: BLE001 — rebase must never crash the watcher
        logger.warning("pr_review_watcher: auto-rebase PR #%d errored — %s", pr_number, exc)
        return "error"


_DOC_PATH_SUFFIXES = (".md", ".markdown", ".rst", ".txt")


def _is_doc_path(path: str) -> bool:
    """True for documentation files — any doc extension, or anything under docs/."""
    p = path.strip().lower()
    return bool(p) and (p.endswith(_DOC_PATH_SUFFIXES) or p.startswith("docs/") or "/docs/" in p)


def _files_from_diff(diff: str) -> list[str]:
    """Extract changed file paths from a unified diff's ``diff --git a/X b/Y`` headers."""
    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split(" b/", 1)
            if len(parts) == 2 and parts[1].strip():
                files.append(parts[1].strip())
    return files


def _diff_is_docs_only(files) -> bool:
    """True when every changed file is documentation (and there is at least one).

    A docs-only diff gets a review rubric that does NOT demand in-diff proof of
    facts a document legitimately references but cannot contain (CI runs, secrets,
    sibling/other-repo PRs) — the over-flagging that looped #334.
    """
    fs = [f for f in (files or []) if f]
    return bool(fs) and all(_is_doc_path(f) for f in fs)


# Review rubric injected when the diff is documentation-only — see _diff_is_docs_only.
_DOC_ONLY_REVIEW_RUBRIC = (
    "\n\n## This diff is DOCUMENTATION-ONLY — apply the docs rubric\n"
    "Every changed file is documentation. Review it for **internal consistency, "
    "accuracy against the repository's actual state, broken cross-references, and "
    "clarity**. Documentation legitimately summarizes and points to work that lives "
    "OUTSIDE this diff (CI runs, secrets, sibling PRs, other repos). Therefore you "
    "MUST NOT raise CONCERNS of the form 'unverifiable in the diff', 'lacks CI "
    "output / test evidence', 'claims changes not shown here', or 'references work "
    "outside this diff' — demanding in-diff proof of an external fact is NOT a valid "
    "concern for a docs PR. Raise CONCERNS only for statements that CONTRADICT the "
    "repository's actual state, broken/dead references, or genuinely incoherent prose."
)


_REVIEWER_VERDICT_STATUS_CONTEXT = "reviewer-verdict"


def _publish_reviewer_verdict(
    gh_client,
    owner: str,
    repo: str,
    sha: str | None,
    *,
    result: str,
    description: str,
) -> None:
    """Publish the reviewer's verdict as a commit status on the PR head SHA.

    This makes the (otherwise comment-only) verdict a first-class status check
    so it can be marked *required* in branch protection — closing the gap where
    a manual ``gh pr merge`` bypasses an unresolved CONCERNS verdict. Until the
    reviewer posts ``success`` (LGTM), the context is ``failure``/absent and the
    merge is blocked, for the fleet and humans alike.

    Best-effort: a status-post failure must never crash the review loop.
    """
    if not sha:
        return
    try:
        gh_client.set_commit_status(
            owner,
            repo,
            sha,
            state=result,
            context=_REVIEWER_VERDICT_STATUS_CONTEXT,
            description=description,
        )
    except Exception as exc:  # noqa: BLE001 — status publishing is best-effort
        logger.warning(
            "pr_review_watcher: failed to publish %s status on %s — %s",
            _REVIEWER_VERDICT_STATUS_CONTEXT,
            sha[:8],
            exc,
        )


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
    # get_mergeable() returns None while GitHub is still computing; treat that as
    # "unknown, try anyway" so we don't hold up clean PRs during GitHub's lazy eval.
    # False means a real conflict with the base — LAZY auto-rebase fires here, and
    # ONLY here (verdict is already LGTM): never eagerly per-poll, which would storm
    # every conflicting PR each time main moves.
    if gh_client.get_mergeable(owner, repo, pr_number) is False:
        _auto_rebase_or_escalate(state, state_path, gh_client, owner, repo, settings, reason)
        return
    state["rebase_attempts"] = 0  # mergeable — clear any rebase bookkeeping
    # Bless this head with reviewer-verdict=success BEFORE merging, so the
    # required status check is satisfied for the fleet's own merge — and so the
    # non-LGTM merge paths (e.g. ci_validated_after_retraction) also clear the
    # gate. GitHub records the status synchronously; a brief propagation lag at
    # most causes one retry on the next poll.
    _publish_reviewer_verdict(
        gh_client,
        owner,
        repo,
        _pr_head_sha(_pr_data),
        result="success",
        description=f"reviewer approved ({reason})",
    )
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

    _retract_flag(state, gh_client, owner, repo, resolution="PR merged")

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


def _auto_rebase_or_escalate(
    state: dict,
    state_path: Path,
    gh_client,
    owner: str,
    repo: str,
    settings,
    reason: str,
) -> None:
    """LGTM PR is CONFLICTING — try one bounded, grace-gated auto-rebase.

    On a clean rebase we push the merge commit and STOP for this cycle: CI
    re-runs on the merged tree and the next review re-validates it before any
    merge to main. This is the backstop for a textually-clean-but-semantically
    -wrong merge (broken import, budget overflow, silent hunk loss) that the
    bot's ephemeral clone would not catch via local pre-push hooks. A real
    conflict escalates for a human; we never force a resolution."""
    pr_number = state["pr_number"]
    state.setdefault("rebase_attempts", 0)

    # Grace: main moves constantly; re-rebasing within the window thrashes.
    last = state.get("last_rebase_at")
    if last:
        try:
            elapsed = (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds()
            if elapsed < _REBASE_GRACE_SECONDS:
                logger.info(
                    "pr_review_watcher: PR #%d CONFLICTING but rebased %ds ago — "
                    "deferring (main may be moving)",
                    pr_number,
                    int(elapsed),
                )
                return
        except ValueError:
            pass

    if state["rebase_attempts"] >= _MAX_REBASE_ATTEMPTS:
        _escalate_needs_human(
            state,
            state_path,
            gh_client,
            owner,
            repo,
            settings,
            reason="rebase_attempts_exhausted",
            detail=(
                f"PR is CONFLICTING and {_MAX_REBASE_ATTEMPTS} auto-rebase attempts did "
                "not yield a mergeable branch (base may be moving faster than CI/review, "
                "or the conflict recurs). Needs a manual rebase."
            ),
        )
        return

    head_ref = (state.get("head_ref") or "").strip()
    if not head_ref:
        logger.warning(
            "pr_review_watcher: PR #%d CONFLICTING but no head_ref recorded — cannot rebase",
            pr_number,
        )
        return

    repo_cfg = settings.repos.get(state["repo_key"])
    outcome = _attempt_auto_rebase(repo_cfg, head_ref, settings, pr_number)
    # last_rebase_at gates the grace window on every *attempt* (success or not),
    # so a fast-moving base cannot trigger back-to-back rebases.
    state["last_rebase_at"] = datetime.now(UTC).isoformat()

    if outcome == "clean":
        state["rebase_attempts"] += 1
        logger.info(
            "pr_review_watcher: PR #%d auto-rebased onto base (attempt %d/%d) — pushed; "
            "CI will re-run and review re-validates next cycle before merge",
            pr_number,
            state["rebase_attempts"],
            _MAX_REBASE_ATTEMPTS,
        )
        _save_state(state_path, state)
    elif outcome == "conflict":
        _escalate_needs_human(
            state,
            state_path,
            gh_client,
            owner,
            repo,
            settings,
            reason="rebase_conflict",
            detail=(
                "Auto-rebase onto the base branch hit a real code conflict "
                "(beyond the union-merged journal). Manual rebase required."
            ),
        )
    else:
        # noop / push_rejected / unavailable / error — log and retry next cycle
        # (grace window throttles). Not charged against rebase_attempts: no merge
        # commit landed, so it is not a consumed attempt.
        logger.info(
            "pr_review_watcher: PR #%d auto-rebase outcome=%s — will retry next cycle",
            pr_number,
            outcome,
        )
        _save_state(state_path, state)


# ── Fix pass + close/re-queue ─────────────────────────────────────────────────

# How many times an issue may be re-queued for a fresh attempt before it is
# left Blocked for a human. Bounds the close→re-queue→new-PR cycle so an
# unfixable issue can't loop forever, while still never merging half-finished.
_MAX_REQUEUES = 3

# How many polls a PR may wait for red CI to go green before it is escalated to
# a human (rather than deferring forever — a persistently-red required check
# must not silently stall the loop, nor merge on red).
_MAX_CI_WAIT_CYCLES = 20

# WO-3: when a PR is escalated (same head, no new push) but CI is fully green,
# retract the escalation and allow the reviewer to re-evaluate. Bounded to
# prevent infinite escalation→retraction loops on PRs whose concerns cannot be
# resolved by automation alone (e.g. diff-truncation false positives).
# 3 allows recovery from: rebase_conflict + ci_never_settled + one genuine
# concern cycle, without enabling runaway loops.
_MAX_CI_GREEN_RETRACTIONS = 3
_DIFF_LIMIT = 60_000


def _run_fix_pass(
    oc_root: Path,
    config_path: Path,
    repo_key: str,
    head_ref: str,
    concerns: str,
    settings,
    *,
    state_key: str,
    extra_context: str = "",
) -> bool:
    """Dispatch a worker pass that resolves review concerns on the PR's own
    branch and pushes (updating the open PR). Returns True only if the pass
    actually pushed changes to the branch — a no-op pass (worker couldn't
    resolve anything) returns False so the caller can log it; the next review
    cycle re-evaluates the actual diff regardless.

    The goal enumerates the concerns and carries the anti-no-op acceptance bar
    (tests passing is necessary but not sufficient; a tested-but-unwired symbol
    must be wired, not re-tested). ``extra_context`` carries the Self-Heal
    Ladder's per-rung enrichment (e.g. the PR diff, or "the previous pass
    changed nothing — take a different approach")."""
    goal_text = _build_fix_goal(concerns, extra_context=extra_context)
    try:
        outcome = _run_pipeline(
            oc_root,
            config_path,
            repo_key,
            goal_text,
            settings,
            source="reviewer_fix",
            state_key=state_key,
            branch_suffix=f"{state_key[:12]}",
            task_branch=head_ref,
            return_result=True,
        )
    except OCSourceTreeUncleanError as exc:
        # Environment problem, not a fix-pass failure — skip this sweep, no churn.
        logger.error("pr_review_watcher: fix pass skipped — %s", exc)
        return False
    if not isinstance(outcome, dict):
        return False
    result = outcome.get("result")
    if not isinstance(result, dict):
        result = outcome
    # Only "branch_pushed" proves the diff changed. result.success is True even
    # for a no-op pass (executor ran cleanly but committed nothing), which would
    # mask a worker that resolved nothing — don't count that as a push.
    return bool(result.get("branch_pushed"))


# Dedicated label for reviewer re-queues — kept separate from board_worker's
# `retry-count` (executor-kill/transient retries) so the two budgets don't
# consume each other.
_REQUEUE_LABEL_PREFIX = "reviewer-requeue-count"


def _label_names(labels: list) -> list[str]:
    out = []
    for lab in labels or []:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        if name:
            out.append(name)
    return out


def _requeue_count(labels: list) -> int:
    raw = _label_value(labels, _REQUEUE_LABEL_PREFIX)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _labels_with_requeue_count(
    labels: list, count: int, *, extra: list[str] | None = None
) -> list[str]:
    """Full replacement label set: existing names minus the old requeue-count
    (and any in *extra*, to avoid dupes), plus the new count and *extra*."""
    drop = {_REQUEUE_LABEL_PREFIX.lower()} | {e.lower() for e in (extra or [])}
    kept = [n for n in _label_names(labels) if n.split(":", 1)[0].strip().lower() not in drop]
    return kept + [f"{_REQUEUE_LABEL_PREFIX}: {count}", *(extra or [])]


def _retract_flag(
    state: dict,
    gh_client,
    owner: str,
    repo: str,
    *,
    resolution: str,
) -> None:
    """Strike-through any open escalation or self-review flag comments.

    Edits the stored comment in-place so operators can see the flag was
    automatically cleared and why, rather than it silently persisting on a
    merged or resumed PR.
    """
    pr_number = state["pr_number"]
    for key in ("escalation_comment_id", "concerns_comment_id"):
        comment_id = state.pop(key, None)
        if not comment_id:
            continue
        try:
            comments = gh_client.list_pr_comments(owner, repo, pr_number)
            body = next((c["body"] for c in comments if c.get("id") == comment_id), None)
            if body is None:
                continue
            new_body = re.sub(
                r"\*\*(Needs human attention|Self-review concerns)\*\*",
                r"~~**\1**~~",
                body,
                count=1,
            )
            new_body = f"> **Resolved**: {resolution}\n\n" + new_body
            gh_client.update_comment(owner, repo, comment_id, new_body)
            logger.info(
                "pr_review_watcher: retracted flag comment %d for PR #%d (%s)",
                comment_id,
                pr_number,
                resolution,
            )
        except Exception as exc:
            logger.warning(
                "pr_review_watcher: failed to retract flag comment %d for PR #%d — %s",
                comment_id,
                pr_number,
                exc,
            )


def _escalate_needs_human(
    state: dict,
    state_path: Path,
    gh_client,
    owner: str,
    repo: str,
    settings,
    *,
    reason: str,
    detail: str,
    current_head_sha: str | None = None,
) -> None:
    """Leave the PR OPEN and flag it for a human. Used when the PR must not be
    merged (unresolved) but also must not be closed (work would be lost) — e.g.
    the review pipeline is persistently unavailable, or there is no Plane task
    to re-queue. Comments exactly once, then keeps polling."""
    pr_number = state["pr_number"]
    if current_head_sha:
        state["escalated_head_sha"] = current_head_sha
    if not state.get("escalated_needs_human"):
        marker = settings.reviewer.bot_comment_marker
        try:
            resp = gh_client.post_comment(
                owner,
                repo,
                pr_number,
                f"{marker}\n**Needs human attention** (reason=`{reason}`). Left open — "
                f"not merged (unresolved) and not closed (work preserved).\n\n{detail}",
            )
            state["escalation_comment_id"] = (resp or {}).get("id")
        except Exception as exc:
            logger.warning(
                "pr_review_watcher: failed to post needs-human comment PR #%d — %s", pr_number, exc
            )
        state["escalated_needs_human"] = True
        logger.warning(
            "pr_review_watcher: PR #%d escalated for human attention (reason=%s)", pr_number, reason
        )
    _save_state(state_path, state)


def _close_and_requeue(
    state: dict,
    state_path: Path,
    pr_data: dict,
    gh_client,
    owner: str,
    repo: str,
    settings,
    *,
    reason: str,
    detail: str,
    concerns: str = "",
) -> None:
    """Close a PR WITHOUT merging and re-queue its issue for a fresh attempt.

    The verdict gate's escape hatch: when a PR cannot reach LGTM it is never
    merged half-finished. Re-queue happens FIRST — the PR is only closed once
    the issue is safely back in the queue, so a Plane outage can't lose the
    work. With no Plane task to re-queue, the PR is left open + escalated rather
    than closed into the void.

    ``concerns`` carries the still-unresolved review concerns onto the re-queued
    task so the fresh attempt is scoped to what actually remained (Self-Heal
    Ladder Phase 3) instead of starting blind."""
    pr_number = state["pr_number"]
    plane_task_id = state.get("plane_task_id")

    if not plane_task_id:
        logger.warning(
            "pr_review_watcher: PR #%d has no Plane task — escalating instead of closing "
            "(closing would lose the work)",
            pr_number,
        )
        _escalate_needs_human(
            state,
            state_path,
            gh_client,
            owner,
            repo,
            settings,
            reason=f"{reason}:no_task",
            detail=detail,
        )
        return

    # Re-queue first; only close if it succeeded.
    if not _requeue_plane_task(
        settings, plane_task_id, pr_number=pr_number, reason=reason, concerns=concerns
    ):
        logger.warning(
            "pr_review_watcher: re-queue failed for PR #%d — leaving PR open, will retry",
            pr_number,
        )
        _save_state(state_path, state)
        return

    try:
        spec_file = _record_close_receipt(
            settings,
            plane_task_id,
            pr_number=pr_number,
            pr_data=pr_data,
            reason=reason,
        )
    except Exception as exc:
        logger.warning(
            "pr_review_watcher: failed to record close receipt for PR #%d — %s",
            pr_number,
            exc,
        )
        _save_state(state_path, state)
        return
    if not spec_file:
        logger.warning(
            "pr_review_watcher: PR #%d missing spec linkage for close receipt — leaving PR open",
            pr_number,
        )
        _save_state(state_path, state)
        return

    marker = settings.reviewer.bot_comment_marker
    close_comment = (
        f"{marker}\n**Closing without merge** (reason=`{reason}`). A PR is never "
        f"merged with unresolved review concerns — the issue has been re-queued for "
        f"a fresh attempt. Durable receipt recorded on Plane task `{plane_task_id}` "
        f"for `{_durable_pr_head_ref(pr_number)}` and `{spec_file}`.\n\n{detail}"
    )
    if not close_without_receipt_allowed(comment=close_comment, durable_receipt_recorded=True):
        logger.error(
            "pr_review_watcher: invariant rejected close for PR #%d despite recorded receipt",
            pr_number,
        )
        _save_state(state_path, state)
        return
    try:
        gh_client.post_comment(owner, repo, pr_number, close_comment)
    except Exception as exc:
        logger.warning(
            "pr_review_watcher: failed to comment before close PR #%d — %s", pr_number, exc
        )
    try:
        gh_client.close_pr(owner, repo, pr_number)
        logger.info("pr_review_watcher: closed PR #%d without merge (reason=%s)", pr_number, reason)
    except Exception as exc:
        # Issue is already re-queued; the open PR is gated from double-claim by
        # OPEN_PR_GATE. Keep state so the close is retried next cycle.
        logger.error("pr_review_watcher: failed to close PR #%d — %s", pr_number, exc)
        _save_state(state_path, state)
        return

    # Delete the head branch so closed-PR branches don't accumulate as orphans.
    head_ref = (pr_data.get("head") or {}).get("ref") or ""
    if head_ref and branch_delete_allowed_after_close(
        comment=close_comment,
        durable_receipt_recorded=True,
    ):
        try:
            gh_client.delete_branch(owner, repo, head_ref)
        except Exception as exc:
            logger.warning(
                "pr_review_watcher: failed to delete branch %s for PR #%d — %s",
                head_ref,
                pr_number,
                exc,
            )
    elif head_ref:
        logger.warning(
            "pr_review_watcher: retained branch %s for PR #%d because close comment "
            "still claims preserved work",
            head_ref,
            pr_number,
        )

    _retract_flag(state, gh_client, owner, repo, resolution="PR closed and re-queued")
    state_path.unlink(missing_ok=True)


def _requeue_plane_task(
    settings, plane_task_id: str, *, pr_number: int, reason: str, concerns: str = ""
) -> bool:
    """Send the issue back to the queue for a fresh attempt, bounded by
    ``_MAX_REQUEUES`` (its own dedicated label); once exhausted, leave it
    Blocked for a human. Returns True if the issue was handled (re-queued or
    blocked), False on failure (e.g. Plane unreachable) so the caller can keep
    the PR open and retry.

    ``concerns`` (the still-unresolved review concerns) is appended to the
    re-queue/blocked comment so the next attempt is scoped to what actually
    remained — the closed PR's branch is gone, but its lesson is not."""
    from operations_center.entrypoints.board_worker.labels import STATE_BLOCKED, STATE_READY

    # Structured, enumerated concerns for the next attempt to address — the same
    # parse the fix pass uses, so the carry-forward reads consistently.
    scope_block = ""
    items = _structure_concerns(concerns)
    if items:
        enumerated = "\n".join(f"{i}. {c}" for i, c in enumerate(items, 1))
        scope_block = (
            "\n\n**Unresolved review concerns to address in the next attempt** "
            "(the previous PR could not resolve these — scope the fresh attempt to "
            f"them, do not start blind):\n\n{enumerated}"
        )

    try:
        client = _plane_client(settings)
    except Exception as exc:
        logger.warning(
            "pr_review_watcher: cannot open Plane client to re-queue task=%s — %s",
            plane_task_id,
            exc,
        )
        return False
    try:
        issue = client.fetch_issue(plane_task_id)
        labels = issue.get("labels", []) or []
        attempts = _requeue_count(labels)
        if attempts >= _MAX_REQUEUES:
            client.update_issue_labels(
                plane_task_id, _labels_with_requeue_count(labels, attempts, extra=["needs-human"])
            )
            client.transition_issue(plane_task_id, STATE_BLOCKED)
            client.comment_issue(
                plane_task_id,
                f"PR #{pr_number} closed ({reason}); re-queue limit "
                f"({_MAX_REQUEUES}) reached — blocked for human review.{scope_block}",
            )
            logger.warning("pr_review_watcher: task=%s hit re-queue limit — Blocked", plane_task_id)
        else:
            client.update_issue_labels(
                plane_task_id, _labels_with_requeue_count(labels, attempts + 1)
            )
            client.transition_issue(plane_task_id, STATE_READY)
            client.comment_issue(
                plane_task_id,
                f"PR #{pr_number} closed ({reason}); re-queued for a fresh attempt "
                f"(#{attempts + 1} of {_MAX_REQUEUES}).{scope_block}",
            )
            logger.info(
                "pr_review_watcher: re-queued task=%s to Ready (attempt %d)",
                plane_task_id,
                attempts + 1,
            )
        return True
    except Exception as exc:
        logger.warning("pr_review_watcher: re-queue failed task=%s — %s", plane_task_id, exc)
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass


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
        system_ruff = shutil.which("ruff")
        if system_ruff:
            ruff_bin = Path(system_ruff)
        else:
            oc_ruff = oc_root / ".venv" / "bin" / "ruff"
            ruff_bin = oc_ruff if oc_ruff.exists() else Path("ruff")

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
    current_head_sha = _pr_head_sha(pr_data)

    previous_concerns_head_sha = str(state.get("last_concerns_head_sha") or "").strip()
    # Only reset the fix/escalation budget when the head moved because of an
    # EXTERNAL push (a human, or another host) — that is genuinely new work to
    # review fresh. When the head moved because OUR OWN fix pass pushed it, the
    # budget must keep accumulating, otherwise every self-pushed fix resets the
    # counter and the PR loops forever instead of escalating to a human (the
    # #334 non-convergence: 7 self-pushes, fix_attempts stuck at 1, piling on
    # evidence files). last_fix_push_sha is the head our last fix pass produced.
    last_fix_push_sha = str(state.get("last_fix_push_sha") or "").strip()
    # Restart-safe recognition of our own fix-push. In steady state the head
    # matches the SHA we recorded after the pass (last_fix_push_sha). But if the
    # watcher was interrupted BETWEEN the fix-push and that recording — e.g. a
    # long fix pass kills the process (seen on #337) — last_fix_push_sha is lost
    # and a naive guard would mistake our own push for an external one and reset
    # the budget (re-opening the #334 loop). Recover from durable state that
    # survives the pre-fix save: when we have an active fix cycle
    # (`fix_attempts > 0`) but the pass outcome was never recorded
    # (`last_fix_pass_pushed` is popped at dispatch start and only re-set when the
    # pass completes), a head move is almost certainly that interrupted pass's
    # push — treat it as ours. (A poll never observes this mid-dispatch: the
    # dispatch is synchronous within one poll, so the unrecorded state is only
    # ever seen after a restart.)
    _fix_dispatch_unrecorded = (
        state.get("fix_attempts", 0) > 0 and "last_fix_pass_pushed" not in state
    )
    _is_our_fix_push = (
        bool(last_fix_push_sha) and current_head_sha == last_fix_push_sha
    ) or _fix_dispatch_unrecorded
    if (
        state.get("concerns_comment_id")
        and current_head_sha
        and previous_concerns_head_sha
        and current_head_sha != previous_concerns_head_sha
        and not _is_our_fix_push
    ):
        _retract_flag(
            state, gh_client, owner, repo, resolution="superseded by new push — re-review resumed"
        )
        state["fix_attempts"] = 0
        state.pop("last_concerns_summary", None)
        state.pop("last_concerns_head_sha", None)
        state.pop("last_fix_pass_pushed", None)
        state.pop("last_fix_push_sha", None)
        state.pop("fix_strategy_level", None)  # new code → start back at L0
        logger.info(
            "pr_review_watcher: PR #%d head changed after concerns (external push); "
            "resetting fix state",
            pr_number,
        )
        _save_state(state_path, state)

    # Once a PR is escalated for human attention, do not keep burning review
    # passes on the same unchanged head. Resume autonomous review only after a
    # new push changes the PR head SHA.
    if state.get("escalated_needs_human"):
        escalated_head_sha = str(state.get("escalated_head_sha") or "").strip()
        if not escalated_head_sha:
            if current_head_sha:
                # Self-heal older/corrupted state files by pinning the
                # escalation to the current head instead of re-posting the same
                # needs-human comment every sweep.
                state["escalated_head_sha"] = current_head_sha
                logger.info(
                    "pr_review_watcher: PR #%d escalated with no recorded SHA; "
                    "backfilled current head and will await change",
                    pr_number,
                )
                _save_state(state_path, state)
                return
            else:
                # No head SHA means we cannot tell whether anything changed.
                # Clear and retry once fresh PR data is available.
                state["escalated_needs_human"] = False
                state["no_verdict_passes"] = 0
                logger.info(
                    "pr_review_watcher: PR #%d escalated with no recorded SHA; clearing for retry",
                    pr_number,
                )
                _save_state(state_path, state)
        elif current_head_sha and current_head_sha != escalated_head_sha:
            _retract_flag(
                state, gh_client, owner, repo, resolution="new push — automated review resumed"
            )
            state["escalated_needs_human"] = False
            state.pop("escalated_head_sha", None)
            state["no_verdict_passes"] = 0
            logger.info(
                "pr_review_watcher: PR #%d head changed after escalation; resuming automated review",
                pr_number,
            )
            _save_state(state_path, state)
        else:
            # WO-3: if CI is green on the escalated head, the test suite has
            # validated the implementation. Retract the escalation once so the
            # reviewer can re-evaluate without a diff-truncation blind spot.
            # Bounded by _MAX_CI_GREEN_RETRACTIONS to prevent loops.
            #
            # EXCEPT when the escalation carries unresolved review concerns on
            # THIS exact (unchanged) head: CI was ALREADY green when those
            # concerns were raised, so green CI is not new information and must
            # not silently clear them. Retracting here shipped #313 — a
            # fix_pass_no_progress escalation got retracted on green CI, the
            # concerns were forgotten (last_concerns_* popped below), and a fresh
            # pass LGTM'd the same broken, CI-invisible integration. A
            # concern-based escalation waits for a real new push (changed head,
            # handled above) or a human — never for "CI is still green".
            _concerns_on_this_head = (
                bool(current_head_sha)
                and current_head_sha == str(state.get("last_concerns_head_sha") or "").strip()
            )
            _ci_green_retracted = state.get("ci_green_retraction_count", 0)
            _did_ci_green_retract = False
            if not _concerns_on_this_head and _ci_green_retracted < _MAX_CI_GREEN_RETRACTIONS:
                _rcfg = settings.repos.get(repo_key)
                if _rcfg and getattr(_rcfg, "auto_merge_on_ci_green", False):
                    _rhead = ((pr_data.get("head") or {}).get("ref") or "").lower()
                    if _rhead.startswith(("goal/", "test/", "improve/", "spec-author/")):
                        _rignored = list(getattr(_rcfg, "ci_ignored_checks", []) or [])
                        try:
                            _rfailed = gh_client.get_failed_checks(
                                owner,
                                repo,
                                pr_number,
                                pr_data=pr_data,
                                ignored_checks=_rignored,
                            )
                            # Settled-and-green only: don't retract while checks run.
                            _rpending = gh_client.get_incomplete_checks(
                                owner,
                                repo,
                                pr_number,
                                pr_data=pr_data,
                                ignored_checks=_rignored,
                            )
                            if not _rfailed and not _rpending:
                                _retract_flag(
                                    state,
                                    gh_client,
                                    owner,
                                    repo,
                                    resolution=(
                                        "CI green on unchanged head — test suite validates "
                                        "implementation; automated review resumed"
                                    ),
                                )
                                state["escalated_needs_human"] = False
                                state.pop("escalated_head_sha", None)
                                state["no_verdict_passes"] = 0
                                state["fix_attempts"] = 0
                                state.pop("last_concerns_summary", None)
                                state.pop("last_concerns_head_sha", None)
                                state.pop("last_fix_pass_pushed", None)
                                state["ci_green_retraction_count"] = _ci_green_retracted + 1
                                logger.info(
                                    "pr_review_watcher: PR #%d CI green on escalated head; "
                                    "retracting escalation for automated review retry "
                                    "(retraction %d/%d)",
                                    pr_number,
                                    _ci_green_retracted + 1,
                                    _MAX_CI_GREEN_RETRACTIONS,
                                )
                                _save_state(state_path, state)
                                _did_ci_green_retract = True
                        except Exception:
                            pass  # CI check failed — fall through to skip
            if not _did_ci_green_retract:
                logger.info(
                    "pr_review_watcher: PR #%d awaiting human attention or new push; "
                    "skipping automated self-review",
                    pr_number,
                )
                return

    # ── CI-green precondition ────────────────────────────────────────────────
    # For autonomy PRs on repos that opt in, green CI is a PRECONDITION for
    # merge — not a merge trigger. While CI is red we defer (ci_fix / CI will
    # resolve it) rather than burning an expensive self-review. Once CI is
    # green we fall through to the verdict-gated self-review below: LGTM is the
    # only thing that merges, so a PR is never shipped on green CI alone (green
    # CI does not prove the issue is complete — e.g. missing docs pass CI).
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
                if failed:
                    # Defer while CI is red so ci_fix / CI can resolve it — but
                    # BOUND the wait. If CI never goes green (e.g. a persistently
                    # failing check), don't defer forever (that would silently
                    # stall the loop) and don't merge red — escalate for a human.
                    state["ci_wait_cycles"] = state.get("ci_wait_cycles", 0) + 1
                    if state["ci_wait_cycles"] >= _MAX_CI_WAIT_CYCLES:
                        detail = (
                            f"CI has not gone green after {state['ci_wait_cycles']} "
                            f"checks ({len(failed)} failing: "
                            f"{', '.join(failed[:5])}). Not merged (red CI) and not "
                            f"closed (work preserved) — needs a human to fix CI."
                        )
                        record_escalation(
                            pr_number=pr_number,
                            repo_key=repo_key,
                            reason="ci_persistently_red",
                            detail=detail,
                        )
                        _escalate_needs_human(
                            state,
                            state_path,
                            gh_client,
                            owner,
                            repo,
                            settings,
                            reason="ci_persistently_red",
                            detail=detail,
                            current_head_sha=current_head_sha,
                        )
                        return
                    logger.info(
                        "pr_review_watcher: PR #%d CI not green (%d failed, wait %d/%d) — "
                        "deferring self-review until CI is green",
                        pr_number,
                        len(failed),
                        state["ci_wait_cycles"],
                        _MAX_CI_WAIT_CYCLES,
                    )
                    record_ci_gate_defer(
                        pr_number=pr_number,
                        repo_key=repo_key,
                        wait_cycle=state["ci_wait_cycles"],
                        max_cycles=_MAX_CI_WAIT_CYCLES,
                        failed_checks=failed,
                    )
                    _save_state(state_path, state)
                    return
                # No check has FAILED — but an empty failure list only means
                # "nothing has failed yet". While any check is still queued/running
                # its conclusion is None, invisible to get_failed_checks. Declaring
                # green here would let a self-review LGTM merge the PR before CI
                # finishes, turning the base branch red (this is how #269 merged red).
                # Require CI to have SETTLED (no pending checks) before green.
                pending = gh_client.get_incomplete_checks(
                    owner,
                    repo,
                    pr_number,
                    pr_data=pr_data,
                    ignored_checks=ignored,
                )
                # Guard C: the gating green must belong to the CURRENT head. An empty
                # completed-checks list means CI has produced no result on this head
                # yet (just pushed / auto-rebased) — failed==[] and pending==[] would
                # otherwise read as green on a head that has no CI at all, so a stale
                # pre-rebase green could carry a self-review LGTM straight to merge.
                completed = gh_client.get_completed_checks(
                    owner,
                    repo,
                    pr_number,
                    pr_data=pr_data,
                    ignored_checks=ignored,
                )
                # Guard D: every configured required check must be PRESENT and passing
                # on the current head. `failed` is already empty here (the failed-checks
                # branch above returned), so a required check is satisfied iff it appears
                # in `completed`. This closes the late-registering-check hole: a required
                # check living in a separate workflow that has not registered yet is
                # invisible to both failed and pending, so without this a PR could merge
                # before that check (e.g. the `audit` job) ever runs.
                required = list(getattr(repo_cfg, "required_checks", []) or [])
                missing_required = [
                    rc
                    for rc in required
                    if not any(rc.lower() in name.lower() for name in completed)
                ]
                if pending or not completed or missing_required:
                    state["ci_wait_cycles"] = state.get("ci_wait_cycles", 0) + 1
                    if pending:
                        _why = f"{len(pending)} still running: {', '.join(pending[:5])}"
                    elif not completed:
                        _why = "no checks have reported on the current head yet"
                    else:
                        _why = f"required checks not yet reported: {', '.join(missing_required)}"
                    if state["ci_wait_cycles"] >= _MAX_CI_WAIT_CYCLES:
                        detail = (
                            f"CI has not settled-green on the current head after "
                            f"{state['ci_wait_cycles']} checks ({_why}). Not merged (CI "
                            f"incomplete) and not closed (work preserved) — needs a human "
                            f"to investigate stuck CI."
                        )
                        record_escalation(
                            pr_number=pr_number,
                            repo_key=repo_key,
                            reason="ci_never_settled",
                            detail=detail,
                        )
                        _escalate_needs_human(
                            state,
                            state_path,
                            gh_client,
                            owner,
                            repo,
                            settings,
                            reason="ci_never_settled",
                            detail=detail,
                            current_head_sha=current_head_sha,
                        )
                        return
                    logger.info(
                        "pr_review_watcher: PR #%d CI not settled-green on current head "
                        "(%s, wait %d/%d) — deferring self-review",
                        pr_number,
                        _why,
                        state["ci_wait_cycles"],
                        _MAX_CI_WAIT_CYCLES,
                    )
                    _save_state(state_path, state)
                    return
                state["ci_wait_cycles"] = 0  # CI settled and green — reset the wait counter
                logger.info(
                    "pr_review_watcher: PR #%d CI green — proceeding to verdict-gated "
                    "self-review (LGTM required to merge)",
                    pr_number,
                )
            except Exception as exc:
                logger.debug("pr_review_watcher: CI check failed PR #%d — %s", pr_number, exc)

    diff = gh_client.get_pr_diff(owner, repo, pr_number)
    if not diff:
        logger.warning("pr_review_watcher: empty diff PR #%d, skipping", pr_number)
        return
    if diff.startswith("[DIFF_TOO_LARGE"):
        logger.warning(
            "pr_review_watcher: PR #%d diff exceeds GitHub API limit — reviewing file list only",
            pr_number,
        )

    _pr_files = _files_from_diff(diff)
    if len(diff) > _DIFF_LIMIT:
        # Fetch the complete file list so the reviewer can verify implementation
        # completeness even when the diff body is truncated. Without this, the
        # reviewer sees only documentation changes and wrongly concludes that
        # implementation files (which sort later alphabetically) are absent.
        _pr_files = gh_client.list_pr_files(owner, repo, pr_number)
        _file_lines = (
            "\n".join(f"  {f}" for f in sorted(_pr_files))
            if _pr_files
            else "  (file list unavailable)"
        )
        diff_excerpt = (
            diff[:_DIFF_LIMIT] + f"\n\n...[diff truncated at {_DIFF_LIMIT} chars]\n\n"
            "IMPORTANT — complete list of ALL files changed in this PR "
            "(files listed here ARE modified even if their diffs are not shown above; "
            "do NOT raise 'missing implementation' concerns for files that appear here):\n"
            + _file_lines
        )
    else:
        diff_excerpt = diff
    # A documentation-only diff gets a rubric that doesn't demand in-diff proof of
    # facts a doc legitimately references but can't contain (the #334 over-flagging).
    docs_only = _diff_is_docs_only(_pr_files)
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
    doc_rubric = _DOC_ONLY_REVIEW_RUBRIC if docs_only else ""

    goal_text = (
        "## TASK TYPE: Read-only code review\n"
        "## SINGLE REQUIRED ACTION: Write verdict.json — no other file changes allowed\n\n"
        f"Review the following pull-request diff for correctness, style, and spec compliance.\n\n"
        f"PR #{pr_number}: {title}\n\n"
        f"```diff\n{diff_excerpt}\n```"
        f"{spec_section}"
        f"{custodian_section}"
        f"{doc_rubric}\n\n"
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
        "CRITICAL: Do NOT modify any source files in the repository. "
        "Do NOT run tests, build, or push. "
        "Your ONLY permitted action is writing verdict.json to the current directory."
    )

    logger.info(
        "pr_review_watcher: self-review PR #%d repo=%s loop=%d",
        pr_number,
        repo_key,
        state["self_review_loops"],
    )

    state.setdefault("fix_attempts", 0)
    state.setdefault("no_verdict_passes", 0)
    state.setdefault("env_unclean_passes", 0)
    state.setdefault("backend_error_passes", 0)
    state.setdefault("no_verdict_escalation_count", 0)

    try:
        verdict = _run_direct_review(oc_root, goal_text, state_key)
    except OCSourceTreeUncleanError as exc:
        # The reviewer's own source tree is broken — this would crash for EVERY
        # PR, so it is not charged against this PR's review budget. Skip the
        # sweep loudly; a clean tree next cycle resumes review automatically.
        # Only after persistent uncleanliness do we escalate — and then with the
        # specific cause, not a misleading "no verdict / reviewer unavailable".
        state["env_unclean_passes"] += 1
        logger.error(
            "pr_review_watcher: PR #%d review SKIPPED (not budget-charged) — %s "
            "(env_unclean_pass=%d/%d)",
            pr_number,
            exc,
            state["env_unclean_passes"],
            reviewer.max_self_review_loops,
        )
        if state["env_unclean_passes"] >= reviewer.max_self_review_loops:
            _escalate_needs_human(
                state,
                state_path,
                gh_client,
                owner,
                repo,
                settings,
                reason="oc_source_tree_unclean",
                detail=str(exc),
                current_head_sha=current_head_sha,
            )
            state["env_unclean_passes"] = 0
        _save_state(state_path, state)
        return
    except ReviewerBackendError as exc:
        # The claude backend crashed, was killed, or timed out — INFRA failure,
        # not a PR quality problem.  Do NOT charge no_verdict_passes (that budget
        # measures genuine "reviewer ran but emitted no verdict", not crashes).
        state["backend_error_passes"] += 1
        logger.warning(
            "pr_review_watcher: PR #%d review SKIPPED (not budget-charged) — backend error: %s "
            "(backend_error_pass=%d/%d)",
            pr_number,
            exc,
            state["backend_error_passes"],
            reviewer.max_self_review_loops,
        )
        if state["backend_error_passes"] >= reviewer.max_self_review_loops:
            _escalate_needs_human(
                state,
                state_path,
                gh_client,
                owner,
                repo,
                settings,
                reason="reviewer_backend_unavailable",
                detail=str(exc),
                current_head_sha=current_head_sha,
            )
            state["backend_error_passes"] = 0
        _save_state(state_path, state)
        return

    state["self_review_loops"] += 1
    state["env_unclean_passes"] = 0  # a pipeline ran — tree is clean
    state["backend_error_passes"] = 0  # backend is reachable

    if verdict is None:
        # Reviewer ran cleanly (exit 0) but did not write verdict.json — a genuine
        # no-verdict (prompt or model failure).  Count against the budget.
        state["no_verdict_passes"] += 1
        if state["no_verdict_passes"] >= reviewer.max_self_review_loops:
            state["no_verdict_escalation_count"] += 1
            escalation_count = state["no_verdict_escalation_count"]
            logger.warning(
                "pr_review_watcher: PR #%d produced no verdict after %d passes — "
                "escalating (leaving open; reviewer unavailable, work preserved) "
                "[no_verdict_escalation_count=%d]",
                pr_number,
                state["no_verdict_passes"],
                escalation_count,
            )
            # Stuck-green detection: a PR that repeatedly reaches the no-verdict
            # escalation threshold without ever merging is likely stuck.  Emit a
            # distinct alarm so the operator (and watchdog) can see it clearly.
            _STUCK_GREEN_ESCALATION_THRESHOLD = 3
            if escalation_count >= _STUCK_GREEN_ESCALATION_THRESHOLD:
                logger.error(
                    "pr_review_watcher: STUCK-GREEN PR #%d repo=%s — green on CI but "
                    "unmerged after %d no-verdict escalation cycles "
                    "(reason=stuck_green_repeated_failures); human review required",
                    pr_number,
                    repo_key,
                    escalation_count,
                )
            detail = (
                "Self-review produced no parseable verdict after repeated passes "
                "(reviewer ran but emitted no verdict.json — possible prompt or model "
                "issue). The PR is left open for human attention; automated "
                "review will retry."
            )
            if escalation_count >= _STUCK_GREEN_ESCALATION_THRESHOLD:
                detail += (
                    f" WARNING: this is the {escalation_count}th no-verdict escalation "
                    f"for this PR (stuck-green — green on CI but review never converges). "
                    f"Reason code: stuck_green_repeated_failures."
                )
            record_escalation(
                pr_number=pr_number,
                repo_key=repo_key,
                reason="no_verdict_unreviewable",
                detail=detail,
            )
            state["escalated_head_sha"] = current_head_sha or state.get("escalated_head_sha")
            _escalate_needs_human(
                state,
                state_path,
                gh_client,
                owner,
                repo,
                settings,
                reason="no_verdict_unreviewable",
                detail=detail,
                current_head_sha=current_head_sha,
            )
            state["no_verdict_passes"] = 0  # keep retrying in case it was transient
            _save_state(state_path, state)
        else:
            logger.warning(
                "pr_review_watcher: no verdict PR #%d (no_verdict_pass=%d/%d) — will retry",
                pr_number,
                state["no_verdict_passes"],
                reviewer.max_self_review_loops,
            )
            _save_state(state_path, state)
        return

    state["no_verdict_passes"] = 0  # a verdict was produced
    result = (verdict.get("result") or "CONCERNS").upper()
    summary = verdict.get("summary", "(no summary)")
    normalized_summary = _normalize_concerns_summary(summary)

    logger.info("pr_review_watcher: PR #%d self-review verdict=%s", pr_number, result)

    # Surface the verdict as a required status check on the reviewed head, so an
    # unresolved CONCERNS verdict blocks merge for humans (manual gh pr merge)
    # too, not just the fleet's own verdict-gated path. _merge_and_done re-blesses
    # success right before merging, which also covers the non-LGTM merge paths.
    _publish_reviewer_verdict(
        gh_client,
        owner,
        repo,
        current_head_sha,
        result="success" if result == "LGTM" else "failure",
        description="reviewer LGTM" if result == "LGTM" else "reviewer concerns — auto-fixing",
    )

    if result == "LGTM":
        # The ONLY merge path on the self-review track — verdict-gated.
        record_decision_outcome(
            pr_number=pr_number,
            repo_key=repo_key,
            outcome="merge",
            reason="self_review_lgtm",
            lanes=1,
        )
        _merge_and_done(
            state, state_path, pr_data, gh_client, owner, repo, settings, reason="self_review_lgtm"
        )
        return

    # Trigger on the second no-change pass at the same head — no text comparison
    # needed.  LLM-generated summaries are not bit-identical across loops even
    # when the root issue is unchanged, so requiring text equality was
    # unreliable and caused the reviewer to spin through all max_fix_attempts
    # before escalating.
    repeated_no_progress = (
        state.get("fix_attempts", 0) > 0
        and state.get("last_fix_pass_pushed") is False
        and current_head_sha
        and current_head_sha == str(state.get("last_concerns_head_sha") or "").strip()
    )
    if repeated_no_progress:
        # WO-3 extension: when the CI-green retraction budget is exhausted and fix
        # passes still push nothing, the concerns are very likely diff-truncation
        # false positives — CI passing the full test suite is ground truth.
        # Merge rather than re-entering the escalation→retraction loop.
        if state.get("ci_green_retraction_count", 0) >= _MAX_CI_GREEN_RETRACTIONS:
            _rcfg_np = settings.repos.get(repo_key)
            _head_ref_np = ((pr_data.get("head") or {}).get("ref") or "").lower()
            if (
                _rcfg_np
                and getattr(_rcfg_np, "auto_merge_on_ci_green", False)
                and _head_ref_np.startswith(("goal/", "test/", "improve/", "spec-author/"))
            ):
                try:
                    _ign_np = list(getattr(_rcfg_np, "ci_ignored_checks", []) or [])
                    _failed_np = gh_client.get_failed_checks(
                        owner, repo, pr_number, pr_data=pr_data, ignored_checks=_ign_np
                    )
                    # Only merge on CI that has SETTLED — never while checks are still
                    # running (no failure yet != green) and never on a head with no
                    # reported checks at all (Guard C: gating green must be on THIS head).
                    _pending_np = gh_client.get_incomplete_checks(
                        owner, repo, pr_number, pr_data=pr_data, ignored_checks=_ign_np
                    )
                    _completed_np = gh_client.get_completed_checks(
                        owner, repo, pr_number, pr_data=pr_data, ignored_checks=_ign_np
                    )
                    # Guard D: every required check must be present and passing too.
                    _required_np = list(getattr(_rcfg_np, "required_checks", []) or [])
                    _missing_np = [
                        rc
                        for rc in _required_np
                        if not any(rc.lower() in name.lower() for name in _completed_np)
                    ]
                    if not _failed_np and not _pending_np and _completed_np and not _missing_np:
                        logger.info(
                            "pr_review_watcher: PR #%d repeated no-progress after "
                            "CI-green retraction budget exhausted; CI still green — "
                            "trusting CI over truncated-diff false positives and merging",
                            pr_number,
                        )
                        record_decision_outcome(
                            pr_number=pr_number,
                            repo_key=repo_key,
                            outcome="merge",
                            reason="ci_validated_after_retraction",
                            lanes=1,
                        )
                        _merge_and_done(
                            state,
                            state_path,
                            pr_data,
                            gh_client,
                            owner,
                            repo,
                            settings,
                            reason="ci_validated_after_retraction",
                        )
                        return
                except Exception:
                    pass  # CI check failed — fall through to normal escalation

        # Self-Heal Ladder: a no-progress repeat does NOT immediately concede to
        # a human. Climb a rung — re-dispatch the fix pass with MORE resolving
        # power (L1 enriched context, L2 decompose to one concern per pass) — and
        # escalate only when the ladder tops out. Binding invariant holds: this
        # changes how hard the system TRIES, never what counts as resolved; LGTM
        # is still the only merge path. (max_fix_strategy_level=0 → old immediate
        # escalation.)
        current_level = int(state.get("fix_strategy_level", 0))
        next_level = current_level + 1
        if next_level <= reviewer.max_fix_strategy_level:
            state["fix_strategy_level"] = next_level
            logger.info(
                "pr_review_watcher: PR #%d no-progress — climbing Self-Heal Ladder to "
                "L%d/%d (re-dispatching with more resolving power, not escalating)",
                pr_number,
                next_level,
                reviewer.max_fix_strategy_level,
            )
            _save_state(state_path, state)
            # Fall through to the dispatch below, which reads fix_strategy_level.
        else:
            detail = (
                "The automated fix passes exhausted the Self-Heal Ladder (reached "
                f"L{current_level}/{reviewer.max_fix_strategy_level}) without changing "
                "the branch; a fresh self-review on the same PR head still finds "
                "concerns. Further autonomous retries would repeat without progress.\n\n"
                f"Latest concerns:\n\n{summary}"
            )
            state["escalated_head_sha"] = current_head_sha or state.get("escalated_head_sha")
            _escalate_needs_human(
                state,
                state_path,
                gh_client,
                owner,
                repo,
                settings,
                reason="fix_pass_no_progress",
                detail=detail,
                current_head_sha=current_head_sha,
            )
            return

    # CONCERNS — never merge. Dispatch a fix pass that resolves the concerns on
    # the PR's own branch (updating the PR), then re-review next cycle. After
    # max_fix_attempts without reaching LGTM, close the PR and re-queue the
    # issue for a fresh attempt — a half-finished PR is never shipped.
    if state["fix_attempts"] >= reviewer.max_fix_attempts:
        logger.warning(
            "pr_review_watcher: PR #%d still CONCERNS after %d fix attempts — "
            "closing and re-queuing",
            pr_number,
            state["fix_attempts"],
        )
        detail = (
            f"Could not resolve review concerns after {state['fix_attempts']} "
            f"fix attempts. Last concerns:\n\n{summary}"
        )
        record_decision_outcome(
            pr_number=pr_number,
            repo_key=repo_key,
            outcome="blocked",
            reason="fix_attempts_exhausted",
            lanes=1,
        )
        _close_and_requeue(
            state,
            state_path,
            pr_data,
            gh_client,
            owner,
            repo,
            settings,
            reason="fix_attempts_exhausted",
            detail=detail,
            concerns=summary,
        )
        return

    # Post the concerns once, on the first CONCERNS pass.
    if state["fix_attempts"] == 0:
        concern_body = (
            f"{reviewer.bot_comment_marker}\n"
            f"**Self-review concerns** — auto-fixing (up to {reviewer.max_fix_attempts} "
            f"attempts; re-queued if still unresolved):\n\n{summary}"
        )
        try:
            resp = gh_client.post_comment(owner, repo, pr_number, concern_body)
            state["concerns_comment_id"] = (resp or {}).get("id")
        except Exception as exc:
            logger.warning(
                "pr_review_watcher: failed to post concern comment PR #%d — %s", pr_number, exc
            )

    head_ref = (pr_data.get("head") or {}).get("ref") or ""
    if not head_ref:
        logger.error(
            "pr_review_watcher: PR #%d has no head ref — cannot dispatch fix pass; "
            "closing and re-queuing",
            pr_number,
        )
        record_decision_outcome(
            pr_number=pr_number,
            repo_key=repo_key,
            outcome="blocked",
            reason="no_head_ref",
            lanes=1,
        )
        _close_and_requeue(
            state,
            state_path,
            pr_data,
            gh_client,
            owner,
            repo,
            settings,
            reason="no_head_ref",
            detail="The PR head branch could not be determined, so concerns cannot be auto-fixed.",
        )
        return

    # Pre-save the attempt counter and head SHA before the (potentially long) fix
    # pass — if the watcher is restarted while the backend runs, the counter
    # survives. last_fix_pass_pushed is cleared until the pass completes, so
    # repeated_no_progress (which checks `is False`) won't fire on a missing key.
    state["fix_attempts"] += 1
    state["last_concerns_head_sha"] = current_head_sha
    state.pop("last_fix_pass_pushed", None)
    _save_state(state_path, state)

    # Self-Heal Ladder rung: L0 is the standard pass; higher rungs (set when a
    # no-progress repeat climbed the ladder above) enrich the dispatch with more
    # resolving power instead of conceding to a human.
    fix_level = int(state.get("fix_strategy_level", 0))
    extra_context = _ladder_enrichment(fix_level, pr_diff=diff)
    logger.info(
        "pr_review_watcher: PR #%d CONCERNS — dispatching fix pass %d/%d (ladder L%d) on branch %s",
        pr_number,
        state["fix_attempts"],
        reviewer.max_fix_attempts,
        fix_level,
        head_ref,
    )
    record_decision_outcome(
        pr_number=pr_number,
        repo_key=repo_key,
        outcome="retry",
        reason="mixed_verdicts",
        lanes=1,
    )
    pushed = _run_fix_pass(
        oc_root,
        config_path,
        repo_key,
        head_ref,
        summary,
        settings,
        state_key=state_key,
        extra_context=extra_context,
    )
    state["last_concerns_summary"] = normalized_summary
    state["last_fix_pass_pushed"] = pushed
    if pushed:
        # Record the head our own fix pass just produced, so the next poll does
        # NOT mistake it for an external push and reset the escalation budget.
        # Without this, fix_attempts never accumulates and the PR loops forever.
        try:
            state["last_fix_push_sha"] = _pr_head_sha(gh_client.get_pr(owner, repo, pr_number))
        except Exception as exc:  # noqa: BLE001 — best-effort; reset-guard degrades safe
            logger.warning(
                "pr_review_watcher: could not record fix-push head for PR #%d — %s",
                pr_number,
                exc,
            )
            state.pop("last_fix_push_sha", None)
    if not pushed:
        logger.warning(
            "pr_review_watcher: fix pass for PR #%d pushed no changes (attempt %d/%d)",
            pr_number,
            state["fix_attempts"],
            reviewer.max_fix_attempts,
        )
    # The fix pass executor can run for minutes.  An external process (e.g. the
    # watchdog) may have updated escalated_needs_human on disk while we waited.
    # Re-read the escalation flag so we don't overwrite it on save.
    try:
        disk = _load_state(state_path)
        if disk.get("escalated_needs_human") and not state.get("escalated_needs_human"):
            state["escalated_needs_human"] = True
            state["escalated_head_sha"] = disk.get("escalated_head_sha") or state.get(
                "escalated_head_sha"
            )
            logger.info(
                "pr_review_watcher: PR #%d external escalation detected after fix pass; "
                "preserving escalated_needs_human=True",
                pr_number,
            )
    except Exception:
        pass
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


def _export_decision_metrics(status_dir: Path) -> None:
    """Surface the merge-decision metrics the instrumenter collects.

    pr_review_watcher records every merge decision via record_decision_outcome
    (→ the global MergeDecisionInstrumenter), but the collected metrics had no
    reader: export_metrics_json was never called. Write the instrumenter's
    summary to the status dir each cycle so the metrics are actually observable.
    Best-effort — metrics export must never break the review loop."""
    try:
        status_dir.mkdir(parents=True, exist_ok=True)
        metrics_path = status_dir / "merge_decision_metrics.json"
        metrics_path.write_text(get_instrumenter().export_metrics_json(), encoding="utf-8")
    except Exception:
        pass


# ── Poll cycle ────────────────────────────────────────────────────────────────


def _review_priority(state: dict) -> tuple[int, int, int]:
    """Sort key for the per-repo review worklist — lower sorts (and runs) first.

    Proactive ordering: a PR that may reach a terminal decision on this pass —
    a fresh self-review that could merge — must not be starved behind a PR
    already sunk into a multi-pass fix battle (each fix pass is a slow LLM run).
    Tiers:
      0  self_review, no fix attempts yet  — the quick-merge candidates
      1  ci_fix                            — bounded automated CI repair
      2  self_review already iterating     — slow fix loops, run last
    Within a tier: fewer consumed fix passes first, then PR number for a
    stable, deterministic order."""
    phase = state.get("phase", "ci_fix")
    fix_attempts = int(state.get("fix_attempts", 0))
    if phase == "self_review" and fix_attempts == 0:
        tier = 0
    elif phase == "ci_fix":
        tier = 1
    else:
        tier = 2
    return (tier, fix_attempts, int(state.get("pr_number", 0)))


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

        # GC leftover review-state for PRs that terminated outside this watcher's
        # merge/close path (manual merge, another host, or while it was down).
        _prune_orphan_state_files(
            oc_root, repo_key, {int(p["number"]) for p in open_prs if p.get("number") is not None}
        )

        # Build the worklist (discover + load state) before processing, so the
        # sweep can be ordered. A single slow PR (a multi-pass fix battle) must
        # not push a merge-ready PR to the back of the sweep — or off it entirely
        # if the watcher restarts mid-cycle.
        worklist: list[tuple[dict, dict, Path]] = []
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
            # Record the live head ref each poll (it changes across pushes) so the
            # auto-rebase path knows which branch to merge the base into.
            head_ref = (pr_data.get("head") or {}).get("ref")
            if head_ref:
                state["head_ref"] = head_ref
            worklist.append((pr_data, state, sp))

        # Proactive ordering: quick-merge candidates before slow fix loops.
        worklist.sort(key=lambda item: _review_priority(item[1]))

        for pr_data, state, sp in worklist:
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
        _export_decision_metrics(status_dir)
        return 0

    logger.info("pr_review_watcher: starting — poll_interval=%ds", args.poll_interval)
    while True:
        try:
            settings = _load_settings(args.config)
            _poll_once(oc_root, args.config, settings)
        except Exception as exc:
            logger.error("pr_review_watcher: unhandled error — %s", exc, exc_info=True)
        _write_heartbeat(status_dir)
        _export_decision_metrics(status_dir)
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    sys.exit(main())
