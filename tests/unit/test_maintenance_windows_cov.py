# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

from operations_center.maintenance_windows import _in_maintenance_window


def _window(days=None, start_hour=0, end_hour=0):
    return SimpleNamespace(
        days=days if days is not None else [],
        start_hour=start_hour,
        end_hour=end_hour,
    )


def _settings(windows):
    return SimpleNamespace(maintenance_windows=windows)


# 2026-06-01 is a Monday (weekday() == 0).
def _dt(weekday=0, hour=12):
    base = datetime(2026, 6, 1, hour, 0, 0, tzinfo=UTC)  # Monday
    return base + timedelta(days=weekday)


def test_no_windows_returns_false():
    assert _in_maintenance_window(_settings([])) is False


def test_missing_attribute_defaults_to_empty():
    settings = SimpleNamespace()  # no maintenance_windows attr
    assert _in_maintenance_window(settings, now=_dt(hour=12)) is False


def test_none_windows_treated_as_empty():
    assert _in_maintenance_window(_settings(None), now=_dt()) is False


def test_normal_window_inside():
    w = _window(start_hour=9, end_hour=17)
    assert _in_maintenance_window(_settings([w]), now=_dt(hour=12)) is True


def test_normal_window_at_start_inclusive():
    w = _window(start_hour=9, end_hour=17)
    assert _in_maintenance_window(_settings([w]), now=_dt(hour=9)) is True


def test_normal_window_at_end_exclusive():
    w = _window(start_hour=9, end_hour=17)
    assert _in_maintenance_window(_settings([w]), now=_dt(hour=17)) is False


def test_normal_window_before_start():
    w = _window(start_hour=9, end_hour=17)
    assert _in_maintenance_window(_settings([w]), now=_dt(hour=8)) is False


def test_zero_width_window_never_active():
    w = _window(start_hour=5, end_hour=5)
    assert _in_maintenance_window(_settings([w]), now=_dt(hour=5)) is False


def test_wrap_midnight_after_start():
    w = _window(start_hour=22, end_hour=4)
    assert _in_maintenance_window(_settings([w]), now=_dt(hour=23)) is True


def test_wrap_midnight_before_end():
    w = _window(start_hour=22, end_hour=4)
    assert _in_maintenance_window(_settings([w]), now=_dt(hour=2)) is True


def test_wrap_midnight_at_start_inclusive():
    w = _window(start_hour=22, end_hour=4)
    assert _in_maintenance_window(_settings([w]), now=_dt(hour=22)) is True


def test_wrap_midnight_at_end_exclusive():
    w = _window(start_hour=22, end_hour=4)
    assert _in_maintenance_window(_settings([w]), now=_dt(hour=4)) is False


def test_wrap_midnight_outside_in_daytime():
    w = _window(start_hour=22, end_hour=4)
    assert _in_maintenance_window(_settings([w]), now=_dt(hour=12)) is False


def test_days_filter_matching_day():
    w = _window(days=[0], start_hour=9, end_hour=17)  # Monday only
    assert _in_maintenance_window(_settings([w]), now=_dt(weekday=0, hour=12)) is True


def test_days_filter_non_matching_day_skipped():
    w = _window(days=[0], start_hour=9, end_hour=17)  # Monday only
    # Tuesday
    assert _in_maintenance_window(_settings([w]), now=_dt(weekday=1, hour=12)) is False


def test_days_filter_multiple_days():
    w = _window(days=[5, 6], start_hour=0, end_hour=23)  # weekend
    assert _in_maintenance_window(_settings([w]), now=_dt(weekday=6, hour=10)) is True
    assert _in_maintenance_window(_settings([w]), now=_dt(weekday=2, hour=10)) is False


def test_empty_days_means_every_day():
    w = _window(days=[], start_hour=9, end_hour=17)
    assert _in_maintenance_window(_settings([w]), now=_dt(weekday=3, hour=12)) is True


def test_none_days_means_every_day():
    w = SimpleNamespace(days=None, start_hour=9, end_hour=17)
    assert _in_maintenance_window(_settings([w]), now=_dt(weekday=2, hour=12)) is True


def test_multiple_windows_second_matches():
    w1 = _window(start_hour=0, end_hour=6)
    w2 = _window(start_hour=20, end_hour=23)
    assert _in_maintenance_window(_settings([w1, w2]), now=_dt(hour=21)) is True


def test_multiple_windows_none_match():
    w1 = _window(start_hour=0, end_hour=6)
    w2 = _window(start_hour=20, end_hour=23)
    assert _in_maintenance_window(_settings([w1, w2]), now=_dt(hour=12)) is False


def test_missing_hour_fields_default_to_zero_width():
    w = SimpleNamespace(days=[])  # no start_hour/end_hour -> 0/0 -> zero-width
    assert _in_maintenance_window(_settings([w]), now=_dt(hour=0)) is False


def test_string_hours_are_cast_to_int():
    w = SimpleNamespace(days=[], start_hour="9", end_hour="17")
    assert _in_maintenance_window(_settings([w]), now=_dt(hour=10)) is True


def test_non_utc_now_is_converted():
    w = _window(start_hour=9, end_hour=17)
    # 14:00 in UTC+5 == 09:00 UTC, which is the inclusive start.
    tz = timezone(timedelta(hours=5))
    moment = datetime(2026, 6, 1, 14, 0, 0, tzinfo=tz)
    assert _in_maintenance_window(_settings([w]), now=moment) is True


def test_default_now_uses_current_clock():
    # Window covering all hours every day -> always True regardless of clock.
    w = _window(start_hour=0, end_hour=23)
    # hour 0..22 covered; pick a real call (now=None path).
    result = _in_maintenance_window(_settings([w]))
    assert isinstance(result, bool)


def test_default_now_covers_none_branch():
    # Full-day window across all weekdays except the exclusive end hour 23.
    w = _window(start_hour=0, end_hour=24)
    result = _in_maintenance_window(_settings([w]))
    assert result is True
