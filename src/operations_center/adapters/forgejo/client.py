# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Forgejo board adapter — `BoardClient` over Forgejo issues.

Implements the board seam against a self-hosted Forgejo instance so the fleet can
leave Plane. See `docs/specs/forgejo-board-adapter.md`; the two hazards that
shaped this file are worth restating here, because both are invisible at the call
site.

**States are exclusive; Forgejo labels are a set.** OC's six states
(`Ready for AI`, `Backlog`, `Blocked`, `Awaiting Input`, `Done`, `Cancelled`)
were a single Plane field — one value, enforced by the schema. Here they are
labels prefixed `state: `, and nothing in Forgejo prevents an issue carrying two.
`transition_issue` is therefore remove-then-add: **two calls, not atomic**. If the
process dies between them the issue is left with zero or two state labels.

This adapter does not pretend to have solved that. It:

* writes the new state before removing the old (so a crash leaves *two* states,
  never *zero* — a task that appears twice is recoverable, a task that has fallen
  off the board is not);
* refuses to guess in `to_board_task`, raising on a multi-state issue rather than
  picking one, so the corruption surfaces at the read that would otherwise act on
  it.

**Pagination truncates silently.** `list_issues()` means the whole board. Forgejo
pages at 20 by default, and a caller that reads page one gets a plausible,
successful, wrong answer. The fleet reasons about *absence* — it promotes when a
queue looks empty and declares stalls when nothing progresses — so a short read
produces confident wrong decisions rather than an error. Every list here pages to
exhaustion, and the tests use a multi-page fixture because a single-page one would
pass while that bug shipped.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, cast

import httpx

from operations_center.application.task_parser import TaskParser

if TYPE_CHECKING:  # pragma: no cover
    from operations_center.domain.models import BoardTask

_logger = logging.getLogger(__name__)

#: States live in the label namespace under this prefix so they cannot collide
#: with the fleet's existing structured labels (`source: `, `task_phase: `,
#: `blocked-reason: `, `retry-count: `), which share that namespace.
STATE_LABEL_PREFIX = "state: "

#: Priority has no Forgejo field either. Lower stakes than state — it only feeds
#: rescoring, never dispatch — but the same set-vs-scalar problem applies.
PRIORITY_LABEL_PREFIX = "priority: "

#: The vocabulary the fleet actually dispatches on. An unknown state is a bug,
#: not a new state, so it fails loudly instead of being created on demand.
KNOWN_STATES = frozenset({
    "Backlog",
    "Ready for AI",
    "Blocked",
    "Awaiting Input",
    "Done",
    "Cancelled",
})

#: Forgejo caps page size; 50 is well inside every deployment's limit.
_PAGE_SIZE = 50

#: Refuse to loop forever if the server keeps returning full pages.
_MAX_PAGES = 200


class MultipleStatesError(RuntimeError):
    """An issue carries more than one `state:` label.

    Raised rather than resolved. The fleet's rules assume exactly one state, so
    guessing here would let a corrupted issue satisfy two rules in one cycle —
    silently, and in a way that only shows up as contradictory board actions.
    """


class UnknownStateError(RuntimeError):
    """A state name outside `KNOWN_STATES` was requested."""


class ForgejoClient:
    """A `BoardClient` backed by Forgejo issues in a single board repo."""

    def __init__(
        self,
        base_url: str,
        api_token: str,
        owner: str,
        repo: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.owner = owner
        self.repo = repo
        self.task_parser = TaskParser()
        self._labels_cache: list[dict[str, Any]] | None = None
        self._client = httpx.Client(
            base_url=self.base_url,
            # Forgejo/Gitea auth. Plane used X-API-Key; sending that here
            # authenticates as nobody and every call 401s.
            headers={
                "Authorization": f"token {api_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30.0,
            transport=transport,
        )

    # ── plumbing ─────────────────────────────────────────────────────────────

    @property
    def _repo_path(self) -> str:
        return f"/api/v1/repos/{self.owner}/{self.repo}"

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Request with retry on 429, transient 5xx, and connection errors.

        Same posture as the Plane adapter: a duplicated side effect (a repeated
        comment) is preferable to a missed board write, so non-idempotent calls
        are retried too.
        """
        attempts = 4
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.request(method, url, **kwargs)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt == attempts:
                    raise
                time.sleep(attempt)
                continue

            if response.status_code == 429 and attempt < attempts:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else attempt
                time.sleep(delay)
                continue
            if response.status_code in (502, 503, 504) and attempt < attempts:
                time.sleep(attempt)
                continue
            return response

        raise last_exc if last_exc else RuntimeError("request failed without a response")

    def _paginate(self, url: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Read every page.

        The whole reason this method exists: a single-page read looks successful.
        Stops when a page comes back shorter than the page size, and refuses to
        spin past `_MAX_PAGES` if a server keeps returning full pages.
        """
        out: list[dict[str, Any]] = []
        page = 1
        while page <= _MAX_PAGES:
            merged = dict(params or {})
            merged.update({"page": page, "limit": _PAGE_SIZE})
            response = self._request("GET", url, params=merged)
            response.raise_for_status()
            batch = cast("list[dict[str, Any]]", response.json())
            out.extend(batch)
            if len(batch) < _PAGE_SIZE:
                return out
            page += 1

        _logger.warning(
            "forgejo: %s hit the %d-page ceiling (%d items) — board may be truncated",
            url, _MAX_PAGES, len(out),
        )
        return out

    # ── state <-> label ──────────────────────────────────────────────────────

    @staticmethod
    def _state_label(state: str) -> str:
        return f"{STATE_LABEL_PREFIX}{state}"

    @staticmethod
    def _states_of(issue: dict[str, Any]) -> list[str]:
        return [
            str(label.get("name", ""))[len(STATE_LABEL_PREFIX):]
            for label in issue.get("labels", [])
            if isinstance(label, dict)
            and str(label.get("name", "")).startswith(STATE_LABEL_PREFIX)
        ]

    def state_of(self, issue: dict[str, Any]) -> str:
        """The issue's single state, or raise.

        Raising is the point. A multi-state issue is corruption, and the caller
        is about to make a dispatch decision from this value.
        """
        states = self._states_of(issue)
        if len(states) == 1:
            return states[0]
        if not states:
            return "Unknown"
        raise MultipleStatesError(
            f"issue #{issue.get('number')} carries {len(states)} state labels "
            f"({sorted(states)}) — exactly one is required. A transition was "
            f"interrupted, or two writers raced. Repair the labels before the "
            f"fleet acts on this issue."
        )

    # ── reads ────────────────────────────────────────────────────────────────

    def fetch_issue(self, task_id: str) -> dict[str, Any]:
        response = self._request("GET", f"{self._repo_path}/issues/{task_id}")
        response.raise_for_status()
        return cast("dict[str, Any]", response.json())

    def fetch_project(self) -> dict[str, Any]:
        response = self._request("GET", self._repo_path)
        response.raise_for_status()
        return cast("dict[str, Any]", response.json())

    def list_issues(self) -> list[dict[str, Any]]:
        """Every issue on the board, across all pages.

        `state=all` because the fleet's terminal states (Done, Cancelled) are
        labels, not Forgejo's open/closed — filtering to open would hide them.
        """
        return self._paginate(f"{self._repo_path}/issues", {"state": "all", "type": "issues"})

    def list_states(self) -> list[dict[str, Any]]:
        """The state vocabulary, shaped like Plane's states for callers.

        Forgejo has no states, so this is derived from `KNOWN_STATES` rather than
        fetched. Callers used it to resolve a name to an id; here name *is* the
        id.
        """
        return [{"id": name, "name": name} for name in sorted(KNOWN_STATES)]

    def list_labels(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        if self._labels_cache is None or force_refresh:
            self._labels_cache = self._paginate(f"{self._repo_path}/labels")
        return self._labels_cache

    def list_comments(self, task_id: str) -> list[dict[str, Any]]:
        return self._paginate(f"{self._repo_path}/issues/{task_id}/comments")

    # ── translation ──────────────────────────────────────────────────────────

    def to_board_task(self, issue: dict[str, Any]) -> BoardTask:
        from operations_center.domain.models import BoardTask

        description = str(issue.get("body") or "")
        label_names = [
            str(label.get("name", ""))
            for label in issue.get("labels", [])
            if isinstance(label, dict)
        ]
        # State labels are adapter plumbing, not fleet vocabulary — the parser
        # and the rules should never see them.
        fleet_labels = [n for n in label_names if not n.startswith(STATE_LABEL_PREFIX)]

        parsed_body = self.task_parser.parse(description, labels=fleet_labels)
        metadata = parsed_body.execution_metadata

        return BoardTask(
            task_id=str(issue["number"]),
            project_id=f"{self.owner}/{self.repo}",
            title=str(issue.get("title", "Untitled")),
            description=description,
            status=self.state_of(issue),
            labels=fleet_labels,
            repo_key=str(metadata["repo"]),
            base_branch=str(metadata["base_branch"]),
            execution_mode=cast("Any", metadata.get("mode", "goal")),
            allowed_paths=[
                str(path) for path in cast("list[object]", metadata.get("allowed_paths") or [])
            ],
            validation_profile=(
                str(metadata.get("validation_profile"))
                if metadata.get("validation_profile")
                else None
            ),
            open_pr=bool(metadata.get("open_pr", False)),
            goal_text=parsed_body.goal_text,
            constraints_text=parsed_body.constraints_text,
        )

    # ── writes ───────────────────────────────────────────────────────────────

    def _ensure_label(self, name: str) -> None:
        """Create a label if the board lacks it. Forgejo requires it to exist."""
        existing = {str(item.get("name", "")) for item in self.list_labels()}
        if name in existing:
            return
        response = self._request(
            "POST", f"{self._repo_path}/labels", json={"name": name, "color": "#ededed"}
        )
        if response.status_code not in (200, 201, 422):  # 422 = already exists (race)
            response.raise_for_status()
        self._labels_cache = None

    def _set_labels(self, task_id: str, names: list[str]) -> None:
        for name in names:
            self._ensure_label(name)
        response = self._request(
            "PUT", f"{self._repo_path}/issues/{task_id}/labels", json={"labels": names}
        )
        response.raise_for_status()

    def transition_issue(self, task_id: str, state: str) -> None:
        """Move an issue to `state`.

        Not atomic — Forgejo has no single field to write. The new state is added
        *before* the old is removed, deliberately: an interrupted transition then
        leaves two states, which `state_of` reports loudly, rather than none,
        which would drop the task off every queue silently.
        """
        if state not in KNOWN_STATES:
            raise UnknownStateError(
                f"{state!r} is not a known board state ({sorted(KNOWN_STATES)}). "
                "Adding a state is a fleet-wide change, not a per-call one."
            )

        issue = self.fetch_issue(task_id)
        current = [
            str(label.get("name", ""))
            for label in issue.get("labels", [])
            if isinstance(label, dict)
        ]
        target = self._state_label(state)
        keep = [n for n in current if not n.startswith(STATE_LABEL_PREFIX)]
        self._set_labels(task_id, [*keep, target])

    def create_issue(
        self,
        *,
        name: str,
        description: str,
        state: str | None = None,
        label_names: list[str] | None = None,
    ) -> dict[str, Any]:
        labels = list(label_names or [])
        if state:
            if state not in KNOWN_STATES:
                raise UnknownStateError(f"{state!r} is not a known board state")
            labels.append(self._state_label(state))
        for label in labels:
            self._ensure_label(label)

        response = self._request(
            "POST",
            f"{self._repo_path}/issues",
            json={"title": name, "body": description, "labels": labels},
        )
        response.raise_for_status()
        return cast("dict[str, Any]", response.json())

    def update_issue_description(self, task_id: str, description: str) -> None:
        response = self._request(
            "PATCH", f"{self._repo_path}/issues/{task_id}", json={"body": description}
        )
        response.raise_for_status()

    def update_issue_labels(self, task_id: str, label_names: list[str]) -> None:
        """Replace the fleet's labels, preserving the state label.

        Callers pass fleet vocabulary and know nothing about `state: ` — dropping
        it here would silently unstate the task.
        """
        issue = self.fetch_issue(task_id)
        states = [
            str(label.get("name", ""))
            for label in issue.get("labels", [])
            if isinstance(label, dict)
            and str(label.get("name", "")).startswith(STATE_LABEL_PREFIX)
        ]
        self._set_labels(task_id, [*label_names, *states])

    def comment_issue(self, task_id: str, comment_markdown: str) -> None:
        response = self._request(
            "POST",
            f"{self._repo_path}/issues/{task_id}/comments",
            json={"body": comment_markdown},
        )
        response.raise_for_status()

    def set_priority(self, task_id: str, priority: str) -> None:
        """Priority as a label — Forgejo has no such field."""
        issue = self.fetch_issue(task_id)
        keep = [
            str(label.get("name", ""))
            for label in issue.get("labels", [])
            if isinstance(label, dict)
            and not str(label.get("name", "")).startswith(PRIORITY_LABEL_PREFIX)
        ]
        self._set_labels(task_id, [*keep, f"{PRIORITY_LABEL_PREFIX}{priority}"])
