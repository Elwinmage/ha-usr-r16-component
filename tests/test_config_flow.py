"""Tests for the USR-R16 config flow."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.usr_r16.const import DEFAULT_PASSWORD, DEFAULT_PORT, DOMAIN

TEST_HOST = "192.168.1.100"
TEST_PORT = DEFAULT_PORT
TEST_PASSWORD = DEFAULT_PASSWORD


async def _start_flow(hass: HomeAssistant):
    """Initialize a config flow and return the init result."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


# All config flow tests need hass + custom integration loader
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("enable_custom_integrations"),
]


async def test_step_user_shows_form(hass: HomeAssistant) -> None:
    """Step 'user' should show the method-selection form."""
    result = await _start_flow(hass)
    assert result.get("type") == FlowResultType.FORM
    assert result.get("step_id") == "user"


async def test_step_user_manual_proceeds_to_manual(hass: HomeAssistant) -> None:
    """Choosing 'manual' should go directly to the manual entry step."""
    result = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"discovery_method": "manual"},
    )
    assert result.get("type") == FlowResultType.FORM
    assert result.get("step_id") == "manual"


async def test_manual_step_success(hass: HomeAssistant, mock_client) -> None:
    """Valid credentials should create a config entry."""
    with patch(
        "custom_components.usr_r16.config_flow.connect_client",
        new_callable=AsyncMock,
        return_value=mock_client,
    ), patch(
        "custom_components.usr_r16.async_setup_entry",
        return_value=True,
    ), patch(
        "custom_components.usr_r16.async_unload_entry",
        return_value=True,
    ):
        result = await _start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"discovery_method": "manual"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "host": TEST_HOST,
                "port": TEST_PORT,
                "password": TEST_PASSWORD,
            },
        )
        await hass.async_block_till_done()

    assert result.get("type") == FlowResultType.CREATE_ENTRY
    data = result.get("data") or {}
    assert data.get("host") == TEST_HOST
    assert data.get("port") == TEST_PORT


async def test_manual_step_cannot_connect(hass: HomeAssistant) -> None:
    """A connection failure should show an error and stay on the form."""
    from custom_components.usr_r16.errors import CannotConnect

    with patch(
        "custom_components.usr_r16.config_flow.connect_client",
        side_effect=CannotConnect,
    ):
        result = await _start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"discovery_method": "manual"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "host": TEST_HOST,
                "port": TEST_PORT,
                "password": TEST_PASSWORD,
            },
        )

    assert result.get("type") == FlowResultType.FORM
    errors = result.get("errors") or {}
    assert errors.get("base") == "cannot_connect"


async def test_manual_step_already_configured(hass: HomeAssistant, mock_client) -> None:
    """Submitting a duplicate device should show an error."""
    # Patch both the config flow validation AND the actual setup connection
    with (
        patch(
            "custom_components.usr_r16.config_flow.connect_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
        patch(
            "custom_components.usr_r16.create_usr_r16_client_connection",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
    ):
        result = await _start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"discovery_method": "manual"}
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "host": TEST_HOST,
                "port": TEST_PORT,
                "password": TEST_PASSWORD,
            },
        )

    with patch(
        "custom_components.usr_r16.config_flow.connect_client",
        return_value=mock_client,
    ):
        result = await _start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"discovery_method": "manual"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "host": TEST_HOST,
                "port": TEST_PORT,
                "password": TEST_PASSWORD,
            },
        )

    assert result.get("type") == FlowResultType.FORM
    errors = result.get("errors") or {}
    assert errors.get("base") == "already_configured"
