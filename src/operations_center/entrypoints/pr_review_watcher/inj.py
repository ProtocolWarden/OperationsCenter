# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Injection-defense helpers for the reviewer (INJ Phase 1, outer + sanitization).

These are *defense-in-depth*, never load-bearing (the load-bearing control is the
code-computed verdict in ``verdict.py``):

- ``make_nonce`` / ``fence`` / ``UNTRUSTED_PREAMBLE`` — wrap every untrusted span
  (PR title, diff, campaign spec, tool output) in a per-run *randomized* sentinel
  with a system preamble (D-INJ outer, §2.2.5). A static sentinel is trivially
  closed by the attacker; the nonce is the only version worth shipping. Still
  defeated by instruction-via-data — hence outer-only.
- ``sanitize_for_comment`` — defang model/untrusted text before reflecting it to
  GitHub (§2.2.4): neutralize ``@``-mentions, strip zero-width / bidi control
  chars, bound length. Stops injection-to-output (steering the next reader/pass).

The primitives now live in the shared ``operations_center.injection`` module so
the worker/executor ingestion path can use the same fence; this module re-exports
them for backward compatibility (existing imports + tests reference it here).
"""

from __future__ import annotations

from operations_center.injection import (
    UNTRUSTED_PREAMBLE,
    fence,
    make_nonce,
    sanitize_for_comment,
)

__all__ = [
    "UNTRUSTED_PREAMBLE",
    "fence",
    "make_nonce",
    "sanitize_for_comment",
]
