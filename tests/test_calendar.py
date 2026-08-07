"""Tests for Kinderpedia calendar platform."""

from datetime import date, datetime, time
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.kinderpedia.coordinator import _parse_timeline
from custom_components.kinderpedia.calendar import KinderpediaCalendar
from tests.conftest import MOCK_CHILD, MOCK_TIMELINE_RAW

MONDAY = "2026-02-09"
WEEK_START = date(2026, 2, 9)
WEEK_END = date(2026, 2, 15)


def _make_coordinator_data():
    """Build coordinator data using the shared fixtures."""
    parsed_days = _parse_timeline(MOCK_TIMELINE_RAW)
    return {
        "last_updated": "2026-02-21 12:00:00",
        "children": {
            "111_222": {
                "child": dict(MOCK_CHILD),
                "days": parsed_days,
            }
        },
    }


def _make_calendar(coordinator) -> KinderpediaCalendar:
    """Create a calendar entity wired to a mock coordinator."""
    return KinderpediaCalendar(
        coordinator,
        child_id=111,
        kg_id=222,
        device_name="Alice Smith",
        first_name="Alice",
    )


def _week_events(cal: KinderpediaCalendar):
    """Build every event of the fixture week."""
    return cal._build_events(WEEK_START, WEEK_END)


# -------------------------------------------------------------------
# Event building
# -------------------------------------------------------------------

async def test_calendar_builds_timed_school_events(hass: HomeAssistant):
    """School events must be timed (datetime start/end), not all-day."""
    coordinator = MagicMock()
    coordinator.data = _make_coordinator_data()

    cal = _make_calendar(coordinator)
    events = _week_events(cal)

    school_events = [e for e in events if "School" in (e.summary or "")]
    assert len(school_events) >= 1

    monday_school = [e for e in school_events if e.summary == "School" and isinstance(e.start, datetime) and e.start.date() == date(2026, 2, 9)]
    assert len(monday_school) == 1
    ev = monday_school[0]

    # Must be timed, not all-day
    assert isinstance(ev.start, datetime)
    assert isinstance(ev.end, datetime)
    assert ev.start.hour == 8
    assert ev.start.minute == 15
    # End at 18:00
    assert ev.end.hour == 18
    assert ev.end.minute == 0


async def test_calendar_school_event_has_tz(hass: HomeAssistant):
    """Timed school events must carry timezone info."""
    coordinator = MagicMock()
    coordinator.data = _make_coordinator_data()

    cal = _make_calendar(coordinator)
    events = _week_events(cal)

    school_events = [e for e in events if "School" in (e.summary or "")]
    for ev in school_events:
        if isinstance(ev.start, datetime):
            assert ev.start.tzinfo is not None
            assert ev.end.tzinfo is not None


async def test_calendar_nap_event_unchanged(hass: HomeAssistant):
    """Nap events still use actual nap start/end times."""
    coordinator = MagicMock()
    coordinator.data = _make_coordinator_data()

    cal = _make_calendar(coordinator)
    events = _week_events(cal)

    nap_events = [e for e in events if e.summary == "Nap"]
    assert len(nap_events) == 1
    nap = nap_events[0]
    assert isinstance(nap.start, datetime)
    assert isinstance(nap.end, datetime)
    assert nap.start.hour == 12 and nap.start.minute == 39
    assert nap.end.hour == 14 and nap.end.minute == 33
    assert nap.start.tzinfo is not None


async def test_calendar_event_description_has_emoji_meals(hass: HomeAssistant):
    """Event description uses emoji-prefixed meal lines."""
    coordinator = MagicMock()
    coordinator.data = _make_coordinator_data()

    cal = _make_calendar(coordinator)
    events = _week_events(cal)

    monday_school = [
        e for e in events
        if isinstance(e.start, datetime) and e.start.date() == date(2026, 2, 9) and "School" in (e.summary or "")
    ]
    assert len(monday_school) == 1

    desc = monday_school[0].description or ""
    assert "🥣" in desc  # breakfast icon
    assert "Breakfast" in desc
    assert "Cereal" in desc
    assert "<br>" in desc  # meal lines are separated the way the cards expect
    assert "🍽️" in desc  # lunch icon
    assert "Chicken soup" in desc
    assert "🍪" in desc  # snack icon
    assert "Apple" in desc

    # Percent shown
    assert "(80%)" in desc  # breakfast 80%
    assert "(90" in desc  # lunch 90% (may be 90.0% due to averaging)

    # Check-in and Nap must NOT appear in description
    assert "Check-in" not in desc
    assert "Nap" not in desc


# -------------------------------------------------------------------
# .event property
# -------------------------------------------------------------------

async def test_calendar_event_property_returns_today(hass: HomeAssistant, freezer):
    """The .event property returns an event for today if one exists."""
    freezer.move_to("2026-02-09T10:00:00+00:00")

    coordinator = MagicMock()
    coordinator.data = _make_coordinator_data()

    cal = _make_calendar(coordinator)
    ev = cal.event

    assert ev is not None
    assert ev.start.date() == date(2026, 2, 9)


async def test_calendar_event_property_none_outside_school_days(
    hass: HomeAssistant, freezer
):
    """A day without data produces no current event."""
    freezer.move_to("2026-02-14T10:00:00+00:00")

    coordinator = MagicMock()
    coordinator.data = _make_coordinator_data()

    assert _make_calendar(coordinator).event is None


# -------------------------------------------------------------------
# async_get_events range filtering
# -------------------------------------------------------------------

async def test_calendar_async_get_events_filters_range(hass: HomeAssistant):
    """async_get_events should filter by the requested date range."""
    coordinator = MagicMock()
    coordinator.data = _make_coordinator_data()

    cal = _make_calendar(coordinator)

    start = datetime(2026, 2, 9, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    end = datetime(2026, 2, 10, tzinfo=dt_util.DEFAULT_TIME_ZONE)

    events = await cal.async_get_events(hass, start, end)
    for e in events:
        ev_date = e.start.date() if isinstance(e.start, datetime) else e.start
        assert ev_date >= date(2026, 2, 9)
        assert ev_date < date(2026, 2, 10)


async def test_calendar_async_get_events_full_week(hass: HomeAssistant):
    """Requesting the full week should return events for days with data."""
    coordinator = MagicMock()
    coordinator.data = _make_coordinator_data()

    cal = _make_calendar(coordinator)

    start = datetime(2026, 2, 9, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    end = datetime(2026, 2, 14, tzinfo=dt_util.DEFAULT_TIME_ZONE)

    events = await cal.async_get_events(hass, start, end)
    assert len(events) >= 2


# -------------------------------------------------------------------
# extra_state_attributes
# -------------------------------------------------------------------

async def test_calendar_extra_state_attributes_today(hass: HomeAssistant, freezer):
    """Calendar entity exposes today's day data as attributes when today has data."""
    freezer.move_to("2026-02-09T18:30:00+00:00")

    coordinator = MagicMock()
    coordinator.data = _make_coordinator_data()

    cal = _make_calendar(coordinator)
    attrs = cal.extra_state_attributes

    assert "checkin" in attrs
    assert "last_updated" in attrs
    assert attrs["checkin"] == "08:15 - 16:30"
    assert "breakfast_items" in attrs
    assert "Cereal" in attrs["breakfast_items"]
    assert "breakfast_percent" in attrs
    assert attrs["breakfast_percent"] == 80
    assert attrs["date"] == MONDAY


async def test_calendar_extra_state_attributes_falls_back_to_latest(
    hass: HomeAssistant, freezer
):
    """When today has no data, attributes show the most recent school day."""
    freezer.move_to("2026-02-13T12:00:00+00:00")

    coordinator = MagicMock()
    coordinator.data = _make_coordinator_data()
    cal = _make_calendar(coordinator)
    attrs = cal.extra_state_attributes

    # Monday is the only day with real activity in MOCK_TIMELINE_RAW
    assert attrs.get("date") == MONDAY
    assert attrs["checkin"] == "08:15 - 16:30"
    assert "Cereal" in attrs["breakfast_items"]


async def test_calendar_extra_state_attributes_skips_empty_days(
    hass: HomeAssistant, freezer
):
    """Days with no checkin or meals are not returned as the latest day."""
    freezer.move_to("2026-02-13T12:00:00+00:00")

    coordinator = MagicMock()
    data = _make_coordinator_data()
    monday = data["children"]["111_222"]["days"][MONDAY]
    monday["checkin"] = "unknown"
    for meal in ("breakfast", "lunch", "snack"):
        monday.pop(f"{meal}_items", None)
    coordinator.data = data

    assert _make_calendar(coordinator).extra_state_attributes == {}


# -------------------------------------------------------------------
# Edge cases
# -------------------------------------------------------------------

async def test_calendar_no_data(hass: HomeAssistant):
    """No crash when coordinator data is empty."""
    coordinator = MagicMock()
    coordinator.data = None

    cal = _make_calendar(coordinator)
    assert cal.event is None
    events = await cal.async_get_events(
        hass,
        datetime(2026, 2, 9, tzinfo=dt_util.DEFAULT_TIME_ZONE),
        datetime(2026, 2, 14, tzinfo=dt_util.DEFAULT_TIME_ZONE),
    )
    assert events == []
    assert cal.extra_state_attributes == {}


async def test_nap_event_not_created_without_times(hass: HomeAssistant):
    """When nap subtitle has only duration (no times), no nap event is created."""
    coordinator = MagicMock()
    data = _make_coordinator_data()
    data["children"]["111_222"]["days"][MONDAY]["nap"] = "1 h and 30 min"
    coordinator.data = data

    cal = _make_calendar(coordinator)
    events = _week_events(cal)

    nap_events = [e for e in events if e.summary == "Nap"]
    assert len(nap_events) == 0


async def test_nap_event_not_created_with_partial_times(hass: HomeAssistant):
    """When nap has a start time but no end time (API glitch), no nap event."""
    coordinator = MagicMock()
    data = _make_coordinator_data()
    monday = data["children"]["111_222"]["days"][MONDAY]

    partial_values = [
        "12:39 - ",
        "12:39 -",
        "12:39",
        "12:39 - , 1 h",
        " - 14:33",
    ]
    for nap_text in partial_values:
        monday["nap"] = nap_text
        coordinator.data = data

        cal = _make_calendar(coordinator)
        events = _week_events(cal)

        nap_events = [e for e in events if e.summary == "Nap"]
        assert len(nap_events) == 0, f"Nap event should not be created for: {nap_text!r}"


async def test_school_event_no_checkin_uses_fallback(hass: HomeAssistant):
    """School event without valid checkin time starts at 08:00 (fallback)."""
    coordinator = MagicMock()
    data = _make_coordinator_data()
    data["children"]["111_222"]["days"][MONDAY]["checkin"] = "unknown"
    coordinator.data = data

    cal = _make_calendar(coordinator)
    events = _week_events(cal)

    monday_school = [
        e for e in events
        if isinstance(e.start, datetime) and e.start.date() == date(2026, 2, 9) and "School" in (e.summary or "")
    ]
    assert len(monday_school) == 1
    ev = monday_school[0]
    assert ev.start.hour == 8
    assert ev.start.minute == 0
    assert ev.end.hour == 18
    assert ev.end.minute == 0


async def test_parse_checkin_time():
    """_parse_checkin_time extracts HH:MM from various checkin formats."""
    assert KinderpediaCalendar._parse_checkin_time("07:40 - by Alina Vieriu") == time(7, 40)
    assert KinderpediaCalendar._parse_checkin_time("08:15 - 16:30") == time(8, 15)
    assert KinderpediaCalendar._parse_checkin_time("unknown") is None
    assert KinderpediaCalendar._parse_checkin_time("") is None
    assert KinderpediaCalendar._parse_checkin_time("Not completed") is None


# -------------------------------------------------------------------
# Absence handling
# -------------------------------------------------------------------

async def test_absent_day_has_no_school_event(hass: HomeAssistant):
    """When a child is absent, no School event should be created for that day."""
    coordinator = MagicMock()
    data = _make_coordinator_data()

    # Mark Monday as absent (but keep meal data — the menu is still published)
    monday = data["children"]["111_222"]["days"][MONDAY]
    monday["absent"] = True
    monday["checkin"] = "Absent"
    monday["absence_reason"] = "vacation"

    coordinator.data = data
    cal = _make_calendar(coordinator)
    events = _week_events(cal)

    monday_events = [
        e for e in events
        if isinstance(e.start, datetime) and e.start.date() == date(2026, 2, 9)
    ]
    assert len(monday_events) == 0, "No events should be created for an absent day"


async def test_absent_day_has_no_nap_event(hass: HomeAssistant):
    """When a child is absent, no Nap event should be created either."""
    coordinator = MagicMock()
    data = _make_coordinator_data()

    data["children"]["111_222"]["days"][MONDAY]["absent"] = True
    data["children"]["111_222"]["days"][MONDAY]["checkin"] = "Absent"

    coordinator.data = data
    cal = _make_calendar(coordinator)
    events = _week_events(cal)

    nap_events = [e for e in events if e.summary == "Nap"]
    assert len(nap_events) == 0, "No nap event for an absent day"


async def test_non_absent_day_still_has_school_event(hass: HomeAssistant):
    """Days without the absent flag should still produce events normally."""
    coordinator = MagicMock()
    data = _make_coordinator_data()
    # Ensure absent is not set
    data["children"]["111_222"]["days"][MONDAY].pop("absent", None)

    coordinator.data = data
    cal = _make_calendar(coordinator)
    events = _week_events(cal)

    monday_school = [
        e for e in events
        if isinstance(e.start, datetime) and e.start.date() == date(2026, 2, 9) and e.summary == "School"
    ]
    assert len(monday_school) == 1
