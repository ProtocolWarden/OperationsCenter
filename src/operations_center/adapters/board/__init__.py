# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""The board seam: what the fleet needs from a task board, and one place to build it.

OC's board was Plane, and replacing it was a 37-file change — not because the
surface is large (it is eleven operations) but because every one of those files
imported `PlaneClient` by name, constructed it from the same four settings
fields, and type-hinted against the concrete class. Ten had independently
hand-rolled the identical `_make_plane_client()` helper, which was the clearest
possible evidence that the missing piece was a shared one.

The migration finished on 2026-08-18 and the Plane adapter is gone. What remains
is the property that made it finishable: callers name this module, not a
backend.

This module is that piece:

* :class:`BoardClient` — the operations the fleet actually performs, named in
  board terms rather than Plane's. Callers depend on this.
* :func:`make_board_client` — the single construction site. Swapping backends
  changes this function, not every caller.

`PlaneClient` already satisfies the protocol structurally, so adopting this is a
rename at each call site, not a behavioural change. That is deliberate: the
migration should be boring and reviewable, and any behaviour change should be its
own commit.

Nothing outside ``adapters/`` should import a concrete client directly.
``tests/unit/adapters/test_board_seam.py`` enforces that against a shrinking
allowlist, so the boundary tightens instead of eroding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from operations_center.domain.models import BoardTask

__all__ = ["BoardClient", "board_project_id", "make_board_client"]


@runtime_checkable
class BoardClient(Protocol):
    """The board operations the fleet performs.

    Deliberately the *existing* surface, verbatim, rather than an idealised one.
    A protocol that reshapes the API at the same time as introducing the seam
    cannot be adopted mechanically, and a migration that is not mechanical is one
    where regressions hide. Reshaping (for instance, `list_issues` growing a
    filter so backends need not return the whole board) belongs in later commits,
    once callers depend on this name instead of `PlaneClient`.
    """

    # ── reads ────────────────────────────────────────────────────────────────
    def fetch_issue(self, task_id: str) -> dict[str, Any]: ...

    def fetch_project(self) -> dict[str, Any]: ...

    def list_issues(self) -> list[dict[str, Any]]: ...

    def list_states(self) -> list[dict[str, Any]]: ...

    def list_labels(self, *, force_refresh: bool = False) -> list[dict[str, Any]]: ...

    def list_comments(self, task_id: str) -> list[dict[str, Any]]: ...

    # ── translation into the domain ──────────────────────────────────────────
    def to_board_task(self, issue: dict[str, Any]) -> BoardTask: ...

    # ── writes ───────────────────────────────────────────────────────────────
    def transition_issue(self, task_id: str, state: str) -> None: ...

    def create_issue(
        self,
        *,
        name: str,
        description: str,
        state: str | None = None,
        label_names: list[str] | None = None,
    ) -> dict[str, Any]: ...

    def set_priority(self, task_id: str, priority: str) -> None: ...

    def update_issue_description(self, task_id: str, description: str) -> None: ...

    def update_issue_labels(self, task_id: str, label_names: list[str]) -> None: ...

    def comment_issue(self, task_id: str, comment_markdown: str) -> None: ...

    # ── lifecycle ────────────────────────────────────────────────────────────
    def close(self) -> None: ...


def make_board_client(settings: Any) -> BoardClient:
    """Build the configured board client.

    The one place that names a concrete backend. Every caller that used to
    construct a concrete client from its settings block calls this instead, so
    pointing the fleet at a different board is a change here and nowhere else.

    Kept byte-compatible with the ten hand-rolled `_make_plane_client()` helpers
    it replaces — same four fields, same token accessor — so adopting it cannot
    change behaviour.
    """
    backend = _backend_name(settings)
    _reject_retired(backend)

    if backend != "forgejo":
        raise RuntimeError(f"unknown board_backend {backend!r} (forgejo)")

    from operations_center.adapters.forgejo import ForgejoClient

    cfg = getattr(settings, "forgejo", None)
    if cfg is None:
        raise RuntimeError(
            "board_backend is 'forgejo' but no `forgejo:` settings block is "
            "configured — the fleet has no board to talk to"
        )
    return ForgejoClient(
        base_url=cfg.base_url,
        api_token=settings.forgejo_token(),
        owner=cfg.owner,
        repo=cfg.repo,
    )


def _backend_name(settings: Any) -> str:
    """The configured backend, normalised.

    A test double (`MagicMock()`) answers every attribute with a child mock, so
    `getattr` never reaches its default and the value becomes a Mock — which
    matches neither "plane" nor "forgejo" and used to raise "unknown
    board_backend <MagicMock ...>" from deep inside the factory. Real Settings
    validates this field as a str, so a non-string here means "nothing
    configured this", not "someone chose backend 42".
    """
    backend = getattr(settings, "board_backend", "forgejo")
    return backend if isinstance(backend, str) else "forgejo"


def _reject_retired(backend: str) -> None:
    """Answer an old config honestly.

    "unknown board_backend 'plane'" reads as a typo. Plane was a real backend
    until the 2026-08-18 cutover, so an operator whose config still says it is
    asking a reasonable question and deserves the actual answer.
    """
    if backend == "plane":
        raise RuntimeError(
            "board_backend 'plane' was removed — the Plane adapter is gone as of "
            "the 2026-08-18 Forgejo cutover. Set `board_backend: forgejo` and a "
            "`forgejo:` block (see config/operations_center.example.yaml)."
        )


def board_project_id(settings: Any) -> str:
    """The board's project identifier, from the *active* backend.

    Callers used to read `settings.plane.project_id` directly. With `plane:`
    optional and the example config Forgejo-first, that dereference is an
    `AttributeError` on exactly the config the example recommends — and it sat
    on the dispatch path, so a Forgejo-only operator could execute nothing.

    The value is opaque to its consumers (worker CLI metadata,
    `CampaignBuilder` stores it without reading it), so each backend supplies
    its natural identifier: Plane its project UUID, Forgejo the board's
    `owner/repo`.
    """
    backend = _backend_name(settings)
    _reject_retired(backend)

    if backend != "forgejo":
        raise RuntimeError(f"unknown board_backend {backend!r} (forgejo)")

    cfg = getattr(settings, "forgejo", None)
    if cfg is None:
        raise RuntimeError(
            "board_backend is 'forgejo' but no `forgejo:` settings block is "
            "configured — the fleet has no board to talk to"
        )
    return f"{cfg.owner}/{cfg.repo}"
