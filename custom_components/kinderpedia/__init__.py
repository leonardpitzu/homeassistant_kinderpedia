"""The Kinderpedia integration."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .api import KinderpediaAPI, KinderpediaAuthError, KinderpediaConnectionError
from .const import DOMAIN, PLATFORMS
from .coordinator import (
    KinderpediaConfigEntry,
    KinderpediaDataUpdateCoordinator,
    KinderpediaRuntimeData,
    _parse_timeline,
)
from .history import KinderpediaHistoryStore

_LOGGER = logging.getLogger(__name__)

SERVICE_BACKFILL = "backfill_history"

_ARCHIVE_HOUR = 3
_MONDAY = 0


async def async_setup_entry(hass: HomeAssistant, entry: KinderpediaConfigEntry) -> bool:
    """Set up Kinderpedia from a config entry."""
    api = KinderpediaAPI(hass, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])

    try:
        await api.login()
        children = await api.fetch_children()
    except KinderpediaConnectionError as err:
        raise ConfigEntryNotReady(f"Cannot connect to Kinderpedia: {err}") from err
    except KinderpediaAuthError as err:
        raise ConfigEntryAuthFailed(f"Kinderpedia authentication failed: {err}") from err

    history_stores: dict[str, KinderpediaHistoryStore] = {}
    for child in children:
        await _async_ensure_store(hass, history_stores, _child_key(child))

    coordinator = KinderpediaDataUpdateCoordinator(
        hass, api, initial_children=children, config_entry=entry
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = KinderpediaRuntimeData(
        api=api, coordinator=coordinator, history_stores=history_stores
    )

    async def _weekly_archive(*_args) -> None:
        """Archive last week's data every Monday morning."""
        if dt_util.now().weekday() != _MONDAY:
            return
        for child in _entry_children(entry):
            key = _child_key(child)
            store = await _async_ensure_store(hass, history_stores, key)
            await store.async_archive_last_week(
                api, child["child_id"], child["kindergarten_id"], _parse_timeline
            )

    entry.async_on_unload(
        async_track_time_change(hass, _weekly_archive, hour=_ARCHIVE_HOUR, minute=0, second=0)
    )

    async def _initial_backfill() -> None:
        """Fetch all past weeks once, in the background."""
        for child in _entry_children(entry):
            key = _child_key(child)
            store = history_stores.get(key)
            if store is None or store.weeks:
                _LOGGER.debug(
                    "Skipping backfill for %s (store has %d weeks)",
                    key, len(store.weeks) if store else 0,
                )
                continue
            _LOGGER.debug("Starting initial history backfill for %s", key)
            if await store.async_backfill(
                api, child["child_id"], child["kindergarten_id"], _parse_timeline
            ):
                await coordinator.async_request_refresh()

    entry.async_create_background_task(hass, _initial_backfill(), "kinderpedia_backfill")

    _async_register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KinderpediaConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and len(hass.config_entries.async_loaded_entries(DOMAIN)) <= 1:
        hass.services.async_remove(DOMAIN, SERVICE_BACKFILL)
    return unload_ok


def _child_key(child: dict) -> str:
    """Return the per-child storage/entity key."""
    return f"{child['child_id']}_{child['kindergarten_id']}"


def _entry_children(entry: KinderpediaConfigEntry) -> list[dict]:
    """Return the children currently known to the coordinator."""
    data = entry.runtime_data.coordinator.data or {}
    return [child_data["child"] for child_data in data.get("children", {}).values()]


async def _async_ensure_store(
    hass: HomeAssistant,
    stores: dict[str, KinderpediaHistoryStore],
    key: str,
) -> KinderpediaHistoryStore:
    """Return the history store for *key*, creating and loading it if needed."""
    if (store := stores.get(key)) is None:
        store = KinderpediaHistoryStore(hass, key)
        await store.async_load()
        stores[key] = store
    return store


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services once."""
    if hass.services.has_service(DOMAIN, SERVICE_BACKFILL):
        return

    async def _handle_backfill(call: ServiceCall) -> None:
        """Re-run the historical backfill for every configured account."""
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            runtime: KinderpediaRuntimeData = entry.runtime_data
            for child in _entry_children(entry):
                store = await _async_ensure_store(hass, runtime.history_stores, _child_key(child))
                await store.async_backfill(
                    runtime.api, child["child_id"], child["kindergarten_id"], _parse_timeline
                )
            await runtime.coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_BACKFILL, _handle_backfill)
