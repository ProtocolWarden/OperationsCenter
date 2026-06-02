# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Timing and memory measurement utilities for performance regression tests."""

from __future__ import annotations

import time
import tracemalloc
from typing import Self


class Timing:
    """Context manager for measuring wall-clock time with high precision."""

    def __init__(self) -> None:
        self.start: float | None = None
        self.end: float | None = None

    def __enter__(self) -> Self:
        self.start = time.perf_counter()
        return self

    def __exit__(self, *args: object) -> None:
        self.end = time.perf_counter()

    def elapsed(self) -> float:
        """Return elapsed time in seconds since context entry."""
        if self.start is None or self.end is None:
            raise RuntimeError("Timer not properly used as context manager")
        return self.end - self.start


class MemoryTracker:
    """Context manager for measuring peak memory usage during execution."""

    def __init__(self) -> None:
        self._peak_mb: float | None = None

    def __enter__(self) -> Self:
        tracemalloc.start()
        return self

    def __exit__(self, *args: object) -> None:
        current, peak = tracemalloc.get_traced_memory()
        self._peak_mb = peak / (1024 * 1024)
        tracemalloc.stop()

    @property
    def peak_memory_mb(self) -> float:
        """Return peak memory usage in megabytes during context."""
        if self._peak_mb is None:
            raise RuntimeError("MemoryTracker not properly used as context manager")
        return self._peak_mb
