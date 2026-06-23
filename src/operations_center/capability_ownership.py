# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""capability_ownership.py — synchronous capability-owner resolution (C2).

The audit (determinism surface 1) found that exactly-one-owns is enforced only by
an async Custodian registry-lint that no-ops in OC's own environment — "code acts
first; the sweep flags drift later." This module provides the *synchronous* check
the thesis requires: resolve a capability's owner from the registry at the moment
of invocation and fail-CLOSED if ownership is ambiguous (≠1 owner).

HONEST SCOPE — dormant-by-environment. OC's pinned ``repograph`` wheel does not
ship the ``capabilities`` plane (it lives in the RepoGraph source / PlatformManifest
registry, not OC's runtime deps). So ``load_capability_registry`` returns None here
today and the guard DEGRADES (proceeds) rather than blocking — the mechanism is
wired and tested, but it does not become load-bearing until the capability plane is
an OC runtime dependency. That binding is a dependency-pinning concern OC cannot
self-resolve; this is the EVAL "blocking-deferred" pattern, not a live enforcement.
Do not claim it closes surface 1 until the registry actually loads in production.

Default posture is opt-in + fail-open (§0.1): the guard is a no-op unless
``require_capability_owner`` is set, and an unavailable registry degrades rather
than halting critical self-healing (board_unblock).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class AmbiguousOwnerError(ValueError):
    """A capability does not have exactly one owner — a real integrity violation."""


def _edge_is_owns(edge: object) -> bool:
    kind = getattr(edge, "kind", None)
    # string-robust: works against the real CapabilityEdgeKind enum (.value ==
    # "owns") and any duck-typed test double.
    return str(getattr(kind, "value", kind)).lower() == "owns"


def resolve_owner(registry: object, action_id: str) -> str:
    """Return the single owner repo_id of ``action_id`` or raise.

    Mirrors RepoGraph's build-time owner-count invariant
    (capabilities/validation.py) but applied synchronously at call time. Raises
    ``AmbiguousOwnerError`` on zero or multiple OWNS edges.
    """

    owners = [
        getattr(e, "target_id", None)
        for e in getattr(registry, "edges", ())
        if getattr(e, "source_id", None) == action_id and _edge_is_owns(e)
    ]
    owners = [o for o in owners if o]
    if len(owners) != 1:
        raise AmbiguousOwnerError(
            f"capability {action_id!r} must have exactly one owner, found {len(owners)}"
        )
    return owners[0]


def load_capability_registry():
    """Best-effort load of the capability registry, or None if unavailable.

    Returns None in OC's current environment (the pinned ``repograph`` wheel has
    no capabilities plane) — callers must treat None as "cannot verify" and
    degrade, never halt.
    """

    import importlib

    try:
        mod = importlib.import_module("repograph")
    except Exception:  # noqa: BLE001 — repograph absent entirely
        return None
    loader = getattr(mod, "load_capability_registry", None)
    if loader is None:
        # the pinned wheel has no capabilities plane — the expected case today
        return None
    try:
        return loader()
    except Exception as exc:  # noqa: BLE001
        logger.warning("capability_ownership: registry load failed — %s", exc)
        return None


def verify_owner_or_degrade(
    action_id: str,
    *,
    required: bool,
    expected_owner: str | None = None,
    registry: object | None = None,
) -> bool:
    """Gate for a capability invocation. Returns True = proceed, False = refuse.

    * ``required`` False → no-op, always proceeds (the default).
    * registry unavailable → DEGRADE (proceed) with an observable warning; never
      halt critical self-healing on a missing registry (§0.1).
    * registry available + exactly one owner (matching ``expected_owner`` if
      given) → proceed.
    * registry available + ambiguous owner, or owner ≠ expected → REFUSE
      (fail-closed): this is the real integrity violation worth blocking on.
    """

    if not required:
        return True
    reg = registry if registry is not None else load_capability_registry()
    if reg is None:
        logger.warning(
            "capability_ownership: registry unavailable, cannot verify %r — degrading "
            '(proceeding) {"event": "capability_owner_unverifiable", "action": "%s"}',
            action_id,
            action_id,
        )
        return True
    try:
        owner = resolve_owner(reg, action_id)
    except AmbiguousOwnerError as exc:
        logger.error(
            "capability_ownership: REFUSING %r — %s "
            '{"event": "capability_owner_ambiguous", "action": "%s"}',
            action_id,
            exc,
            action_id,
        )
        return False
    if expected_owner is not None and owner != expected_owner:
        logger.error(
            "capability_ownership: REFUSING %r — owner %r != expected %r",
            action_id,
            owner,
            expected_owner,
        )
        return False
    return True


__all__ = [
    "AmbiguousOwnerError",
    "load_capability_registry",
    "resolve_owner",
    "verify_owner_or_degrade",
]
