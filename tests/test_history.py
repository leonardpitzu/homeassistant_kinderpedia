"""Tests for the Kinderpedia history store."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.kinderpedia.api import KinderpediaConnectionError
from custom_components.kinderpedia.coordinator import _parse_timeline
from custom_components.kinderpedia.history import (
    KinderpediaHistoryStore,
    _has_real_data,
    _monday_of,
)
from datetime import date


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_week_raw(monday_iso: str, *, checkin="08:15 - 16:30", meals=True):
    """Build a minimal raw timeline response for a week starting at *monday_iso*."""
    d = date.fromisoformat(monday_iso)
    days = {}
    for i in range(7):
        day = d + __import__("datetime").timedelta(days=i)
        day_iso = day.isoformat()
        data = []
        if i == 0:  # Monday only: fill with data
            data.append({"id": "checkin", "subtitle": checkin})
            if meals:
                data.append({
                    "id": "food_1",
                    "details": {
                        "food": {
                            "meals": [{
                                "type": "md",
                                "percent": 80,
                                "menus": [{"name": "Cereal"}],
                                "totals": {"kcal": 200, "weight": 150},
                            }]
                        }
                    },
                })
        days[day_iso] = {"data": data}

    return {"result": {"dailytimeline": {"days": days}}}


def _make_empty_week_raw(monday_iso: str):
    """Build a raw timeline response with no real data."""
    d = date.fromisoformat(monday_iso)
    days = {}
    for i in range(7):
        day = d + __import__("datetime").timedelta(days=i)
        days[day.isoformat()] = {"data": []}
    return {"result": {"dailytimeline": {"days": days}}}


# ---------------------------------------------------------------------------
# _monday_of
# ---------------------------------------------------------------------------


class TestMondayOf:
    def test_monday_returns_itself(self):
        assert _monday_of(date(2026, 2, 23)) == date(2026, 2, 23)  # Monday

    def test_friday(self):
        assert _monday_of(date(2026, 2, 27)) == date(2026, 2, 23)

    def test_sunday(self):
        assert _monday_of(date(2026, 3, 1)) == date(2026, 2, 23)


# ---------------------------------------------------------------------------
# _has_real_data
# ---------------------------------------------------------------------------


class TestHasRealData:
    def test_with_checkin(self):
        assert _has_real_data({"checkin": "08:00 - 16:00"}) is True

    def test_with_meals(self):
        assert _has_real_data({"checkin": "unknown", "breakfast_items": ["Cereal"]}) is True

    def test_empty(self):
        assert _has_real_data({"checkin": "unknown"}) is False


# ---------------------------------------------------------------------------
# KinderpediaHistoryStore
# ---------------------------------------------------------------------------


class TestHistoryStoreLoadSave:
    """Tests for load/save operations."""

    @pytest.mark.asyncio
    async def test_load_empty_store(self):
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value=None)

        await store.async_load()

        assert store._weeks == {}
        assert store._loaded is True

    @pytest.mark.asyncio
    async def test_load_existing_data(self):
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._store = MagicMock()

        existing = {"weeks": {"2026-02-16": {"monday": {"date": "2026-02-16", "checkin": "08:00"}}}}
        store._store.async_load = AsyncMock(return_value=existing)

        await store.async_load()

        assert "2026-02-16" in store._weeks
        assert store._loaded is True

    @pytest.mark.asyncio
    async def test_save(self):
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._store = MagicMock()
        store._store.async_save = AsyncMock()
        store._weeks = {"2026-02-16": {"monday": {"date": "2026-02-16"}}}

        await store.async_save()

        store._store.async_save.assert_called_once_with({"weeks": store._weeks})


class TestHistoryStoreGetAllDays:
    """Tests for the get_all_days helper."""

    def test_flattens_weeks(self):
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._weeks = {
            "2026-02-16": {
                "monday": {"date": "2026-02-16", "name": "monday", "checkin": "08:00"},
                "tuesday": {"date": "2026-02-17", "name": "tuesday", "checkin": "unknown"},
            },
            "2026-02-09": {
                "monday": {"date": "2026-02-09", "name": "monday", "checkin": "09:00"},
            },
        }

        result = store.get_all_days()

        assert "2026-02-16" in result
        assert "2026-02-17" in result
        assert "2026-02-09" in result
        assert result["2026-02-16"]["checkin"] == "08:00"
        assert result["2026-02-09"]["checkin"] == "09:00"

    def test_empty_store(self):
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        assert store.get_all_days() == {}


class TestHistoryStoreHasWeek:
    def test_existing_week(self):
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._weeks = {"2026-02-16": {}}
        assert store.has_week("2026-02-16") is True

    def test_missing_week(self):
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._weeks = {}
        assert store.has_week("2026-02-16") is False


class TestBackfill:
    """Tests for the backfill method."""

    @pytest.mark.asyncio
    async def test_backfill_stores_weeks(self):
        """Backfill fetches past weeks and stores them."""
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value=None)
        store._store.async_save = AsyncMock()

        week1_raw = _make_week_raw("2026-02-16")
        week2_raw = _make_week_raw("2026-02-09")
        empty_raw = _make_empty_week_raw("2026-02-02")

        call_count = 0

        async def mock_fetch(child_id, kg_id, week_offset=0):
            nonlocal call_count
            call_count += 1
            if week_offset == -1:
                return week1_raw
            elif week_offset == -2:
                return week2_raw
            else:
                return empty_raw

        api = MagicMock()
        api.fetch_timeline = mock_fetch

        count = await store.async_backfill(api, 111, 222, _parse_timeline, delay=0)

        assert count == 2
        assert store.has_week("2026-02-16")
        assert store.has_week("2026-02-09")
        # The whole walk is persisted with a single write.
        assert store._store.async_save.call_count == 1

    @pytest.mark.asyncio
    async def test_backfill_stops_at_existing_week(self):
        """Backfill stops when it hits a week already in the store."""
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value=None)
        store._store.async_save = AsyncMock()

        # Pre-populate week 2026-02-09
        week2_parsed = _parse_timeline(_make_week_raw("2026-02-09"))
        store._weeks["2026-02-09"] = week2_parsed
        store._loaded = True

        week1_raw = _make_week_raw("2026-02-16")
        week2_raw = _make_week_raw("2026-02-09")

        call_count = 0

        async def mock_fetch(child_id, kg_id, week_offset=0):
            nonlocal call_count
            call_count += 1
            if week_offset == -1:
                return week1_raw
            elif week_offset == -2:
                return week2_raw
            return _make_empty_week_raw("2026-02-02")

        api = MagicMock()
        api.fetch_timeline = mock_fetch

        count = await store.async_backfill(api, 111, 222, _parse_timeline, delay=0)

        assert count == 1  # Only week -1 stored; -2 was already there
        assert store.has_week("2026-02-16")

    @pytest.mark.asyncio
    async def test_backfill_stops_on_empty_response(self):
        """Backfill stops when API returns empty data."""
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value=None)
        store._store.async_save = AsyncMock()

        async def mock_fetch(child_id, kg_id, week_offset=0):
            return {}

        api = MagicMock()
        api.fetch_timeline = mock_fetch

        count = await store.async_backfill(api, 111, 222, _parse_timeline, delay=0)

        assert count == 0

    @pytest.mark.asyncio
    async def test_backfill_stops_on_api_error(self):
        """Backfill handles API errors gracefully."""
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value=None)
        store._store.async_save = AsyncMock()

        async def mock_fetch(child_id, kg_id, week_offset=0):
            raise KinderpediaConnectionError("API down")

        api = MagicMock()
        api.fetch_timeline = mock_fetch

        count = await store.async_backfill(api, 111, 222, _parse_timeline, delay=0)

        assert count == 0

    @pytest.mark.asyncio
    async def test_backfill_stops_on_no_real_data(self):
        """Backfill stops when a week has structure but no real data."""
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value=None)
        store._store.async_save = AsyncMock()

        async def mock_fetch(child_id, kg_id, week_offset=0):
            return _make_empty_week_raw("2026-02-16")

        api = MagicMock()
        api.fetch_timeline = mock_fetch

        count = await store.async_backfill(api, 111, 222, _parse_timeline, delay=0)

        assert count == 0


class TestArchiveLastWeek:
    """Tests for the weekly archive method."""

    @pytest.mark.asyncio
    async def test_archive_stores_new_week(self):
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value=None)
        store._store.async_save = AsyncMock()

        week_raw = _make_week_raw("2026-02-16")

        async def mock_fetch(child_id, kg_id, week_offset=0):
            return week_raw

        api = MagicMock()
        api.fetch_timeline = mock_fetch

        result = await store.async_archive_last_week(api, 111, 222, _parse_timeline)

        assert result is True
        assert store.has_week("2026-02-16")
        store._store.async_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_archive_skips_existing_week(self):
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value=None)
        store._store.async_save = AsyncMock()
        store._loaded = True

        # Pre-populate
        store._weeks["2026-02-16"] = _parse_timeline(_make_week_raw("2026-02-16"))

        week_raw = _make_week_raw("2026-02-16")

        async def mock_fetch(child_id, kg_id, week_offset=0):
            return week_raw

        api = MagicMock()
        api.fetch_timeline = mock_fetch

        result = await store.async_archive_last_week(api, 111, 222, _parse_timeline)

        assert result is False
        store._store.async_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_archive_handles_api_error(self):
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value=None)
        store._store.async_save = AsyncMock()

        async def mock_fetch(child_id, kg_id, week_offset=0):
            raise KinderpediaConnectionError("timeout")

        api = MagicMock()
        api.fetch_timeline = mock_fetch

        result = await store.async_archive_last_week(api, 111, 222, _parse_timeline)

        assert result is False

    @pytest.mark.asyncio
    async def test_archive_handles_empty_response(self):
        hass = MagicMock()
        store = KinderpediaHistoryStore(hass, "111_222")
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value=None)
        store._store.async_save = AsyncMock()

        async def mock_fetch(child_id, kg_id, week_offset=0):
            return {}

        api = MagicMock()
        api.fetch_timeline = mock_fetch

        result = await store.async_archive_last_week(api, 111, 222, _parse_timeline)

        assert result is False


class TestMondayFromDays:
    """Tests for the _monday_from_days static method."""

    def test_derives_monday(self):
        days = {
            "wednesday": {"date": "2026-02-25", "name": "wednesday"},
        }
        assert KinderpediaHistoryStore._monday_from_days(days) == "2026-02-23"

    def test_returns_none_for_empty(self):
        assert KinderpediaHistoryStore._monday_from_days({}) is None

    def test_skips_unknown_dates(self):
        days = {
            "monday": {"date": "unknown"},
            "tuesday": {"date": "2026-02-17"},
        }
        assert KinderpediaHistoryStore._monday_from_days(days) == "2026-02-16"
