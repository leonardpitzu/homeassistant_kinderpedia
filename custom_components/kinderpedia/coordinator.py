"""Data update coordinator and payload parsing for Kinderpedia."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date as date_cls, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.dt import utcnow

from .api import KinderpediaAPI, KinderpediaAuthError, KinderpediaConnectionError
from .const import UPDATE_INTERVAL_MINUTES, WEEKDAY_NAMES
from .history import KinderpediaHistoryStore

_LOGGER = logging.getLogger(__name__)

_FOOD_TYPE_MAP = {"md": "breakfast", "mp": "lunch", "mp2": "lunch", "g": "snack"}
_LUNCH_TYPES = ("mp", "mp2")
_NAP_PATTERN = re.compile(r"\s*(\d+)\s*h\s*and\s*(\d+)\s*min")
_NAP_PATTERN_MIN = re.compile(r"\s*(\d+)\s*min")


def _parse_timeline(json_data: Any) -> dict[str, dict]:
    """Parse raw timeline JSON into a dict of day data keyed by ISO date."""
    parsed: dict[str, dict] = {}

    days = {}
    if isinstance(json_data, dict):
        result = json_data.get("result")
        if isinstance(result, dict):
            dailytimeline = result.get("dailytimeline")
            if isinstance(dailytimeline, dict):
                days = dailytimeline.get("days", {}) or {}

    for date_key, day_data in sorted(days.items()):
        try:
            weekday = WEEKDAY_NAMES[date_cls.fromisoformat(date_key).weekday()]
        except (ValueError, TypeError):
            continue

        day_entry = {
            "name": weekday,
            "date": date_key,
            "checkin": "unknown",
            "nap": "unknown",
        }

        items = day_data.get("data") if isinstance(day_data, dict) else None
        for item in items or []:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id", "")
            if item_id == "checkin":
                _parse_checkin(item, day_entry)
            elif item_id == "nap":
                _parse_nap(item, day_entry)
            elif item_id.startswith("food_"):
                _parse_food(item, day_entry)

        parsed[date_key] = day_entry

    return parsed


def _parse_checkin(item: dict, day_entry: dict) -> None:
    """Fill check-in and absence details into *day_entry*."""
    day_entry["checkin"] = item.get("subtitle", "unknown")

    details = item.get("details")
    presence = details.get("presence") if isinstance(details, dict) else None
    absence = presence.get("absence") if isinstance(presence, dict) else None
    if not isinstance(absence, dict):
        return

    day_entry["absent"] = True
    day_entry["absence_reason"] = absence.get("reason", "")
    day_entry["absence_motivated"] = absence.get("motivated", False)
    day_entry["absence_by"] = absence.get("by", "")


def _parse_nap(item: dict, day_entry: dict) -> None:
    """Fill nap text and duration in minutes into *day_entry*."""
    nap = item.get("subtitle", "unknown")
    day_entry["nap"] = nap
    if not nap or nap == "unknown":
        return

    if match := _NAP_PATTERN.search(nap):
        day_entry["nap_duration"] = int(match.group(1)) * 60 + int(match.group(2))
    elif match := _NAP_PATTERN_MIN.search(nap):
        day_entry["nap_duration"] = int(match.group(1))
    else:
        day_entry["nap_duration"] = 0


def _parse_food(item: dict, day_entry: dict) -> None:
    """Fill meal menus, totals and eaten percentages into *day_entry*.

    Lunch can arrive as two courses (``mp`` + ``mp2``); those are averaged.
    """
    details = item.get("details")
    if not isinstance(details, dict):
        return

    meals = (details.get("food") or {}).get("meals", []) or []
    lunch_percents: list[float] = []

    for meal in meals:
        if not isinstance(meal, dict):
            continue
        raw_type = meal.get("type", "unknown")
        food_type = _FOOD_TYPE_MAP.get(raw_type, raw_type)
        percent = meal.get("percent")

        if menus := (meal.get("menus") or []):
            day_entry[f"{food_type}_items"] = [m.get("name", "unknown") for m in menus if isinstance(m, dict)]
            totals = meal.get("totals") or {}
            day_entry[f"{food_type}_kcal"] = totals.get("kcal", 0)
            day_entry[f"{food_type}_weight"] = totals.get("weight", 0)

        if raw_type in _LUNCH_TYPES:
            if isinstance(percent, (int, float)):
                lunch_percents.append(percent)
            day_entry["lunch_percent"] = (
                round(sum(lunch_percents) / len(lunch_percents), 1) if lunch_percents else 0
            )
        else:
            day_entry[f"{food_type}_percent"] = percent if percent is not None else 0


def _parse_newsfeed(json_data: Any) -> list[dict]:
    """Parse raw newsfeed JSON into a list of text-friendly feed items."""
    items: list[dict] = []

    result = json_data.get("result") if isinstance(json_data, dict) else None
    feed = result.get("feed") if isinstance(result, dict) else None
    if not isinstance(feed, list):
        return items

    for entry in feed:
        if not isinstance(entry, dict):
            continue
        item_type = entry.get("type", "unknown")

        # Skip gallery items – they add noise and no actionable info
        if item_type == "gallery":
            continue

        content = entry.get("content") or {}
        user = entry.get("user") or {}
        author = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()

        title = content.get("title") or ""
        description = content.get("description") or ""

        items.append({
            "id": entry.get("id"),
            "summary": _build_summary(item_type, title, content, author),
            "title": title,
            "description": description[:500] if description else "",
            "date": entry.get("date_friendly", ""),
        })

    return items


def _build_summary(item_type: str, title: str, content: dict, author: str) -> str:
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


class KinderpediaDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the Kinderpedia API for every child on the account."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: KinderpediaAPI,
        initial_children: list | None = None,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        self.api = api
        # Children already fetched during setup; consumed on the first refresh
        # to avoid a duplicate fetch_children round-trip at startup.
        self._initial_children: list | None = initial_children
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Kinderpedia Coordinator",
            update_interval=timedelta(minutes=UPDATE_INTERVAL_MINUTES),
        )

    async def _async_update_data(self) -> dict[str, Any]:
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

            child_results = await asyncio.gather(
                *(self._fetch_child(child) for child in children)
            )
        except KinderpediaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except KinderpediaConnectionError as err:
            raise UpdateFailed(f"Error fetching data: {err}") from err
        except Exception as err:
            # Anything else is a bug in the parsing/shape assumptions — keep the traceback.
            _LOGGER.exception("Unexpected error fetching Kinderpedia data")
            raise UpdateFailed(f"Error fetching data: {err}") from err

        _LOGGER.debug("Kinderpedia data successfully fetched for %d children", len(children))
        return {
            "children": dict(child_results),
            "last_updated": utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }

    async def _fetch_child(self, child: dict) -> tuple[str, dict]:
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

        return key, {
            "child": child,
            "days": _parse_timeline(timeline_raw),
            "newsfeed": _parse_newsfeed(newsfeed_raw),
        }


@dataclass
class KinderpediaRuntimeData:
    """Everything a config entry owns at runtime."""

    api: KinderpediaAPI
    coordinator: KinderpediaDataUpdateCoordinator
    history_stores: dict[str, KinderpediaHistoryStore]


type KinderpediaConfigEntry = ConfigEntry[KinderpediaRuntimeData]
