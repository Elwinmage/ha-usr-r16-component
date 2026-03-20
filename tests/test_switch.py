"""Tests for the USR-R16 switch platform."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.usr_r16 import DATA_DEVICE_REGISTER
from custom_components.usr_r16.const import DOMAIN
from custom_components.usr_r16.switch import R16Switch

TEST_ENTRY_ID = "test_entry_id"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_switch(port: int = 1, client=None) -> R16Switch:
    """Instantiate an R16Switch with a mock client."""
    if client is None:
        client = MagicMock()
        client.is_connected = True
        client.status_callbacks = {}
        client.status = AsyncMock(return_value={str(port): False})
        client.turn_on = AsyncMock()
        client.turn_off = AsyncMock()
        client.toggle = AsyncMock()
    return R16Switch(port, TEST_ENTRY_ID, client)


# ---------------------------------------------------------------------------
# Basic attributes — pure unit tests, no hass needed
# ---------------------------------------------------------------------------

def test_unique_id() -> None:
    """Each relay channel should have a unique ID based on entry + port."""
    sw = _make_switch(port=3)
    assert sw.unique_id == f"{TEST_ENTRY_ID}_3"


def test_name() -> None:
    """Entity name should include the port number."""
    sw = _make_switch(port=5)
    assert "5" in str(sw.name)


def test_should_poll_is_false() -> None:
    """Integration uses push updates — polling must be disabled."""
    sw = _make_switch()
    assert sw.should_poll is False


def test_available_reflects_connection() -> None:
    """Availability should mirror the client connection state."""
    client = MagicMock()
    client.is_connected = True
    client.status_callbacks = {}
    sw = R16Switch(1, TEST_ENTRY_ID, client)
    assert sw.available is True

    # Simulate disconnection
    sw._attr_available = False
    assert sw.available is False


def test_translation_key() -> None:
    """Switch should declare the 'relay' translation key."""
    sw = _make_switch()
    assert sw._attr_translation_key == "relay"


# ---------------------------------------------------------------------------
# State — pure unit tests, no hass needed
# ---------------------------------------------------------------------------

def test_initial_is_on_is_none() -> None:
    """Before the first status fetch, is_on should be None."""
    sw = _make_switch()
    assert sw.is_on is None


def test_event_callback_updates_state() -> None:
    """handle_event_callback should update _attr_is_on."""
    sw = _make_switch()
    sw.hass = MagicMock()
    sw.async_write_ha_state = MagicMock()

    sw.handle_event_callback(True)
    assert sw._attr_is_on is True

    sw.handle_event_callback(False)
    assert sw._attr_is_on is False


# ---------------------------------------------------------------------------
# Commands — async, no hass needed
# ---------------------------------------------------------------------------

async def test_turn_on_calls_client() -> None:
    """async_turn_on should delegate to client.turn_on."""
    sw = _make_switch(port=2)
    await sw.async_turn_on()
    sw._client.turn_on.assert_called_once_with("2")


async def test_turn_off_calls_client() -> None:
    """async_turn_off should delegate to client.turn_off."""
    sw = _make_switch(port=4)
    await sw.async_turn_off()
    sw._client.turn_off.assert_called_once_with("4")


async def test_toggle_calls_client() -> None:
    """async_toggle should delegate to client.toggle."""
    sw = _make_switch(port=7)
    await sw.async_toggle()
    sw._client.toggle.assert_called_once_with("7")


# ---------------------------------------------------------------------------
# 16 relays created — needs hass, must be async
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("enable_custom_integrations")
async def test_sixteen_relays_created(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    """devices_from_entities should return exactly 16 R16Switch instances."""
    from custom_components.usr_r16.switch import devices_from_entities

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][TEST_ENTRY_ID] = {DATA_DEVICE_REGISTER: mock_client}

    devices = devices_from_entities(hass, mock_config_entry)

    assert len(devices) == 16
    assert all(isinstance(d, R16Switch) for d in devices)
    ports = [d._device_port for d in devices]
    assert ports == [str(i) for i in range(1, 17)]
