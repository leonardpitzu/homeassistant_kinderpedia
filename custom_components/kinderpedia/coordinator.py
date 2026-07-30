import asyncio
import logging
import re
from datetime import date as date_cls, timedelta

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow

from .api import KinderpediaAPI

_LOGGER = logging.getLogger(__name__)

_FOOD_TYPE_MAP = {"md": "breakfast", "mp": "lunch", "mp2": "lunch", "g": "snack"}
_NAP_PATTERN = re.compile(r"\s*(\d+)\s*h\s*and\s*(\d+)\s*min")
_NAP_PATTERN_MIN = re.compile(r"\s*(\d+)\s*min")
_WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _parse_timeline(json_data):
    """Parse raw timeline JSON into a weekday-keyed dict of day data."""
    parsed = {}

    try:
        days = {}
        if isinstance(json_data, dict):
            result = json_data.get("result")
            if isinstance(result, dict):
                dailytimeline = result.get("dailytimeline")
                if isinstance(dailytimeline, dict):
                    days = dailytimeline.get("days", {})

        for date_key, day_data in sorted(days.items()):
            day_data = day_data or {}

            # Derive weekday name from the date string
            try:
                parsed_date = date_cls.fromisoformat(date_key)
                weekday = _WEEKDAY_NAMES[parsed_date.weekday()]
            except (ValueError, TypeError):
                continue

            day_entry = {
                "name": weekday,
                "date": date_key,
                "checkin": "unknown",
                "nap": "unknown"
            }

            for item in day_data.get("data", []) or []:
                item_id = item.get("id", "")
                if item_id == "checkin":
                    day_entry["checkin"] = item.get("subtitle", "unknown")
                    # Detect absence from presence details
                    details = item.get("details")
                    if isinstance(details, dict):
                        presence = details.get("presence")
                        if isinstance(presence, dict):
                            absence = presence.get("absence")
                            if isinstance(absence, dict):
                                day_entry["absent"] = True
                                day_entry["absence_reason"] = absence.get("reason", "")
                                day_entry["absence_motivated"] = absence.get("motivated", False)
                                day_entry["absence_by"] = absence.get("by", "")
                elif item_id == "nap":
                    day_entry["nap"] = item.get("subtitle", "unknown")
                    if day_entry["nap"] != "unknown":
                        match = _NAP_PATTERN.search(day_entry["nap"])
                        if match:
                            hours = int(match.group(1))
                            minutes = int(match.group(2))
                            day_entry["nap_duration"] = hours * 60 + minutes
                        else:
                            match_min = _NAP_PATTERN_MIN.search(day_entry["nap"])
                            if match_min:
                                day_entry["nap_duration"] = int(match_min.group(1))
                            else:
                                day_entry["nap_duration"] = 0
                elif item_id.startswith("food_"):
                    details = item.get("details")
                    if isinstance(details, dict):
                        food = details.get("food") or {}
                        meals = food.get("meals", []) or []

                        lunch_percents = []

                        for meal in meals:
                            food_type = _FOOD_TYPE_MAP.get(meal.get("type", "unknown"), meal.get("type", "unknown"))
                            percent = meal.get("percent")
                            if meal.get("type") in ["mp", "mp2"] and isinstance(percent, (int, float)):
                                lunch_percents.append(percent)

                            menus = meal.get("menus", []) or []
                            if menus:
                                day_entry[f"{food_type}_items"] = [m.get("name", "unknown") for m in menus]
                                totals = meal.get("totals", {}) or {}
                                day_entry[f"{food_type}_kcal"] = totals.get("kcal", 0)
                                day_entry[f"{food_type}_weight"] = totals.get("weight", 0)

                            if meal.get("type") not in ["mp", "mp2"]:
                                day_entry[f"{food_type}_percent"] = percent if percent is not None else 0
                            else:
                                if lunch_percents:
                                    day_entry["lunch_percent"] = round(sum(lunch_percents) / len(lunch_percents), 1)
                                else:
                                    day_entry["lunch_percent"] = 0

            parsed[weekday] = day_entry

    except Exception as e:
        _LOGGER.error("Error parsing kinderpedia timeline: %s", e)

    return parsed


def _parse_newsfeed(json_data):
    """Parse raw newsfeed JSON into a list of text-friendly feed items."""
    items = []
    try:
        if not isinstance(json_data, dict):
            return items

        result = json_data.get("result")
        if not isinstance(result, dict):
            return items

        feed = result.get("feed")
        if not isinstance(feed, list):
            return items

        for entry in feed:
            item_type = entry.get("type", "unknown")

            # Skip gallery items – they add noise and no actionable info
            if item_type == "gallery":
                continue

            content = entry.get("content") or {}
            user = entry.get("user") or {}
            author = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()

            title = content.get("title") or ""
            description = content.get("description") or ""

            # Build a human-readable summary based on type
            summary = _build_summary(item_type, title, content, author)

            items.append({
                "id": entry.get("id"),
                "summary": summary,
                "title": title,
                "description": description[:500] if description else "",
                "date": entry.get("date_friendly", ""),
            })

    except Exception as e:
        _LOGGER.error("Error parsing kinderpedia newsfeed: %s", e)

    return items


def _build_summary(item_type, title, content, author):
    """Build a short human-readable summary for a feed item."""
    if item_type == "invoice":
        due = content.get("subtitle1", "")
        amount = content.get("subtitle2", "")
        parts = [title]
        if due:
            parts.append(due)
        if amount:
            parts.append(amount)
        return " — ".join(parts)

    # text / wall_post / other
    if title:
        return f"{author}: {title}"
    desc = content.get("description") or ""
    if desc:
        short = desc[:120].rstrip()
        if len(desc) > 120:
            short += "…"
        return f"{author}: {short}"
    return f"New post from {author}"


class KinderpediaDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(
        self,
        hass: HomeAssistant,
        api: KinderpediaAPI,
        history_stores: dict | None = None,
        initial_children: list | None = None,
    ) -> None:
        self.api = api
        # {child_key: KinderpediaHistoryStore}
        self._history_stores: dict = history_stores or {}
        # Children already fetched during setup; consumed on the first refresh
        # to avoid a duplicate fetch_children round-trip at startup.
        self._initial_children: list | None = initial_children
        super().__init__(
            hass,
            _LOGGER,
            name="Kinderpedia Coordinator",
            update_interval=timedelta(minutes=15),
        )

    async def _async_update_data(self):
        """Fetch all children and their timelines, return parsed data."""
        _LOGGER.debug("Fetching data from Kinderpedia API")
        try:
            # Reuse children fetched during setup on the very first refresh;
            # every subsequent refresh re-fetches to pick up new enrolments.
            if self._initial_children is not None:
                children = self._initial_children
                self._initial_children = None
            else:
                children = await self.api.fetch_children()

            result = {
                "children": {},
                "last_updated": utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            }

            # Fetch every child's timeline + newsfeed concurrently.
            child_results = await asyncio.gather(
                *(self._fetch_child(child) for child in children)
            )
            for key, entry in child_results:
                result["children"][key] = entry

            _LOGGER.debug("Kinderpedia data successfully fetched for %d children", len(children))
            return result
        except Exception as err:
            _LOGGER.error("Failed to fetch Kinderpedia data: %s", err)
            raise UpdateFailed(f"Error fetching data: {err}") from err

    async def _fetch_child(self, child):
        """Fetch and parse one child's timeline + newsfeed concurrently."""
        child_id = child["child_id"]
        kg_id = child["kindergarten_id"]
        key = f"{child_id}_{kg_id}"

        timeline_raw, newsfeed_raw = await asyncio.gather(
            self.api.fetch_timeline(child_id, kg_id),
            self.api.fetch_newsfeed(child_id, kg_id),
        )
        _LOGGER.debug("Kinderpedia: Raw timeline for %s: %s", key, timeline_raw)
        _LOGGER.debug("Kinderpedia: Raw newsfeed for %s: %s", key, newsfeed_raw)

        parsed_days = _parse_timeline(timeline_raw)
        parsed_feed = _parse_newsfeed(newsfeed_raw)

        entry = {
            "child": child,
            "days": parsed_days,
            "newsfeed": parsed_feed,
        }

        # Merge historical weeks underneath current-week data.  Historical days
        # use their date string as key; current-week days use weekday names.
        # All days end up keyed by date so the calendar can iterate uniformly.
        history_store = self._history_stores.get(key)
        if history_store is not None:
            historical_days = history_store.get_all_days()
            # Current week takes precedence – never overwrite live data
            current_dates = {
                d.get("date") for d in parsed_days.values() if d.get("date")
            }
            for date_iso, day_entry in historical_days.items():
                if date_iso not in current_dates:
                    # Use date_iso as key to avoid weekday name collisions
                    parsed_days[date_iso] = day_entry

        return key, entry
