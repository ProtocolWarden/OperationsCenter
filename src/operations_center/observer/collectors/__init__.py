# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Signal collectors for RepoObserverService.

This module contains collector implementations that gather signals from
various sources for integration into repository observation snapshots.
"""

from operations_center.observer.collectors.flaky_test_collector import FlakyTestCollector

__all__ = [
    "FlakyTestCollector",
]
