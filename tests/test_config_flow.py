"""Tests for the Kinderpedia config flow."""

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType

from custom_components.kinderpedia.const import DOMAIN
from custom_components.kinderpedia.api import KinderpediaAuthError, KinderpediaConnectionError

from tests.conftest import MOCK_EMAIL, MOCK_PASSWORD, MOCK_CHILD


async def test_user_form_shown(hass: HomeAssistant):
    """Test that the user form is shown on first step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_successful_config_flow(hass: HomeAssistant):
    """Test a successful config flow creates an entry."""
    with patch(
        "custom_components.kinderpedia.config_flow.KinderpediaAPI"
    ) as mock_api_cls:
        mock_api = mock_api_cls.return_value
        mock_api.fetch_children = AsyncMock(return_value=[MOCK_CHILD])

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_EMAIL: MOCK_EMAIL, CONF_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == MOCK_EMAIL
    assert result["data"][CONF_EMAIL] == MOCK_EMAIL
    assert result["data"][CONF_PASSWORD] == MOCK_PASSWORD


async def test_auth_error(hass: HomeAssistant):
    """Test config flow handles authentication errors."""
    with patch(
        "custom_components.kinderpedia.config_flow.KinderpediaAPI"
    ) as mock_api_cls:
        mock_api = mock_api_cls.return_value
        mock_api.fetch_children = AsyncMock(side_effect=KinderpediaAuthError("bad creds"))

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_EMAIL: MOCK_EMAIL, CONF_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_connection_error(hass: HomeAssistant):
    """Test config flow handles connection errors."""
    with patch(
        "custom_components.kinderpedia.config_flow.KinderpediaAPI"
    ) as mock_api_cls:
        mock_api = mock_api_cls.return_value
        mock_api.fetch_children = AsyncMock(
            side_effect=KinderpediaConnectionError("timeout")
        )

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_EMAIL: MOCK_EMAIL, CONF_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_generic_exception(hass: HomeAssistant):
    """Test config flow handles unexpected exceptions."""
    with patch(
        "custom_components.kinderpedia.config_flow.KinderpediaAPI"
    ) as mock_api_cls:
        mock_api = mock_api_cls.return_value
        mock_api.fetch_children = AsyncMock(side_effect=RuntimeError("boom"))

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_EMAIL: MOCK_EMAIL, CONF_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_reauth_flow_updates_password(hass: HomeAssistant, mock_config_entry):
    """Reauth replaces the stored password without creating a new entry."""
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch(
        "custom_components.kinderpedia.config_flow.KinderpediaAPI"
    ) as mock_api_cls:
        mock_api_cls.return_value.fetch_children = AsyncMock(return_value=[MOCK_CHILD])

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new-password"}
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "new-password"


async def test_reauth_flow_rejects_bad_password(hass: HomeAssistant, mock_config_entry):
    """Reauth keeps asking while the new password is refused."""
    result = await mock_config_entry.start_reauth_flow(hass)

    with patch(
        "custom_components.kinderpedia.config_flow.KinderpediaAPI"
    ) as mock_api_cls:
        mock_api_cls.return_value.fetch_children = AsyncMock(
            side_effect=KinderpediaAuthError("nope")
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "still-wrong"}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert mock_config_entry.data[CONF_PASSWORD] == MOCK_PASSWORD


async def test_no_children_found(hass: HomeAssistant):
    """Test config flow handles no children being discovered."""
    with patch(
        "custom_components.kinderpedia.config_flow.KinderpediaAPI"
    ) as mock_api_cls:
        mock_api = mock_api_cls.return_value
        mock_api.fetch_children = AsyncMock(return_value=[])

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_EMAIL: MOCK_EMAIL, CONF_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "no_children_found"}


async def test_duplicate_entry(hass: HomeAssistant, mock_config_entry):
    """Test config flow aborts when account is already configured."""
    with patch(
        "custom_components.kinderpedia.config_flow.KinderpediaAPI"
    ) as mock_api_cls:
        mock_api = mock_api_cls.return_value
        mock_api.fetch_children = AsyncMock(return_value=[MOCK_CHILD])

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_EMAIL: MOCK_EMAIL, CONF_PASSWORD: MOCK_PASSWORD},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
