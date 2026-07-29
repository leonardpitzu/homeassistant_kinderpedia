import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_change

from .const import DOMAIN, PLATFORMS
from .api import KinderpediaAPI, KinderpediaAuthError, KinderpediaConnectionError
from .coordinator import KinderpediaDataUpdateCoordinator, _parse_timeline
from .history import KinderpediaHistoryStore

_LOGGER = logging.getLogger(__name__)

SERVICE_BACKFILL = "backfill_history"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    hass.data.setdefault(DOMAIN, {})

    email = entry.data["email"]
    password = entry.data["password"]

    api = KinderpediaAPI(hass, email, password)

    try:
        await api.login()
    except KinderpediaConnectionError as err:
        raise ConfigEntryNotReady(f"Cannot connect to Kinderpedia: {err}") from err
    except KinderpediaAuthError as err:
        _LOGGER.error("Kinderpedia authentication failed: %s", err)
        return False

    # Discover children so we can create per-child history stores
    children = await api.fetch_children()

    history_stores: dict[str, KinderpediaHistoryStore] = {}
    for child in children:
        key = f"{child['child_id']}_{child['kindergarten_id']}"
        store = KinderpediaHistoryStore(hass, key)
        await store.async_load()
        history_stores[key] = store

    coordinator = KinderpediaDataUpdateCoordinator(
        hass, api, history_stores, initial_children=children
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "history_stores": history_stores,
        "api": api,
        "children": children,
        "unsub_weekly": [],
    }

    # ------------------------------------------------------------------
    # Schedule weekly archive: every Monday at 03:00
    # ------------------------------------------------------------------
    async def _weekly_archive(*_args):
        """Archive last week's data for every child."""
        for child in children:
            key = f"{child['child_id']}_{child['kindergarten_id']}"
            store = history_stores.get(key)
            if store:
                await store.async_archive_last_week(
                    api, child["child_id"], child["kindergarten_id"], _parse_timeline
                )

    unsub = async_track_time_change(hass, _weekly_archive, hour=3, minute=0, second=0)
    hass.data[DOMAIN][entry.entry_id]["unsub_weekly"].append(unsub)

    # ------------------------------------------------------------------
    # Initial backfill (if stores are empty) — runs in background
    # ------------------------------------------------------------------
    async def _initial_backfill():
        try:
            for child in children:
                key = f"{child['child_id']}_{child['kindergarten_id']}"
                store = history_stores.get(key)
                if store and not store.weeks:
                    _LOGGER.debug("Starting initial history backfill for %s", key)
                    count = await store.async_backfill(
                        api, child["child_id"], child["kindergarten_id"], _parse_timeline
                    )
                    if count > 0:
                        await coordinator.async_request_refresh()
                else:
                    _LOGGER.debug(
                        "Skipping backfill for %s (store has %d weeks)",
                        key, len(store.weeks) if store else 0,
                    )
        except Exception:
            _LOGGER.exception("Unexpected error during initial history backfill")

    # Fire-and-forget: runs in background without blocking setup
    entry.async_create_background_task(hass, _initial_backfill(), "kinderpedia_backfill")

    # ------------------------------------------------------------------
    # Service: kinderpedia.backfill_history (manual trigger)
    # ------------------------------------------------------------------
    async def _handle_backfill_service(call: ServiceCall):
        """Handle the backfill_history service call."""
        for _eid, edata in hass.data[DOMAIN].items():
            stores = edata.get("history_stores", {})
            entry_api = edata.get("api")
            entry_children = edata.get("children", [])
            entry_coordinator = edata.get("coordinator")
            if not entry_api:
                continue
            for child in entry_children:
                key = f"{child['child_id']}_{child['kindergarten_id']}"
                store = stores.get(key)
                if store:
                    await store.async_backfill(
                        entry_api,
                        child["child_id"],
                        child["kindergarten_id"],
                        _parse_timeline,
                    )
            if entry_coordinator:
                await entry_coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_BACKFILL):
        hass.services.async_register(DOMAIN, SERVICE_BACKFILL, _handle_backfill_service)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, {})
        for unsub in entry_data.get("unsub_weekly", []):
            unsub()
    return unload_ok
