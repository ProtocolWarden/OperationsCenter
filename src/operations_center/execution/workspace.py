# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Workspace lifecycle for the execution boundary.

The coordinator hands the adapter a workspace and a task branch, but until now
nothing populated that workspace from a real git clone, and nothing pushed the
results when the adapter finished. So every "successful" run produced changes
in a tmp directory that got cleaned up — no branch on origin, no PR, no
visible artifact for the reviewer watcher to pick up.

WorkspaceManager closes that gap:

- prepare(request): clones request.clone_url into request.workspace_path,
  checks out the base branch, and creates the task branch.
- finalize(request, result): if execution succeeded, commits any uncommitted
  changes, pushes the task branch, and (when the repo is configured for
  await_review) opens a pull request via GitHub. Returns an ExecutionResult
  updated with branch_pushed / branch_name / pull_request_url.

The manager is optional on ExecutionCoordinator — coordinator tests construct
the coordinator without one, preserving the existing pure-coordinator surface.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from operations_center.adapters.git.client import GitClient
from operations_center.adapters.workspace.patch_applier import PatchApplier
from operations_center.contracts.enums import (
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult

logger = logging.getLogger(__name__)


class BaselineValidationError(RuntimeError):
    """Raised from ``WorkspaceManager.prepare`` when baseline validation fails
    and the request opted in via ``require_clean_validation is True``.

    A ``RuntimeError`` subclass so the coordinator's existing broad
    prepare-failure handler catches it and produces a clean FAILED
    ExecutionResult — no new handler wiring required.
    """


# Branch prefixes whose runs should NOT be pushed or PR'd. Improve mode is
# analysis-only (output is the follow-up goal task, not code). Reviewer self-
# review writes verdict.json and exits — never code changes. Anything written
# to the workspace incidentally is dropped.
_NO_PUSH_BRANCH_PREFIXES = ("improve/", "review/")

# Paths inside the workspace that must never be committed even if .gitignore
# isn't updated downstream. Belt-and-suspenders against artifact pollution.
_LOCAL_EXCLUDE_PATTERNS = (
    ".operations_center/",
    ".codex",
    ".coverage",
    ".baseline-validation.json",
)

# Soft limits on the size of a single autonomy commit. A diff that exceeds
# either threshold is almost always the executor going wide unintentionally —
# the 25K-LOC PR #56 was the spark for adding this guard. Operators can bump
# the env vars when intentionally large refactors are expected.
_DEFAULT_MAX_FILES = 50
_DEFAULT_MAX_LINES = 2000


class WorkspaceManager:
    def __init__(
        self,
        *,
        git_client: GitClient | None = None,
        github_token: str | None = None,
        await_review_repos: set[str] | None = None,
        bot_identity: tuple[str, str] = ("Operations Center", "operations-center@local"),
        max_files: int | None = None,
        max_lines: int | None = None,
        repo_settings_lookup: Callable[[str], object] | None = None,
        settings: object | None = None,
        oc_root: Path | None = None,
    ) -> None:
        self._git = git_client or GitClient()
        self._token = github_token
        self._await_review = set(await_review_repos or [])
        self._bot_name, self._bot_email = bot_identity
        self._max_files = max_files if max_files is not None else _DEFAULT_MAX_FILES
        self._max_lines = max_lines if max_lines is not None else _DEFAULT_MAX_LINES
        # Optional: a callable repo_key -> RepoSettings (or None). Used by the
        # bootstrap step (C-K3) and the open_pr_default gate (C-K9). Decoupled
        # from the full Settings object so callers can pass a lambda.
        self._repo_lookup = repo_settings_lookup or (lambda _k: None)
        # Optional full Settings object. Read defensively (getattr with a
        # default) so the many tests / callers that construct WorkspaceManager
        # without settings keep working. Used by the pre-PR custodian gate.
        self._settings = settings
        # OperationsCenter checkout root — used to resolve the custodian-multi
        # binary from OC's own venv as a last fallback. Defaults to the repo
        # root inferred from this module's location (.../src/operations_center/
        # execution/workspace.py -> repo root).
        self._oc_root = oc_root or Path(__file__).resolve().parents[3]
        # SBX pre-push applier: validates patches before commit/push
        self._patch_applier = PatchApplier()

    # ── credential handling ──────────────────────────────────────────────────

    def _extract_tokenless_url(self, clone_url: str) -> str:
        """Extract tokenless URL from clone_url by removing embedded credentials.

        Examples:
            https://ghp_abc123@github.com/org/repo.git → https://github.com/org/repo.git
            https://token@github.com/org/repo.git → https://github.com/org/repo.git
            git@github.com:org/repo.git → git@github.com:org/repo.git (unchanged)
        """
        if "://" in clone_url and "@" in clone_url:
            protocol, rest = clone_url.split("://", 1)
            if "@" in rest:
                # Find the end of the authority section (where path starts)
                # For https:// URLs, path starts with /
                # For SSH URLs (git@), this is already unchanged by the check above
                slash_pos = rest.find("/")
                # Authority section is everything before the first /
                if slash_pos == -1:
                    # No path, entire rest is authority
                    authority = rest
                else:
                    authority = rest[:slash_pos]

                # Find the last @ in the authority (credentials separator)
                last_at = authority.rfind("@")
                if last_at != -1:
                    # Found credentials, remove them
                    host_part = authority[last_at + 1 :]
                    if slash_pos == -1:
                        return f"{protocol}://{host_part}"
                    else:
                        path_part = rest[slash_pos:]
                        return f"{protocol}://{host_part}{path_part}"
        return clone_url

    def _clean_reflog(self, workspace_path: Path) -> None:
        """Clean reflog to remove token references from clone operation.

        Token may appear in reflog URLs from the clone operation. This clears
        the reflog and runs gc to remove any cached token references.
        """
        try:
            self._git._run(
                ["git", "reflog", "expire", "--expire=now", "--all"],
                cwd=workspace_path,
            )
            self._git._run(
                ["git", "gc", "--prune=now"],
                cwd=workspace_path,
            )
        except RuntimeError as e:
            logger.debug("Reflog cleanup failed (non-fatal): %s", e)

    def _strip_token_from_config(self, workspace_path: Path, clone_url: str) -> None:
        """Remove embedded token from .git/config after clone.

        Implementation of Option B (tokenless URL rewrite):
        - Clone used token URL (required for authentication)
        - Immediately rewrite remote.origin.url to tokenless URL
        - Clean reflog to remove token references from clone operation
        """
        tokenless_url = self._extract_tokenless_url(clone_url)
        try:
            self._git._run(
                ["git", "config", "remote.origin.url", tokenless_url],
                cwd=workspace_path,
            )
        except RuntimeError as e:
            logger.warning("Failed to rewrite git config: %s", e)
            raise RuntimeError(f"Could not strip token from git config: {e}") from e

        self._clean_reflog(workspace_path)

    def verify_no_token_in_workspace(self, workspace_path: Path) -> tuple[bool, list[str]]:
        """Verify that no git token remains in workspace after clone.

        Checks:
            1. .git/config remote.origin.url contains no embedded credentials
            2. .git/logs/HEAD (reflog) contains no token references

        Returns:
            (success, error_messages) - success is True if no tokens found
        """
        ws = Path(workspace_path)
        errors = []

        config_file = ws / ".git" / "config"
        if config_file.exists():
            config_content = config_file.read_text(encoding="utf-8")
            if "://" in config_content and "@" in config_content:
                for line in config_content.split("\n"):
                    if "url =" in line and "://" in line:
                        after_protocol = line.split("://", 1)[1] if "://" in line else ""
                        if "@" in after_protocol and not after_protocol.startswith("git@"):
                            errors.append(
                                f"Found embedded credentials in .git/config: {line.strip()}"
                            )

        reflog_file = ws / ".git" / "logs" / "HEAD"
        if reflog_file.exists():
            reflog_content = reflog_file.read_text(encoding="utf-8")
            if "://" in reflog_content and "@" in reflog_content:
                for i, line in enumerate(reflog_content.split("\n")):
                    if "://" in line and "@" in line and "github.com" in line:
                        errors.append(
                            f"Found embedded credentials in reflog line {i}: {line[:100]}"
                        )

        return len(errors) == 0, errors

    # ── pre-execution ────────────────────────────────────────────────────────

    def prepare(self, request: ExecutionRequest) -> None:
        """Clone the repo into workspace_path and create the task branch.

        Tolerates a pre-existing empty workspace_path (board_worker creates one).
        Fails fast if the directory exists and is non-empty.
        """
        ws = Path(request.workspace_path)
        ws.mkdir(parents=True, exist_ok=True)
        if any(ws.iterdir()):
            raise RuntimeError(
                f"workspace_path {ws} is not empty; refusing to clone into it",
            )

        # `git clone <url> .` populates the current directory directly, so the
        # repo root IS the workspace — no extra `repo/` subdir.
        # --depth 1 --no-single-branch fetches all branch tips without full history
        # (measured: shallow clone ~38s vs full clone >120s for large repos).
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--no-single-branch", request.clone_url, "."],
            cwd=ws,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"git clone failed: {proc.stderr.strip() or proc.stdout.strip()}",
            )

        # Strip token from .git/config immediately after successful clone, then
        # verify nothing leaked through. The verification is a production gate,
        # not just a test helper: a residual token in .git/config or the reflog
        # is a credential-exposure defect, so fail closed rather than proceed
        # with a tainted workspace.
        self._strip_token_from_config(ws, request.clone_url)
        clean, token_errors = self.verify_no_token_in_workspace(ws)
        if not clean:
            raise RuntimeError(
                "git token survived workspace sanitisation: " + "; ".join(token_errors),
            )

        self._git.set_identity(ws, self._bot_name, self._bot_email)
        # Belt-and-suspenders: even if the target repo's .gitignore doesn't
        # exclude backend artifacts, this local exclude keeps them out of the
        # commit. Earlier runs without this committed executor stdout.log
        # back into the repo, producing 25K-LOC garbage PRs.
        for pattern in _LOCAL_EXCLUDE_PATTERNS:
            self._git.add_local_exclude(ws, pattern)
        # Check out the base branch on the pristine clone BEFORE any step that
        # writes artifacts into the tree (bootstrap, baseline validation). If a
        # tooling artifact such as `.baseline-validation.json` is tracked on the
        # clone's default branch (it slipped into the self-repo's main), writing
        # it before the checkout dirties a tracked file and `git checkout
        # <base_branch>` aborts with "local changes would be overwritten". Doing
        # the checkout first keeps the working tree clean for the switch and lets
        # baseline validation run against the branch we actually build on.
        #
        # If base_branch is a sandbox like "autonomy-staging" that the operator
        # forgot to create, fail with a clear error rather than a cryptic
        # `git checkout` failure. Plane will surface the reason via the new
        # failure-reason plumbing.
        try:
            self._git.verify_remote_branch_exists(ws, request.base_branch)
        except Exception as exc:
            # Sandbox base branches (sandbox_base_branch in config) can go
            # missing on origin between runs — they have repeatedly vanished
            # and dead-ended every queued OC self-modify task while the
            # watchdog recreated them by hand. Self-heal: create the branch
            # from the remote default branch tip and re-verify, so a missing
            # sandbox base no longer requires operator intervention. In
            # non-sandbox mode base_branch is the repo's real default branch,
            # which always exists, so this recovery path never fires.
            try:
                default_ref = f"origin/{self._git.remote_default_branch(ws)}"
                self._git.create_remote_branch_from(ws, request.base_branch, default_ref)
                self._git.verify_remote_branch_exists(ws, request.base_branch)
                logger.warning(
                    "WorkspaceManager.prepare: base_branch %r was missing on "
                    "origin; self-healed by creating it from %s",
                    request.base_branch,
                    default_ref,
                )
            except Exception:
                raise RuntimeError(
                    f"base_branch {request.base_branch!r} does not exist on origin "
                    "and could not be auto-created — if this is a sandbox branch "
                    "(sandbox_base_branch in config), create it on origin first "
                    f"(e.g. `git push origin main:{request.base_branch}`). "
                    f"Underlying: {exc}"
                ) from exc
        self._git.checkout_base(ws, request.base_branch)
        # C-K3 bootstrap chain — only fires when the repo opts in via
        # explicit install_dev_command or bootstrap_commands. Default
        # config (no bootstrap fields set) is a no-op so existing repos
        # see no behavior change.
        self._maybe_bootstrap(ws, request)
        # Baseline validation — if the repo (or the per-task request) declares
        # validation_commands and hasn't opted out via skip_baseline_validation,
        # run them here. The summary is also persisted to a marker file.
        #
        # S1c gate: a FAILED/ERROR baseline blocks the run ONLY when the request
        # explicitly opted in via require_clean_validation is True. None (every
        # live task today) preserves the prior behavior — baseline failure does
        # not abort. We raise so the coordinator's existing prepare-failure
        # handler turns it into a clean FAILED ExecutionResult.
        baseline_summary = self._run_baseline_validation(ws, request)
        if (
            request.require_clean_validation is True
            and baseline_summary is not None
            and baseline_summary.status in (ValidationStatus.FAILED, ValidationStatus.ERROR)
        ):
            raise BaselineValidationError(
                "baseline validation did not pass and require_clean_validation "
                f"is set: status={baseline_summary.status.value}"
                + (
                    f"; {baseline_summary.failure_excerpt}"
                    if baseline_summary.failure_excerpt
                    else ""
                )
            )
        # Restore .baseline-validation.json to HEAD so create_task_branch can
        # checkout origin/task_branch without "local changes would be overwritten".
        # The file has slipped into several goal-branch commits; baseline validation
        # overwrites it, making it dirty and blocking the retry-path checkout.
        self._git.restore_to_head(ws, ".baseline-validation.json")
        self._git.create_task_branch(ws, request.task_branch)
        logger.info(
            "WorkspaceManager.prepare: cloned into %s on branch %s (token stripped from config)",
            ws,
            request.task_branch,
        )

    # ── post-execution ───────────────────────────────────────────────────────

    def finalize(
        self,
        request: ExecutionRequest,
        result: ExecutionResult,
    ) -> ExecutionResult:
        """Commit pending changes, push, and (optionally) open a PR.

        Updates and returns an ExecutionResult with branch_pushed / branch_name
        / pull_request_url populated. Failures are non-fatal — the original
        result is returned with branch_pushed=False if anything goes wrong.
        """
        if not result.success:
            return result

        # Don't push runs whose branch prefix indicates analysis-only work.
        # Improve mode: executor analyses and the orchestrator creates a follow-up
        # goal task — that goal task is what gets shipped, not the improve
        # workspace. Reviewer self-review: writes verdict.json, never code.
        if request.task_branch.startswith(_NO_PUSH_BRANCH_PREFIXES):
            logger.info(
                "WorkspaceManager.finalize: skipping push for %s (branch prefix is analysis-only)",
                request.task_branch,
            )
            return result

        ws = Path(request.workspace_path)
        if not (ws / ".git").exists():
            logger.warning("WorkspaceManager.finalize: %s is not a git repo", ws)
            return result

        # Pre-flight diff cap: refuse to commit a diff that's almost certainly
        # the executor going wide. Operators set OPS_CENTER_MAX_FILES /
        # OPS_CENTER_MAX_LINES higher when intentionally shipping a large
        # refactor; default is conservative.
        oversized = self._diff_oversized(ws, request)
        if oversized is not None:
            n_files, n_lines, file_list = oversized
            logger.warning(
                "WorkspaceManager.finalize: refusing to commit oversized diff "
                "for %s — %d files, %d lines (caps: %d files, %d lines)",
                request.task_branch,
                n_files,
                n_lines,
                self._max_files,
                self._max_lines,
            )
            # Persist the file list so the caller (board_worker) can build
            # a focused split-task per chunk before the workspace is torn down.
            try:
                import json as _json

                (ws / "scope-too-wide.json").write_text(
                    _json.dumps({"files": file_list, "n_lines": n_lines}),
                    encoding="utf-8",
                )
            except OSError as exc:
                logger.warning(
                    "WorkspaceManager.finalize: could not persist scope-too-wide.json — %s", exc
                )
            # Detailed reason — surfaces in the Plane comment via _handle_failure
            # plumbing. Lists the top files so an operator (or a future
            # auto-split recovery service) can see exactly where the executor
            # went wide and break the work into focused chunks.
            top_files = "\n".join(f"  - {f}" for f in file_list[:15])
            extra = f" (+{len(file_list) - 15} more)" if len(file_list) > 15 else ""
            return result.model_copy(
                update={
                    "branch_pushed": False,
                    "failure_category": FailureReasonCategory.SCOPE_TOO_WIDE,
                    "failure_reason": (
                        f"diff exceeded soft cap: {n_files} files, {n_lines} lines "
                        f"(caps {self._max_files} / {self._max_lines}). "
                        "Suggested next: split into smaller goal tasks scoped to one or "
                        "two files each, or raise OPS_CENTER_MAX_FILES / "
                        f"OPS_CENTER_MAX_LINES if the wide scope is intentional.\n"
                        f"Top files in this run:\n{top_files}{extra}"
                    ),
                }
            )

        # SBX pre-push applier gate: validate patch before committing
        # This enforces the path allowlist, blocking dangerous changes
        is_valid, error_msg = self._validate_patch_before_commit(ws, request)
        if not is_valid:
            logger.warning(
                "WorkspaceManager.finalize: patch validation failed for %s — %s",
                request.task_branch,
                error_msg,
            )
            return result.model_copy(
                update={
                    "branch_pushed": False,
                    "failure_category": FailureReasonCategory.POLICY_BLOCKED,
                    "failure_reason": error_msg or "Patch validation failed",
                }
            )

        # Commit anything the executor left in the working tree
        if self._git.changed_files(ws):
            commit_message = self._commit_message(request)
            self._git.commit_all(ws, commit_message)

        if not self._has_new_commits(ws, request.base_branch):
            logger.info(
                "WorkspaceManager.finalize: no new commits on %s vs origin/%s — nothing to push",
                request.task_branch,
                request.base_branch,
            )
            return result

        # Squash all stage commits into one before pushing (ADR 0009 P4).
        # Rewritten history requires force-push; single-commit branches use
        # regular push so the first push of a new branch doesn't force.
        squash_message = self._commit_message(request)
        squashed = self._git.squash_commits(ws, request.base_branch, squash_message)

        # Pre-PR custodian gate. Run AFTER the squash but BEFORE the push so a
        # custodian-failing run produces a clean failure with NO orphan branch
        # pushed and NO PR opened (its required `audit` CI check would just go
        # red on arrival). Fail-safe: only a real findings exit blocks; a missing
        # binary / crash / timeout degrades to the prior behavior (push + PR).
        findings = self._run_pre_pr_custodian_gate(request)
        if findings is not None:
            logger.warning(
                "WorkspaceManager.finalize: pre-PR custodian gate BLOCKED %s — "
                "not pushing, not opening PR. %s",
                request.task_branch,
                findings.splitlines()[0] if findings else "custodian findings",
            )
            return result.model_copy(
                update={
                    "status": ExecutionStatus.FAILED,
                    "success": False,
                    "branch_pushed": False,
                    "failure_category": FailureReasonCategory.POLICY_BLOCKED,
                    "failure_reason": (
                        "pre-PR custodian gate: the task branch has custodian "
                        "findings; refusing to push or open a PR that would fail "
                        "the required `audit` CI check. Resolve the findings and "
                        f"re-run.\n{findings}"
                    ),
                }
            )

        try:
            if squashed:
                self._git.push_branch_force(ws, request.task_branch)
            else:
                self._git.push_branch(ws, request.task_branch)
        except Exception as exc:
            logger.warning("WorkspaceManager.finalize: push failed — %s", exc)
            return result

        # Cross-repo impact warning: if the changed files touch any other
        # repo's declared impact_report_paths, log the affected neighbours
        # so the operator (or a future commenter) knows. Non-fatal — just
        # surfaces information the WorkspaceManager already has.
        self._warn_cross_repo_impact(ws, request)

        pr_url = self._maybe_create_pr(request)

        return result.model_copy(
            update={
                "branch_pushed": True,
                "branch_name": request.task_branch,
                "pull_request_url": pr_url,
            }
        )

    # ── pre-PR custodian gate ──────────────────────────────

    def _resolve_custodian_bin(self, request: ExecutionRequest) -> str | None:
        """Resolve the ``custodian-multi`` binary, or None if not found.

        Mirrors ``_phase0_ci_fix``\'s ruff resolution (pr_review_watcher): prefer
        the repo\'s own venv bin, then a system ``custodian-multi`` on PATH, then
        OC\'s own ``.venv/bin``. Unlike the ruff fallback we do NOT fall through to
        a bare ``custodian-multi`` name — a missing binary must be a clean
        "not found" so the gate fail-safes (degrade → proceed) cleanly.
        """
        # 1) repo venv bin (the repo being built may ship custodian itself)
        repo_cfg = self._repo_lookup(request.repo_key)
        venv_dir = (getattr(repo_cfg, "venv_dir", None) or ".venv") if repo_cfg else ".venv"
        local_path = getattr(repo_cfg, "local_path", None) if repo_cfg else None
        if local_path:
            cand = Path(local_path) / venv_dir / "bin" / "custodian-multi"
            if cand.exists():
                return str(cand)
        # 2) system custodian-multi on PATH
        which = shutil.which("custodian-multi")
        if which:
            return which
        # 3) OC\'s own venv bin (the fleet runs OC/.venv)
        oc_bin = self._oc_root / ".venv" / "bin" / "custodian-multi"
        if oc_bin.exists():
            return str(oc_bin)
        return None

    def _run_pre_pr_custodian_gate(self, request: ExecutionRequest) -> str | None:
        """Run custodian on the workspace; return findings text iff it BLOCKS.

        Returns:
            - ``None`` to PROCEED (gate disabled, custodian clean, OR any
              fail-safe condition: binary missing, crash, timeout, or a non
              findings error exit). When unsure -> proceed.
            - a non-empty findings summary string to BLOCK (push + PR skipped).
              Only returned when custodian actually RAN and reported findings
              (the ``--fail-on-findings`` contract: exit code 1 with a findings
              table on stdout).
        """
        # Gate requires a real Settings object. Production wires one through
        # (entrypoints/execute/main.py, default True); the many callers / tests
        # that construct WorkspaceManager WITHOUT settings skip the gate, so unit
        # tests never shell out to a real custodian-multi against a fake workspace
        # — that no-settings-default-ON behavior was the #405 test-pollution
        # regression. With a Settings object the field defaults True (older config
        # missing the field still enables it).
        if self._settings is None or not getattr(
            self._settings, "pre_pr_custodian_gate", True
        ):
            logger.debug("WorkspaceManager: pre-PR custodian gate inactive — skipping")
            return None

        bin_path = self._resolve_custodian_bin(request)
        if bin_path is None:
            # FAIL-SAFE: no custodian binary anywhere -> degrade to prior behavior.
            logger.warning(
                "WorkspaceManager: pre-PR custodian gate could not find a "
                "custodian-multi binary — proceeding without the gate (fail-safe)"
            )
            return None

        ws = str(request.workspace_path)
        env = dict(os.environ)
        # Some detectors (boundary checks) need the disclosure artifact; pass it
        # through when present. Absence must NOT hard-fail — the fail-safe below
        # treats a non-findings error exit as "proceed", and findings exits are
        # the only thing that blocks.
        artifact = os.environ.get("REPOGRAPH_BOUNDARY_ARTIFACT_FILE")
        if artifact:
            env["REPOGRAPH_BOUNDARY_ARTIFACT_FILE"] = artifact

        try:
            proc = subprocess.run(
                [bin_path, "--repos", ws, "--fail-on-findings", "--no-color"],
                capture_output=True,
                text=True,
                timeout=180,
                env=env,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            # FAIL-SAFE: timeout or spawn failure -> proceed.
            logger.warning(
                "WorkspaceManager: pre-PR custodian gate did not complete (%s) — "
                "proceeding without the gate (fail-safe)",
                exc,
            )
            return None

        # custodian-multi --fail-on-findings contract:
        #   0  -> clean (no findings)        -> PROCEED
        #   1  -> findings present           -> BLOCK
        #   other (>=2 / negative signal)    -> tool/env error -> PROCEED (fail-safe)
        if proc.returncode == 0:
            return None
        if proc.returncode != 1:
            logger.warning(
                "WorkspaceManager: pre-PR custodian gate exited %s (not a clean "
                "findings exit) — proceeding without the gate (fail-safe). stderr: %s",
                proc.returncode,
                (proc.stderr or "").strip()[:300],
            )
            return None

        # Exit 1 = real findings. Surface the findings table (stdout) trimmed.
        summary = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        if not summary:
            # Defensive: exit 1 but no output at all is ambiguous; treat the bare
            # signal as findings (block) since --fail-on-findings only returns 1
            # when it found something.
            summary = "custodian reported findings (no detail captured)"
        # Keep the message bounded — the full table can be large.
        return summary[:4000]

    def _validate_patch_before_commit(
        self, ws: Path, request: ExecutionRequest
    ) -> tuple[bool, str | None]:
        """Validate the staged diff against path allowlist before committing.

        This is the SBX pre-push applier gate: enforce path policy that would
        otherwise be advisory. Reject patches touching blocked paths, absolute
        paths, .. traversal, or symlinks.

        Returns (is_valid, error_message).
        """
        # Get the staged diff
        try:
            # First stage all changes
            subprocess.run(
                ["git", "add", "-A"],
                cwd=ws,
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Get the staged diff
            proc = subprocess.run(
                ["git", "diff", "--cached"],
                cwd=ws,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        except subprocess.CalledProcessError:
            return True, None  # No staged changes; nothing to validate
        except subprocess.TimeoutExpired:
            return False, "Patch validation timed out"

        diff_text = proc.stdout
        if not diff_text.strip():
            return True, None  # Empty diff is valid

        # Validate the patch (without applying it)
        result = self._patch_applier.validate(diff_text, ws)
        if not result.success:
            blocked_msg = ""
            if result.blocked_paths:
                blocked_msg = f"\nBlocked paths: {', '.join(result.blocked_paths)}"
            return False, f"Patch validation failed: {result.reason}{blocked_msg}"
        # Per-task write-scope allowlist — was inert (the static blocklist ran, but
        # request.allowed_paths never reached the diff). Wired here, fail-CLOSED, but
        # ONLY when the proposal set a non-empty allowlist: find_violations() fail-opens
        # on an empty list, so normal/self-modify tasks (which set none) are unaffected.
        if request.allowed_paths:
            from operations_center.application.scope_policy import ChangedFilePolicyChecker

            names = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=ws,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.splitlines()
            changed = [n.strip() for n in names if n.strip()]
            violations = ChangedFilePolicyChecker().find_violations(
                changed, list(request.allowed_paths)
            )
            if violations:
                return False, (
                    f"Patch touches paths outside the task allowlist "
                    f"{list(request.allowed_paths)}: {', '.join(violations)}"
                )
        return True, None

    def _warn_cross_repo_impact(self, ws: Path, request: ExecutionRequest) -> None:
        """Log a warning when changed files cross another repo's interface.

        Pulls the changed files from `git diff --name-only HEAD~1..HEAD`
        and runs them through `_check_cross_repo_impact` against the
        configured repos. No Plane / GitHub side effects — this is
        instrumentation, not control flow.
        """
        try:
            from operations_center.cross_repo_impact import _check_cross_repo_impact
        except Exception:
            return  # helper missing — silent (defensive in case of partial install)
        # Build {key: cfg} from whatever the lookup gives us.
        # The lookup callback returns a single repo cfg; we don't have a
        # full repo dict here. Skip if we can't enumerate.
        all_repos: dict[str, object] = {}
        # Attempt to walk a settings-like object if the lookup exposes it
        # via attribute access on a wrapped Settings; otherwise we just
        # log a stub. Real wiring through a settings reference is a
        # deliberate next step (avoids breaking the existing unit tests
        # that construct WorkspaceManager without settings).
        if hasattr(self._repo_lookup, "__self__"):
            settings_obj = getattr(self._repo_lookup, "__self__", None)
            if settings_obj is not None:
                all_repos = getattr(settings_obj, "repos", {}) or {}
        if not all_repos:
            return
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only", "HEAD~1..HEAD"],
                cwd=ws,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        except subprocess.CalledProcessError:
            return
        files = [f for f in proc.stdout.splitlines() if f]
        if not files:
            return
        impacts = _check_cross_repo_impact(
            files,
            repos=all_repos,
            source_repo_key=request.repo_key,
        )
        for impact in impacts:
            logger.warning(
                "WorkspaceManager.finalize: cross-repo impact — %s touched paths "
                "declared by %s: %s",
                request.repo_key,
                impact.repo_key,
                ", ".join(impact.matched_paths),
            )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _diff_oversized(
        self, ws: Path, request: ExecutionRequest
    ) -> tuple[int, int, list[str]] | None:
        """Return (files, lines, file_list) when the staged diff exceeds caps.

        Returns None when within bounds. file_list is sorted alphabetically
        so the comment we write to Plane is deterministic.
        """
        try:
            subprocess.run(
                ["git", "add", "-A"], cwd=ws, check=True, capture_output=True, timeout=30
            )
            proc = subprocess.run(
                ["git", "diff", "--cached", "--shortstat"],
                cwd=ws,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            files_proc = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=ws,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        except subprocess.CalledProcessError:
            return None
        finally:
            subprocess.run(["git", "reset"], cwd=ws, capture_output=True, timeout=30)

        file_list = sorted(line.strip() for line in files_proc.stdout.splitlines() if line.strip())
        n_files = len(file_list)
        n_lines = 0
        import re

        for m in re.finditer(r"(\d+)\s+(?:insertion|deletion)", proc.stdout):
            n_lines += int(m.group(1))
        # Per-task max_changed_files (when the proposal set it) can only TIGHTEN the
        # operator's global file cap, never loosen it. None (normal tasks) = global cap.
        effective_max_files = self._max_files
        if request.max_changed_files is not None:
            effective_max_files = min(self._max_files, request.max_changed_files)
        if n_files > effective_max_files or n_lines > self._max_lines:
            return n_files, n_lines, file_list
        return None

    def _has_new_commits(self, ws: Path, base_branch: str) -> bool:
        proc = subprocess.run(
            ["git", "rev-list", "--count", f"origin/{base_branch}..HEAD"],
            cwd=ws,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return False
        try:
            return int(proc.stdout.strip()) > 0
        except ValueError:
            return False

    def _commit_message(self, request: ExecutionRequest) -> str:
        r"""Derive a short commit / PR title from the request.

        Strips markdown noise (``**bold**``, ``\`code\```) and ``[Tag] `` prefixes
        that goal text often carries from spec-campaign builders. Falls back
        to a stable run-id slug if the goal text is empty or so prompt-shaped
        that no useful title can be extracted.
        """
        import re

        text = (request.goal_text or "").strip()
        first_line = text.splitlines()[0].strip() if text else ""
        first_line = re.sub(r"^#+\s*", "", first_line)  # strip markdown heading markers
        first_line = re.sub(r"^\[\w+\]\s*", "", first_line)  # strip [Impl]/[Test]/[Improve]
        first_line = re.sub(r"\*\*([^*]+)\*\*", r"\1", first_line)  # **bold** → bold
        first_line = re.sub(r"`([^`]+)`", r"\1", first_line)  # `code` → code
        first_line = first_line.strip(" .—-")
        if not first_line:
            return f"Operations Center run {request.run_id[:8]}"
        return first_line[:72]

    def _maybe_create_pr(self, request: ExecutionRequest) -> str | None:
        if not self._token:
            return None
        if request.repo_key not in self._await_review:
            return None
        # C-K9: per-repo opt-out of automatic PR creation. When False, the
        # branch is pushed but no PR opens — operator opens it manually.
        repo_cfg = self._repo_lookup(request.repo_key)
        if repo_cfg is not None and not getattr(repo_cfg, "open_pr_default", True):
            logger.info(
                "WorkspaceManager: skipping PR for %s — open_pr_default=False",
                request.repo_key,
            )
            return None
        try:
            from operations_center.adapters.github_pr import GitHubPRClient

            owner, repo = GitHubPRClient.owner_repo_from_clone_url(request.clone_url)
            gh = GitHubPRClient(self._token)
            title = self._commit_message(request)
            body = (
                f"Auto-generated by Operations Center execution.\n\n## Goal\n{request.goal_text}\n"
            )
            pr = gh.create_pr(
                owner,
                repo,
                head=request.task_branch,
                base=request.base_branch,
                title=title,
                body=body,
            )
            return pr.get("html_url")
        except Exception as exc:
            logger.warning("WorkspaceManager: PR creation failed — %s", exc)
            return None

    def _run_baseline_validation(self, ws: Path, request: ExecutionRequest):
        """Run repo's baseline validation; persist the summary to a marker file.

        We write the result to ``ws/.baseline-validation.json`` so a later stage
        can read it without WorkspaceManager having to thread the contract
        through. Best-effort — failures here log a warning but don't abort.

        Returns the ``ValidationSummary`` (or None when there is no repo_cfg /
        the run crashed) so the caller can apply the per-task
        ``require_clean_validation`` gate (S1c). The abort decision itself is
        made in ``prepare()``, not here.

        Command source (S1c): when ``request.validation_commands`` is non-empty,
        those per-task commands run *instead of* the repo's configured
        ``validation_commands`` — wrapped in a small shim that still carries the
        repo's ``validation_timeout_seconds`` and ``skip_baseline_validation``
        so ``run_baseline_validation`` reads a complete cfg. Empty (every live
        task today) leaves the repo_cfg path unchanged.
        """
        repo_cfg = self._repo_lookup(request.repo_key)
        if repo_cfg is None:
            return None

        # Per-task command override: only when the request explicitly set
        # validation_commands. The shim mirrors exactly the three attributes
        # baseline_validation.run_baseline_validation reads off repo_cfg.
        cfg_for_validation = repo_cfg
        if request.validation_commands:
            from types import SimpleNamespace

            cfg_for_validation = SimpleNamespace(
                validation_commands=list(request.validation_commands),
                validation_timeout_seconds=getattr(repo_cfg, "validation_timeout_seconds", 300),
                skip_baseline_validation=getattr(repo_cfg, "skip_baseline_validation", False),
            )

        try:
            from operations_center.execution.baseline_validation import (
                run_baseline_validation,
            )

            summary = run_baseline_validation(ws, repo_cfg=cfg_for_validation)
        except Exception as exc:
            logger.warning(
                "WorkspaceManager.prepare: baseline validation crashed — %s",
                exc,
            )
            return None
        try:
            (ws / ".baseline-validation.json").write_text(
                summary.model_dump_json(),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.debug("baseline-validation marker write failed — %s", exc)
        return summary

    def _maybe_bootstrap(self, ws: Path, request: ExecutionRequest) -> None:
        """Run repo-bootstrap (venv setup + dev install) when configured.

        Opt-in: only fires when the repo declares `install_dev_command` or
        `bootstrap_commands`. Default-shaped configs (no bootstrap fields)
        are a no-op so adding this code doesn't change existing behavior
        for any repo that hasn't asked for it.

        Failures are non-fatal: we log a warning and proceed. Kodo still
        runs against the cloned tree; bootstrap is an enhancer, not a gate.
        """
        repo_cfg = self._repo_lookup(request.repo_key)
        if repo_cfg is None:
            return
        if not getattr(repo_cfg, "bootstrap_enabled", True):
            return
        custom = getattr(repo_cfg, "bootstrap_commands", None) or []
        install_cmd = getattr(repo_cfg, "install_dev_command", None)
        if not custom and not install_cmd:
            return  # nothing configured — explicit no-op

        # Custom commands take precedence — caller knows their environment.
        if custom:
            for cmd in custom:
                if not isinstance(cmd, str) or not cmd.strip():
                    continue
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=ws,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                if proc.returncode != 0:
                    logger.warning(
                        "WorkspaceManager: bootstrap_commands step failed (%s) in %s — %s",
                        cmd,
                        request.repo_key,
                        (proc.stderr or proc.stdout).strip()[:200],
                    )
                    return
            logger.info(
                "WorkspaceManager: bootstrap_commands ran (%d step(s)) for %s",
                len(custom),
                request.repo_key,
            )
            return

        # Standard path: create venv, run install_dev_command.
        venv_dir = getattr(repo_cfg, "venv_dir", None) or ".venv"
        # When an offline wheelhouse is in play, its wheels are tag-locked to the
        # interpreter that built it (OC_WHEELHOUSE_PYTHON, set by provision_env). The
        # venv that installs from it MUST be that same interpreter or the cp-tagged
        # wheels won't match (e.g. host python3=3.14 vs a cp312 wheelhouse → the
        # `--no-index --find-links` install fails "from versions: none" and the task
        # is blocked). Prefer it; fall back to the repo's configured python_binary
        # (and finally `python3`) when no wheelhouse is active. Single source of truth
        # with the wheelhouse builder, so the two can never drift.
        python_bin = (
            os.environ.get("OC_WHEELHOUSE_PYTHON")
            or getattr(repo_cfg, "python_binary", None)
            or "python3"
        )
        try:
            subprocess.run(
                [python_bin, "-m", "venv", venv_dir],
                cwd=ws,
                capture_output=True,
                text=True,
                timeout=300,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning(
                "WorkspaceManager: venv creation failed for %s — %s",
                request.repo_key,
                exc,
            )
            return
        # install_cmd is non-None at this point (early-returned above when both
        # custom and install_cmd were empty); narrow for ty.
        if install_cmd is None:  # pragma: no cover — guarded by early return above
            return
        proc = subprocess.run(
            install_cmd,
            shell=True,
            cwd=ws,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if proc.returncode != 0:
            logger.warning(
                "WorkspaceManager: install_dev_command failed for %s — %s",
                request.repo_key,
                (proc.stderr or proc.stdout).strip()[:200],
            )
            return
        logger.info(
            "WorkspaceManager: bootstrapped venv at %s/%s for %s", ws, venv_dir, request.repo_key
        )
