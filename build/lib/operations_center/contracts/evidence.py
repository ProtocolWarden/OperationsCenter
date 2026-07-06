# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
evidence.py — structured evidence models for policy and rule evaluation.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class RuleEvidence(BaseModel):
    """Evidence produced when a policy rule is evaluated against a work item."""

    rule_id: str = Field(description="Canonical identifier of the rule that fired")
    kind: str = Field(description="Rule category or family (e.g. 'policy', 'guardrail', 'lint')")
    matched: bool = Field(description="True if the rule condition was satisfied")
    severity: Optional[str] = Field(
        default=None,
        description="Severity hint when matched is True (e.g. 'low', 'medium', 'high')",
    )
    detail: Optional[str] = Field(
        default=None,
        description="Human-readable explanation or failure detail",
    )

    model_config = {"frozen": True, "extra": "forbid"}
