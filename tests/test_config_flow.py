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
    with (
        patch(
            "custom_components.usr_r16.config_flow.connect_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
        patch(
            "custom_components.usr_r16.async_setup_entry",
            return_value=True,
        ),
        patch(
            "custom_components.usr_r16.async_unload_entry",
            return_value=True,
        ),
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
            "custom_components.usr_r16.protocol.USR16Client.setup",
            new_callable=AsyncMock,
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


# ---------------------------------------------------------------------------
# Options flow (reconfigure)
# ---------------------------------------------------------------------------


async def test_options_flow_shows_form(hass: HomeAssistant, mock_client) -> None:
    """Options flow init step should show the reconfigure form."""
    # First create an entry
    with (
        patch(
            "custom_components.usr_r16.config_flow.connect_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
        patch(
            "custom_components.usr_r16.async_setup_entry",
            return_value=True,
        ),
        patch(
            "custom_components.usr_r16.async_unload_entry",
            return_value=True,
        ),
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

    # Get the created entry
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    entry = entries[0]

    # Start options flow
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result.get("type") == FlowResultType.FORM
    assert result.get("step_id") == "init"


async def test_options_flow_success(hass: HomeAssistant, mock_client) -> None:
    """Options flow should save new connection settings."""
    with (
        patch(
            "custom_components.usr_r16.config_flow.connect_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
        patch(
            "custom_components.usr_r16.async_setup_entry",
            return_value=True,
        ),
        patch(
            "custom_components.usr_r16.async_unload_entry",
            return_value=True,
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
        await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]

    new_password = "newpw"
    with patch(
        "custom_components.usr_r16.config_flow.connect_client",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"host": TEST_HOST, "port": TEST_PORT, "password": new_password},
        )

    assert result.get("type") == FlowResultType.CREATE_ENTRY
    assert result.get("data", {}).get("password") == new_password


async def test_options_flow_cannot_connect(hass: HomeAssistant, mock_client) -> None:
    """Options flow connection failure should show error."""
    from custom_components.usr_r16.errors import CannotConnect

    with (
        patch(
            "custom_components.usr_r16.config_flow.connect_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
        patch(
            "custom_components.usr_r16.async_setup_entry",
            return_value=True,
        ),
        patch(
            "custom_components.usr_r16.async_unload_entry",
            return_value=True,
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
        await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]

    with patch(
        "custom_components.usr_r16.config_flow.connect_client",
        side_effect=CannotConnect,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"host": TEST_HOST, "port": TEST_PORT, "password": "bad"},
        )

    assert result.get("type") == FlowResultType.FORM
    errors = result.get("errors") or {}
    assert errors.get("base") == "cannot_connect"


async def test_options_flow_already_configured(
    hass: HomeAssistant, mock_client
) -> None:
    """Options flow should error if new host conflicts with another entry."""
    OTHER_HOST = "192.168.1.200"

    # Create first entry
    with (
        patch(
            "custom_components.usr_r16.config_flow.connect_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
        patch(
            "custom_components.usr_r16.async_setup_entry",
            return_value=True,
        ),
        patch(
            "custom_components.usr_r16.async_unload_entry",
            return_value=True,
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
        await hass.async_block_till_done()

    # Create second entry on a different host
    with (
        patch(
            "custom_components.usr_r16.config_flow.connect_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
        patch(
            "custom_components.usr_r16.async_setup_entry",
            return_value=True,
        ),
        patch(
            "custom_components.usr_r16.async_unload_entry",
            return_value=True,
        ),
    ):
        result = await _start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"discovery_method": "manual"}
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "host": OTHER_HOST,
                "port": TEST_PORT,
                "password": TEST_PASSWORD,
            },
        )
        await hass.async_block_till_done()

    # Try to reconfigure second entry to conflict with first
    entries = hass.config_entries.async_entries(DOMAIN)
    second_entry = next(e for e in entries if e.data["host"] == OTHER_HOST)

    with patch(
        "custom_components.usr_r16.config_flow.connect_client",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):
        result = await hass.config_entries.options.async_init(second_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
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


async def test_manual_step_invalid_auth(hass: HomeAssistant, mock_client) -> None:
    """Invalid password should show invalid_auth error."""
    from custom_components.usr_r16.protocol import InvalidAuth

    with patch(
        "custom_components.usr_r16.config_flow.connect_client",
        side_effect=InvalidAuth,
    ):
        result = await _start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"discovery_method": "manual"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": TEST_HOST, "port": TEST_PORT, "password": "wrong"},
        )

    assert result.get("type") == FlowResultType.FORM
    assert (result.get("errors") or {}).get("base") == "invalid_auth"


async def test_manual_step_password_too_long(hass: HomeAssistant, mock_client) -> None:
    """Password > 5 chars should show password_too_long error."""
    with patch(
        "custom_components.usr_r16.config_flow.connect_client",
        new_callable=AsyncMock,
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
                "password": "toolongpassword",
            },
        )

    assert result.get("type") == FlowResultType.FORM
    errors = result.get("errors") or {}
    assert "password" in errors or errors.get("password") == "password_too_long"


async def test_options_flow_invalid_auth(hass: HomeAssistant, mock_client) -> None:
    """Options flow invalid_auth should show error."""
    from custom_components.usr_r16.protocol import InvalidAuth

    with (
        patch(
            "custom_components.usr_r16.config_flow.connect_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
        patch(
            "custom_components.usr_r16.async_setup_entry",
            return_value=True,
        ),
        patch(
            "custom_components.usr_r16.async_unload_entry",
            return_value=True,
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
        await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]

    with patch(
        "custom_components.usr_r16.config_flow.connect_client",
        side_effect=InvalidAuth,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"host": TEST_HOST, "port": TEST_PORT, "password": "wrong"},
        )

    assert result.get("type") == FlowResultType.FORM
    assert (result.get("errors") or {}).get("base") == "invalid_auth"


async def test_options_flow_password_too_long(hass: HomeAssistant, mock_client) -> None:
    """Options flow password > 5 chars should show error."""
    with (
        patch(
            "custom_components.usr_r16.config_flow.connect_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
        patch(
            "custom_components.usr_r16.async_setup_entry",
            return_value=True,
        ),
        patch(
            "custom_components.usr_r16.async_unload_entry",
            return_value=True,
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
        await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]

    with patch(
        "custom_components.usr_r16.config_flow.connect_client",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "host": TEST_HOST,
                "port": TEST_PORT,
                "password": "toolongpassword",
            },
        )

    assert result.get("type") == FlowResultType.FORM
    errors = result.get("errors") or {}
    assert "password" in str(errors)


async def test_connect_client_protocol_cannot_connect(hass: HomeAssistant) -> None:
    """ProtocolCannotConnect should be re-raised as CannotConnect (lines 198-201)."""
    from custom_components.usr_r16.config_flow import connect_client
    from custom_components.usr_r16.errors import CannotConnect
    from custom_components.usr_r16.protocol import (
        CannotConnect as ProtocolCannotConnect,
    )

    with (
        patch(
            "custom_components.usr_r16.protocol.USR16Client.setup",
            side_effect=ProtocolCannotConnect("tcp error"),
        ),
        pytest.raises(CannotConnect),
    ):
        await connect_client(
            hass,
            {"host": TEST_HOST, "port": TEST_PORT, "password": TEST_PASSWORD},
        )


async def test_connect_client_success_stops_client(hass: HomeAssistant) -> None:
    """connect_client should call client.stop() after successful setup (line 197)."""
    from custom_components.usr_r16.config_flow import connect_client

    with (
        patch(
            "custom_components.usr_r16.protocol.USR16Client.setup",
            new_callable=AsyncMock,
        ) as mock_setup,
        patch(
            "custom_components.usr_r16.protocol.USR16Client.stop",
        ) as mock_stop,
    ):
        mock_setup.return_value = None
        await connect_client(
            hass,
            {"host": TEST_HOST, "port": TEST_PORT, "password": TEST_PASSWORD},
        )

    mock_stop.assert_called_once()
