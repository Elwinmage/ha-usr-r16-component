"""Additional switch tests targeting uncovered lines (async_setup_entry, async_added_to_hass)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.usr_r16 import DATA_DEVICE_REGISTER
from custom_components.usr_r16.const import DOMAIN
from custom_components.usr_r16.switch import R16Switch, async_setup_entry

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("enable_custom_integrations"),
]

TEST_ENTRY_ID = "test_entry_id"


def _make_client():
    client = MagicMock()
    client.is_connected = True
    client.status_callbacks = {}
    client.status = AsyncMock(return_value=False)
    client.turn_on = AsyncMock()
    client.turn_off = AsyncMock()
    client.toggle = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# async_setup_entry (switch.py line 24)
# ---------------------------------------------------------------------------

async def test_switch_async_setup_entry_adds_16_entities(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    """async_setup_entry should call async_add_entities with 16 switches."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][TEST_ENTRY_ID] = {DATA_DEVICE_REGISTER: mock_client}

    added_entities = []

    def fake_add(entities):
        added_entities.extend(entities)

    await async_setup_entry(hass, mock_config_entry, fake_add)

    assert len(added_entities) == 16
    assert all(isinstance(e, R16Switch) for e in added_entities)


# ---------------------------------------------------------------------------
# R16Switch.async_added_to_hass (switch.py lines 56-69)
# ---------------------------------------------------------------------------

async def test_switch_async_added_to_hass_registers_and_fetches(
    hass: HomeAssistant,
) -> None:
    """async_added_to_hass should register callback, fetch state and subscribe."""
    client = _make_client()
    client.status = AsyncMock(return_value={str(1): True})

    sw = R16Switch(1, TEST_ENTRY_ID, client)
    sw.hass = hass
    sw.async_on_remove = MagicMock()

    with patch(
        "homeassistant.helpers.dispatcher.async_dispatcher_connect",
        return_value=MagicMock(),
    ) as mock_connect:
        await sw.async_added_to_hass()

    # register_status_callback called
    client.register_status_callback.assert_called_once_with(sw.handle_event_callback, "1")

    # Initial state fetched from dict result
    client.status.assert_called_once_with("1")

    # Dispatcher connected for availability
    mock_connect.assert_called_once()
    sw.async_on_remove.assert_called_once()


async def test_switch_async_added_to_hass_non_dict_status(hass: HomeAssistant) -> None:
    """async_added_to_hass should handle non-dict status result."""
    client = _make_client()
    client.status = AsyncMock(return_value=True)  # non-dict

    sw = R16Switch(2, TEST_ENTRY_ID, client)
    sw.hass = hass
    sw.async_on_remove = MagicMock()

    with patch(
        "homeassistant.helpers.dispatcher.async_dispatcher_connect",
        return_value=MagicMock(),
    ):
        await sw.async_added_to_hass()

    assert sw._attr_is_on is True
