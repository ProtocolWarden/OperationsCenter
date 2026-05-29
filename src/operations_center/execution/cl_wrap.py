# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""ContextLifecycle hydrate/capture wrap for the execution coordinator.

ADR 0002 P4 — wraps each executor dispatch in CL's pre/around/post hooks.
The wrap is **lineage-scoped**: each `ExecutionCoordinator.execute()` call
derives a `lineage_id` from the request, calls `cl.hydrate()` before the
adapter runs, and `cl.capture()` after — even on adapter exception, so
failed lineages still leave a trace under the anchor manifest.

The wrap is a no-op when:
- `CL_ANCHOR` is unset (no session anchored — opt-in by env)
- `context_lifecycle` is not importable
- `cl.hydrate` raises `AnchorMissing` / `SessionNotStarted`

This keeps every existing OC test that doesn't anchor a session passing
unchanged; CL integration activates only inside an anchored session.

Per ADR 0002 P4.3, `cl.peek` is NOT wired into routing here — it's
available to callers but the dispatcher itself doesn't consult it.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lineage derivation
# ---------------------------------------------------------------------------


def derive_lineage_id(work_item: Any) -> str:
    """Derive a stable lineage id from a work item.

    Preferred sources, in order:
      1. ``work_item.lineage_id`` (explicit override)
      2. ``work_item.run_id`` — ExecutionRequest stamps a fresh uuid here
      3. ``work_item.proposal_id`` — coarser; one lineage per proposal
      4. fallback ``"l-unknown"`` (last resort; should never hit in
         production because ExecutionRequest always has a run_id)
    """
    for attr in ("lineage_id", "run_id", "proposal_id"):
        val = getattr(work_item, attr, None)
        if isinstance(val, str) and val:
            return val if val.startswith("l-") else f"l-{val}"
    return "l-unknown"


# ---------------------------------------------------------------------------
# Work-item / result serialization
# ---------------------------------------------------------------------------


def _to_dict(obj: Any) -> dict[str, Any]:
    """Best-effort conversion of a pydantic / dataclass / dict to dict.

    Used to hand work items and results to CL without forcing CL to know
    OC's domain types. CL only reads scalar fields (repo names, ids) —
    so JSON-mode dump is fine.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except Exception:  # noqa: BLE001 - never break dispatch over serialization
            try:
                return dump()
            except Exception:  # noqa: BLE001
                pass
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {"value": repr(obj)}


# ---------------------------------------------------------------------------
# CL availability gate
# ---------------------------------------------------------------------------


def _cl_active() -> bool:
    """True iff a CL session is anchored AND context_lifecycle imports."""
    if not os.environ.get("CL_ANCHOR"):
        return False
    try:
        import context_lifecycle  # noqa: F401,PGH003
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# Public wrap
# ---------------------------------------------------------------------------


@contextmanager
def cl_dispatch_wrap(work_item: Any) -> Iterator[dict[str, Any]]:
    """Context manager wrapping a single executor dispatch.

    Usage::

        with cl_dispatch_wrap(request) as ctx:
            # ctx is a dict with at least {"lineage_id": ...}; empty when
            # CL is not active. Run the adapter inside the with-block and
            # call ctx["set_result"](result_dict) before exiting to drive
            # the post-dispatch capture. Exceptions propagate normally;
            # capture still fires with an error payload.

    The block is a strict no-op (no CL imports, no env lookups beyond the
    initial gate) when CL is not active, so existing OC tests that don't
    anchor a session are not affected.
    """
    if not _cl_active():
        # No-op path. Yield a stub so the caller's `ctx["set_result"]`
        # idiom works uniformly.
        state: dict[str, Any] = {"lineage_id": None, "set_result": lambda _r: None}
        yield state
        return

    from context_lifecycle import (  # noqa: PGH003
        AnchorMissing,
        SessionNotStarted,
        capture,
        hydrate,
    )

    lineage_id = derive_lineage_id(work_item)
    work_item_dict = _to_dict(work_item)
    captured_result: dict[str, Any] = {}

    try:
        hydrated = hydrate(lineage_id, work_item_dict)
    except (AnchorMissing, SessionNotStarted) as exc:
        # Anchor disappeared between gate check and hydrate (unlikely
        # but cheap to handle). Fall back to no-op.
        logger.debug("CL hydrate skipped: %s", exc)
        state = {"lineage_id": lineage_id, "set_result": lambda _r: None}
        yield state
        return
    except Exception as exc:  # noqa: BLE001 - never break dispatch on CL errors
        logger.warning("CL hydrate failed for lineage=%s: %s", lineage_id, exc)
        state = {"lineage_id": lineage_id, "set_result": lambda _r: None}
        yield state
        return

    def _set(result: Any) -> None:
        captured_result.clear()
        captured_result.update(_to_dict(result))

    state = {
        "lineage_id": lineage_id,
        "hydrated_context": hydrated,
        "set_result": _set,
    }

    exc_info: BaseException | None = None
    try:
        yield state
    except BaseException as exc:  # noqa: BLE001 - we re-raise below
        exc_info = exc
        raise
    finally:
        payload: dict[str, Any]
        if exc_info is not None:
            payload = {
                "lineage_id": lineage_id,
                "status": "error",
                "error": f"{type(exc_info).__name__}: {exc_info}",
            }
        elif captured_result:
            payload = dict(captured_result)
            payload.setdefault("lineage_id", lineage_id)
        else:
            payload = {"lineage_id": lineage_id, "status": "no_result"}

        try:
            capture(lineage_id, payload)
        except Exception as cap_exc:  # noqa: BLE001 - capture must not mask dispatch
            logger.warning(
                "CL capture failed for lineage=%s: %s", lineage_id, cap_exc,
            )
