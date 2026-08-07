"""Tests for Kinderpedia integration setup and teardown."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState

from custom_components.kinderpedia.const import DOMAIN
from custom_components.kinderpedia.api import KinderpediaAuthError, KinderpediaConnectionError

from tests.conftest import MOCK_CHILD, MOCK_TIMELINE_RAW, MOCK_NEWSFEED_RAW


def _mock_history_store():
    """Create a mock KinderpediaHistoryStore."""
    store = MagicMock()
    store.async_load = AsyncMock()
    store.weeks = {}
    store.async_backfill = AsyncMock(return_value=0)
    return store


async def test_setup_entry(hass: HomeAssistant, mock_config_entry):
    """Test successful setup of a config entry."""
    with (
        patch("custom_components.kinderpedia.KinderpediaAPI") as mock_api_cls,
        patch("custom_components.kinderpedia.KinderpediaDataUpdateCoordinator") as mock_coord_cls,
        patch("custom_components.kinderpedia.KinderpediaHistoryStore") as mock_store_cls,
    ):
        api = mock_api_cls.return_value
        api.login = AsyncMock()
        api.fetch_children = AsyncMock(return_value=[MOCK_CHILD])
        api.fetch_timeline = AsyncMock(return_value=MOCK_TIMELINE_RAW)
        api.fetch_newsfeed = AsyncMock(return_value=MOCK_NEWSFEED_RAW)

        mock_store_cls.return_value = _mock_history_store()

        coordinator = mock_coord_cls.return_value
        coordinator.async_config_entry_first_refresh = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.LOADED
    assert mock_config_entry.runtime_data.coordinator is coordinator
    assert "111_222" in mock_config_entry.runtime_data.history_stores
    assert hass.services.has_service(DOMAIN, "backfill_history")


async def test_setup_entry_auth_failure(hass: HomeAssistant, mock_config_entry):
    """Test setup fails gracefully on auth error."""
    with patch("custom_components.kinderpedia.KinderpediaAPI") as mock_api_cls:
        api = mock_api_cls.return_value
        api.login = AsyncMock(side_effect=KinderpediaAuthError("bad creds"))

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.SETUP_ERROR


async def test_setup_entry_connection_failure(hass: HomeAssistant, mock_config_entry):
    """Test setup raises ConfigEntryNotReady on connection error."""
    with patch("custom_components.kinderpedia.KinderpediaAPI") as mock_api_cls:
        api = mock_api_cls.return_value
        api.login = AsyncMock(side_effect=KinderpediaConnectionError("timeout"))

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.SETUP_RETRY


async def test_unload_entry(hass: HomeAssistant, mock_config_entry):
    """Test unload of a config entry."""
    with (
        patch("custom_components.kinderpedia.KinderpediaAPI") as mock_api_cls,
        patch("custom_components.kinderpedia.KinderpediaDataUpdateCoordinator") as mock_coord_cls,
        patch("custom_components.kinderpedia.KinderpediaHistoryStore") as mock_store_cls,
    ):
        api = mock_api_cls.return_value
        api.login = AsyncMock()
        api.fetch_children = AsyncMock(return_value=[MOCK_CHILD])
        api.fetch_timeline = AsyncMock(return_value=MOCK_TIMELINE_RAW)
        api.fetch_newsfeed = AsyncMock(return_value=MOCK_NEWSFEED_RAW)

        mock_store_cls.return_value = _mock_history_store()

        coordinator = mock_coord_cls.return_value
        coordinator.async_config_entry_first_refresh = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED

    result = await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert result is True
    assert not hass.services.has_service(DOMAIN, "backfill_history")
