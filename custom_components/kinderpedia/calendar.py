"""Calendar platform for Kinderpedia."""

import logging
import re
from datetime import date, datetime, time, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_NAP_TIME_RE = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
_CHECKIN_TIME_RE = re.compile(r"^(\d{1,2}:\d{2})")

_SCHOOL_END_TIME = time(18, 0)
_SCHOOL_FALLBACK_START = time(8, 0)

_MEAL_ICONS = {"breakfast": "🥣", "lunch": "🍽️", "snack": "🍪"}


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up Kinderpedia calendar entities."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = entry_data["coordinator"]

    tracked_keys: set[str] = set()

    @callback
    def _discover_new_children():
        data = coordinator.data or {}
        children_data = data.get("children", {})
        new_entities = []

        for key, child_data in children_data.items():
            if key in tracked_keys:
                continue
            tracked_keys.add(key)

            child = child_data["child"]
            child_id = child["child_id"]
            kg_id = child["kindergarten_id"]
            device_name = f"{child['first_name']} {child['last_name']}"
            first_name = child["first_name"]

            new_entities.append(
                KinderpediaCalendar(
                    coordinator, child_id, kg_id, device_name, first_name
                )
            )

        if new_entities:
            async_add_entities(new_entities)

    _discover_new_children()
    config_entry.async_on_unload(
        coordinator.async_add_listener(_discover_new_children)
    )


class KinderpediaCalendar(CoordinatorEntity, CalendarEntity):
    """Calendar showing daily school activities for a child."""

    def __init__(self, coordinator, child_id, kg_id, device_name, first_name):
        """Initialise the calendar entity."""
        super().__init__(coordinator)
        self._key = f"{child_id}_{kg_id}"
        self._attr_unique_id = f"{DOMAIN}_calendar_{child_id}_{kg_id}"
        self._attr_name = f"{first_name.lower()} school"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{child_id}_{kg_id}")},
            "name": device_name,
            "manufacturer": "Kinderpedia",
        }
        # Event list is rebuilt only when the coordinator data changes.
        self._events_cache: list[CalendarEvent] | None = None
        self._events_token: str | None = None
        # Latest-day lookup also depends on the current date.
        self._latest_day_cache: dict | None = None
        self._latest_day_token: tuple[str | None, str] | None = None

    # ------------------------------------------------------------------
    # CalendarEntity interface
    # ------------------------------------------------------------------

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current/next event (today)."""
        events = self._build_events()
        now = dt_util.now()
        today = now.date()

        for ev in events:
            ev_start = ev.start if isinstance(ev.start, datetime) else datetime.combine(ev.start, time.min, tzinfo=dt_util.DEFAULT_TIME_ZONE)
            ev_end = ev.end if isinstance(ev.end, datetime) else datetime.combine(ev.end, time.min, tzinfo=dt_util.DEFAULT_TIME_ZONE)
            if ev_start.date() == today and ev_start <= now <= ev_end:
                return ev
            if ev_start.date() == today and now < ev_start:
                return ev

        # Fall back to any event whose date is today
        for ev in events:
            ev_date = ev.start.date() if isinstance(ev.start, datetime) else ev.start
            if ev_date == today:
                return ev
        return None

    @property
    def extra_state_attributes(self):
        """Expose the latest school-day data as entity attributes."""
        day_info = self._get_latest_day_info()
        if not day_info:
            return {}
        data = self.coordinator.data or {}
        attrs = {
            "date": day_info.get("date"),
            "last_updated": data.get("last_updated"),
        }
        for key, val in day_info.items():
            if key not in ("name", "date"):
                attrs[key] = val
        return attrs

    async def async_get_events(
        self,
        hass,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return events within the requested range."""
        events = self._build_events()
        start_d = start_date.date() if isinstance(start_date, datetime) else start_date
        end_d = end_date.date() if isinstance(end_date, datetime) else end_date

        def _event_date(dt_or_d: date | datetime) -> date:
            return dt_or_d.date() if isinstance(dt_or_d, datetime) else dt_or_d

        return [
            ev
            for ev in events
            if _event_date(ev.start) < end_d and _event_date(ev.end) >= start_d
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_latest_day_info(self) -> dict | None:
        """Return the most recent day-info with real activity.

        Prefers today if available, otherwise falls back to the latest
        past day that has a valid checkin or meal data.  Cached per
        coordinator update and per calendar day.
        """
        data = self.coordinator.data or {}
        today_str = date.today().isoformat()
        token = (data.get("last_updated"), today_str)
        if self._latest_day_token == token:
            return self._latest_day_cache

        child_data = data.get("children", {}).get(self._key, {})
        days = child_data.get("days", {})

        # Prefer today
        result: dict | None = None
        for day_info in days.values():
            if day_info.get("date") == today_str and self._has_activity(day_info):
                result = day_info
                break

        # Fall back to the most recent day with activity
        if result is None:
            best_date = ""
            for day_info in days.values():
                d = day_info.get("date", "")
                if d and d != "unknown" and d <= today_str and self._has_activity(day_info) and d > best_date:
                    best_date = d
                    result = day_info

        self._latest_day_token = token
        self._latest_day_cache = result
        return result

    @staticmethod
    def _has_activity(day_info: dict) -> bool:
        """Return True if a day has meaningful data (checkin or meals)."""
        checkin = day_info.get("checkin", "unknown")
        if checkin and checkin != "unknown":
            return True
        return any(day_info.get(f"{meal}_items") for meal in ("breakfast", "lunch", "snack"))

    def _build_events(self) -> list[CalendarEvent]:
        """Build calendar events from coordinator day data.

        Cached and only rebuilt when the coordinator data changes.
        """
        data = self.coordinator.data or {}
        token = data.get("last_updated")
        if self._events_token == token and self._events_cache is not None:
            return self._events_cache

        child_data = data.get("children", {}).get(self._key, {})
        days = child_data.get("days", {})

        events: list[CalendarEvent] = []
        for _weekday, day_info in days.items():
            date_str = day_info.get("date")
            if not date_str or date_str == "unknown":
                continue

            try:
                event_date = date.fromisoformat(date_str)
            except (ValueError, TypeError):
                continue

            # Skip absent days — no school or nap events
            if day_info.get("absent"):
                continue

            description_parts: list[str] = []

            # Parse check-in time for timed school event
            checkin = day_info.get("checkin", "unknown")
            checkin_time = self._parse_checkin_time(checkin)

            # Nap → separate timed event
            nap = day_info.get("nap", "unknown")
            if nap and nap != "unknown":
                nap_event = self._build_nap_event(event_date, nap)
                if nap_event:
                    events.append(nap_event)

            # Meals → description
            for meal in ("breakfast", "lunch", "snack"):
                items = day_info.get(f"{meal}_items", [])
                pct = day_info.get(f"{meal}_percent")
                if items:
                    icon = _MEAL_ICONS.get(meal, "🍴")
                    food_str = ", ".join(items)
                    pct_str = f" ({pct}%)" if pct else ""
                    description_parts.append(
                        f"{icon} {meal.capitalize()}{pct_str}: {food_str}"
                    )

            # Need at least a checkin or meal data to create the school event
            if not checkin_time and not description_parts:
                continue

            # Build summary
            summary = "School"

            description = "<br>".join(description_parts) if description_parts else None

            # Timed event: start at check-in (or 08:00 fallback), end at 18:00
            # If we cannot determine a valid start, fall back to midnight end.
            start_t = checkin_time or _SCHOOL_FALLBACK_START
            try:
                ev_start = datetime.combine(
                    event_date, start_t, tzinfo=dt_util.DEFAULT_TIME_ZONE
                )
                ev_end = datetime.combine(
                    event_date, _SCHOOL_END_TIME, tzinfo=dt_util.DEFAULT_TIME_ZONE
                )
                if ev_end <= ev_start:
                    # Safety: end must be after start; fall back to midnight
                    ev_end = datetime.combine(
                        event_date + timedelta(days=1),
                        time.min,
                        tzinfo=dt_util.DEFAULT_TIME_ZONE,
                    )
            except (ValueError, TypeError):
                ev_end = datetime.combine(
                    event_date + timedelta(days=1),
                    time.min,
                    tzinfo=dt_util.DEFAULT_TIME_ZONE,
                )
                ev_start = datetime.combine(
                    event_date, _SCHOOL_FALLBACK_START, tzinfo=dt_util.DEFAULT_TIME_ZONE
                )

            events.append(
                CalendarEvent(
                    summary=summary,
                    start=ev_start,
                    end=ev_end,
                    description=description,
                )
            )

        self._events_token = token
        self._events_cache = events
        return events

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
    def _build_nap_event(
        event_date: date, nap_text: str
    ) -> CalendarEvent | None:
        """Create a timed nap event when start/end times are available."""
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
