"""Persistent historical data store for Kinderpedia.

Past weeks never change, so we fetch each week once and persist it using
Home Assistant's ``helpers.storage.Store``.  The store is keyed by the
Monday date of each week (ISO format, e.g. ``2026-02-23``).

Three operations are supported:

* **Initial backfill** – walk backwards from last week until the store
  already has the data or the API returns an empty week.  Runs once
  at first install, throttled to one request per *delay* seconds.

* **Weekly archive** – called once per week (typically early Monday) to
  store the just-completed previous week.

* **Manual re-sync** – exposed as a Home Assistant service so the user
  can trigger a full backfill on demand.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date as date_cls, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "kinderpedia_history"

# Delay between API requests during backfill (seconds).
BACKFILL_DELAY_SECONDS = 5

# Weeks of history kept on disk (~2 school years); older weeks are dropped.
MAX_HISTORY_WEEKS = 104


def _monday_of(d: date_cls) -> date_cls:
    """Return the Monday of the ISO week containing *d*."""
    return d - timedelta(days=d.weekday())


def _has_real_data(day_entry: dict) -> bool:
    """Return True if a parsed day entry contains meaningful information."""
    checkin = day_entry.get("checkin", "unknown")
    if checkin and checkin != "unknown":
        return True
    return any(day_entry.get(f"{meal}_items") for meal in ("breakfast", "lunch", "snack"))


class KinderpediaHistoryStore:
    """Persistent store for historical weekly timeline data."""

    def __init__(self, hass: HomeAssistant, child_key: str) -> None:
        self.hass = hass
        self._child_key = child_key
        self._store = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}_{child_key}",
        )
        # {monday_iso_str: {weekday_name: day_entry, ...}}
        self._weeks: dict[str, dict] = {}
        # Flattened {date_iso: day_entry} cache; rebuilt lazily, invalidated
        # whenever the underlying week data changes.
        self._all_days_cache: dict[str, dict] | None = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def async_load(self) -> None:
        """Load data from disk."""
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._weeks = stored.get("weeks", {})
        self._all_days_cache = None
        self._loaded = True

    async def async_save(self) -> None:
        """Persist current data to disk, dropping weeks past the retention limit."""
        if len(self._weeks) > MAX_HISTORY_WEEKS:
            keep = sorted(self._weeks, reverse=True)[:MAX_HISTORY_WEEKS]
            self._weeks = {monday: self._weeks[monday] for monday in keep}
        self._all_days_cache = None
        await self._store.async_save({"weeks": self._weeks})

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    @property
    def weeks(self) -> dict[str, dict]:
        """Return the raw week dict (monday_iso → weekday dict)."""
        return self._weeks

    def get_all_days(self) -> dict[str, dict]:
        """Return *all* historical days, keyed by ``date_iso`` string.

        This flattens the week structure so the coordinator can merge
        history with the live current-week data.  The result is cached and
        only recomputed after the underlying weeks change.
        """
        if self._all_days_cache is not None:
            return self._all_days_cache

        result: dict[str, dict] = {}
        for _monday, week_days in self._weeks.items():
            for _weekday, day_entry in week_days.items():
                d = day_entry.get("date")
                if d:
                    result[d] = day_entry
        self._all_days_cache = result
        return result

    def has_week(self, monday_iso: str) -> bool:
        """Return True if the given week is already stored."""
        return monday_iso in self._weeks

    def days_in_range(self, start: date_cls, end: date_cls) -> dict[str, dict]:
        """Return stored days whose date falls within [start, end]."""
        start_iso = start.isoformat()
        end_iso = end.isoformat()
        return {
            date_iso: day
            for date_iso, day in self.get_all_days().items()
            if start_iso <= date_iso <= end_iso
        }

    # ------------------------------------------------------------------
    # Backfill
    # ------------------------------------------------------------------

    async def async_backfill(
        self,
        api,
        child_id: int,
        kg_id: int,
        parse_fn,
        *,
        delay: int = BACKFILL_DELAY_SECONDS,
    ) -> int:
        """Walk backwards through weeks until history is exhausted.

        *parse_fn* is ``coordinator._parse_timeline``.
        Returns the number of newly stored weeks.
        """
        if not self._loaded:
            await self.async_load()

        stored_count = 0
        offset = -1

        while True:
            _LOGGER.debug("Backfill: fetching week offset %d for child %s_%s", offset, child_id, kg_id)
            try:
                raw = await api.fetch_timeline(child_id, kg_id, week_offset=offset)
            except HomeAssistantError:
                _LOGGER.debug(
                    "Backfill: API error at week offset %d, stopping", offset,
                    exc_info=True,
                )
                break

            days = parse_fn(raw)
            if not days:
                _LOGGER.debug(
                    "Backfill: no parseable days at offset %d, stopping (raw keys: %s)",
                    offset,
                    list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
                )
                break

            # The first date in the parsed days tells us which Monday it is
            monday_iso = self._monday_from_days(days)
            if not monday_iso:
                _LOGGER.debug(
                    "Backfill: could not determine monday from offset %d (day dates: %s), stopping",
                    offset,
                    [d.get('date') for d in days.values()],
                )
                break

            if monday_iso in self._weeks:
                _LOGGER.debug(
                    "Backfill: week %s already stored, caught up", monday_iso
                )
                break

            if not any(_has_real_data(d) for d in days.values()):
                _LOGGER.debug(
                    "Backfill: week %s has no real data (enrollment start?), stopping", monday_iso
                )
                break

            self._weeks[monday_iso] = days
            stored_count += 1
            _LOGGER.debug("Backfill: stored week %s (offset %d, total %d)", monday_iso, offset, stored_count)

            offset -= 1
            if delay > 0:
                await asyncio.sleep(delay)

        # One write for the whole walk instead of one per week.
        if stored_count:
            await self.async_save()

        _LOGGER.debug("Backfill complete for %s_%s: %d new weeks stored", child_id, kg_id, stored_count)
        return stored_count

    async def async_archive_last_week(
        self,
        api,
        child_id: int,
        kg_id: int,
        parse_fn,
    ) -> bool:
        """Fetch and store last week's data (week offset -1).

        Returns True if a new week was stored.
        """
        if not self._loaded:
            await self.async_load()

        try:
            raw = await api.fetch_timeline(child_id, kg_id, week_offset=-1)
        except HomeAssistantError:
            _LOGGER.debug("Weekly archive: API error fetching last week")
            return False

        days = parse_fn(raw)
        if not days:
            return False

        monday_iso = self._monday_from_days(days)
        if not monday_iso:
            return False

        if monday_iso in self._weeks:
            _LOGGER.debug("Weekly archive: week %s already stored", monday_iso)
            return False

        self._weeks[monday_iso] = days
        await self.async_save()
        _LOGGER.debug("Weekly archive: stored week %s", monday_iso)
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _monday_from_days(days: dict) -> str | None:
        """Derive the Monday ISO date from parsed day entries."""
        for day_entry in days.values():
            d = day_entry.get("date")
            if d and d != "unknown":
                try:
                    parsed = date_cls.fromisoformat(d)
                    return _monday_of(parsed).isoformat()
                except (ValueError, TypeError):
                    continue
        return None
