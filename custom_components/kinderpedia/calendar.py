"""Calendar platform for Kinderpedia."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import KinderpediaConfigEntry, KinderpediaDataUpdateCoordinator
from .entity import KinderpediaChildEntity
from .history import KinderpediaHistoryStore

_LOGGER = logging.getLogger(__name__)

_NAP_TIME_RE = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
_CHECKIN_TIME_RE = re.compile(r"^(\d{1,2}:\d{2})")

_SCHOOL_END_TIME = time(18, 0)
_SCHOOL_FALLBACK_START = time(8, 0)

_MEAL_ICONS = {"breakfast": "🥣", "lunch": "🍽️", "snack": "🍪"}


def _as_datetime(value: date | datetime) -> datetime:
    """Return *value* as an aware datetime in the Home Assistant timezone."""
    if isinstance(value, datetime):
        return dt_util.as_local(value)
    return datetime.combine(value, time.min, tzinfo=dt_util.DEFAULT_TIME_ZONE)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: KinderpediaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kinderpedia calendar entities."""
    runtime = config_entry.runtime_data
    coordinator = runtime.coordinator
    tracked_keys: set[str] = set()

    @callback
    def _discover_new_children() -> None:
        data = coordinator.data or {}
        new_entities = []

        for key, child_data in data.get("children", {}).items():
            if key in tracked_keys:
                continue
            tracked_keys.add(key)

            child = child_data["child"]
            new_entities.append(
                KinderpediaCalendar(
                    coordinator,
                    child["child_id"],
                    child["kindergarten_id"],
                    f"{child['first_name']} {child['last_name']}".strip(),
                    child["first_name"],
                    runtime.history_stores.get(key),
                )
            )

        if new_entities:
            async_add_entities(new_entities)

    _discover_new_children()
    config_entry.async_on_unload(coordinator.async_add_listener(_discover_new_children))


class KinderpediaCalendar(KinderpediaChildEntity, CalendarEntity):
    """Calendar showing daily school activities for a child."""

    def __init__(
        self,
        coordinator: KinderpediaDataUpdateCoordinator,
        child_id: int,
        kg_id: int,
        device_name: str,
        first_name: str,
        history_store: KinderpediaHistoryStore | None = None,
    ) -> None:
        """Initialise the calendar entity."""
        super().__init__(coordinator, child_id, kg_id, device_name)
        self._attr_unique_id = f"{DOMAIN}_calendar_{child_id}_{kg_id}"
        self._attr_name = f"{first_name.lower()} school"
        self._history_store = history_store

    # ------------------------------------------------------------------
    # CalendarEntity interface
    # ------------------------------------------------------------------

    @property
    def event(self) -> CalendarEvent | None:
        """Return the ongoing or next event of today."""
        now = dt_util.now()
        events = sorted(self._build_events(now.date(), now.date()), key=lambda ev: ev.start)

        for ev in events:
            if isinstance(ev.end, datetime) and ev.end >= now:
                return ev
        return events[0] if events else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the latest school-day data as entity attributes."""
        day_info = self._latest_day_info()
        if not day_info:
            return {}
        attrs: dict[str, Any] = {
            "date": day_info.get("date"),
            "last_updated": self._last_updated,
        }
        attrs.update(
            {key: val for key, val in day_info.items() if key not in ("name", "date")}
        )
        return attrs

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return events overlapping [start_date, end_date)."""
        return [
            ev
            for ev in self._build_events(start_date.date(), end_date.date())
            if _as_datetime(ev.start) < end_date and _as_datetime(ev.end) > start_date
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _days_in_range(self, start: date, end: date) -> list[dict]:
        """Return day entries between *start* and *end*, live data winning."""
        days: dict[str, dict] = {}
        if self._history_store is not None:
            days.update(self._history_store.days_in_range(start, end))

        start_iso, end_iso = start.isoformat(), end.isoformat()
        days.update({
            date_iso: day
            for date_iso, day in self._days.items()
            if start_iso <= date_iso <= end_iso
        })
        return list(days.values())

    def _latest_day_info(self) -> dict | None:
        """Return the most recent day with real activity, today included."""
        today = dt_util.now().date()
        # A fortnight is enough to skip a holiday and still find a school day.
        candidates = [
            day
            for day in self._days_in_range(today - timedelta(days=14), today)
            if self._has_activity(day)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda day: day.get("date", ""))

    @staticmethod
    def _has_activity(day_info: dict) -> bool:
        """Return True if a day has meaningful data (checkin or meals)."""
        checkin = day_info.get("checkin", "unknown")
        if checkin and checkin != "unknown":
            return True
        return any(day_info.get(f"{meal}_items") for meal in ("breakfast", "lunch", "snack"))

    def _build_events(self, start: date, end: date) -> list[CalendarEvent]:
        """Build calendar events for the days between *start* and *end*."""
        events: list[CalendarEvent] = []

        for day_info in self._days_in_range(start, end):
            try:
                event_date = date.fromisoformat(day_info.get("date", ""))
            except (ValueError, TypeError):
                continue

            if day_info.get("absent"):
                continue

            if nap_event := self._build_nap_event(event_date, day_info.get("nap", "unknown")):
                events.append(nap_event)

            if school_event := self._build_school_event(event_date, day_info):
                events.append(school_event)

        return events

    @classmethod
    def _build_school_event(cls, event_date: date, day_info: dict) -> CalendarEvent | None:
        """Return the timed school-day event, or None if there is nothing to show."""
        description_parts: list[str] = []
        for meal in ("breakfast", "lunch", "snack"):
            items = day_info.get(f"{meal}_items")
            if not items:
                continue
            pct = day_info.get(f"{meal}_percent")
            pct_str = f" ({pct}%)" if pct else ""
            icon = _MEAL_ICONS.get(meal, "🍴")
            description_parts.append(f"{icon} {meal.capitalize()}{pct_str}: {', '.join(items)}")

        checkin_time = cls._parse_checkin_time(day_info.get("checkin", "unknown"))
        if not checkin_time and not description_parts:
            return None

        start = datetime.combine(
            event_date, checkin_time or _SCHOOL_FALLBACK_START, tzinfo=dt_util.DEFAULT_TIME_ZONE
        )
        end = datetime.combine(event_date, _SCHOOL_END_TIME, tzinfo=dt_util.DEFAULT_TIME_ZONE)
        if end <= start:
            end = datetime.combine(
                event_date + timedelta(days=1), time.min, tzinfo=dt_util.DEFAULT_TIME_ZONE
            )

        return CalendarEvent(
            summary="School",
            start=start,
            end=end,
            description="<br>".join(description_parts) if description_parts else None,
        )

    @staticmethod
    def _parse_checkin_time(checkin: str) -> time | None:
        """Extract start time from a checkin string like '07:40 - by Alina'."""
        if not checkin or checkin == "unknown":
            return None
        match = _CHECKIN_TIME_RE.search(checkin)
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1), "%H:%M").time()
        except ValueError:
            return None

    @staticmethod
    def _build_nap_event(event_date: date, nap_text: str) -> CalendarEvent | None:
        """Create a timed nap event when start/end times are available."""
        if not nap_text or nap_text == "unknown":
            return None

        match = _NAP_TIME_RE.search(nap_text)
        if not match:
            return None

        try:
            start_time = datetime.strptime(match.group(1), "%H:%M").time()
            end_time = datetime.strptime(match.group(2), "%H:%M").time()
        except ValueError:
            return None

        nap_start = datetime.combine(event_date, start_time, tzinfo=dt_util.DEFAULT_TIME_ZONE)
        nap_end = datetime.combine(event_date, end_time, tzinfo=dt_util.DEFAULT_TIME_ZONE)

        if nap_end <= nap_start:
            return None

        return CalendarEvent(
            summary="Nap",
            start=nap_start,
            end=nap_end,
        )
