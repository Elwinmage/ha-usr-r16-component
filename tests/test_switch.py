"""Tests for the USR-R16 switch platform."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.usr_r16 import DATA_DEVICE_REGISTER
from custom_components.usr_r16.const import DOMAIN
from custom_components.usr_r16.switch import R16Switch

from .conftest import TEST_ENTRY_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_switch(port: int = 1, client=None):
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
# Basic attributes
# ---------------------------------------------------------------------------

def test_unique_id():
    """Each relay channel should have a unique ID based on entry + port."""
    sw = _make_switch(port=3)
    assert sw.unique_id == f"{TEST_ENTRY_ID}_3"


def test_name():
    """Entity name should include the domain and port number."""
    sw = _make_switch(port=5)
    assert "5" in sw.name


def test_should_poll_is_false():
    """Integration uses push updates — polling must be disabled."""
    sw = _make_switch()
    assert sw.should_poll is False


def test_available_reflects_connection():
    """Availability should mirror the client connection state."""
    client = MagicMock()
    client.is_connected = True
    client.status_callbacks = {}
    sw = R16Switch(1, TEST_ENTRY_ID, client)
    assert sw.available is True

    # Simulate disconnection
    sw._attr_available = False
    assert sw.available is False


def test_translation_key():
    """Switch should declare the 'relay' translation key."""
    sw = _make_switch()
    assert sw._attr_translation_key == "relay"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def test_initial_is_on_is_none():
    """Before the first status fetch, is_on should be None."""
    sw = _make_switch()
    assert sw.is_on is None


def test_event_callback_updates_state():
    """handle_event_callback should update _attr_is_on."""
    sw = _make_switch()
    sw.hass = MagicMock()
    sw.async_write_ha_state = MagicMock()

    sw.handle_event_callback(True)
    assert sw._attr_is_on is True

    sw.handle_event_callback(False)
    assert sw._attr_is_on is False


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_turn_on_calls_client(hass: HomeAssistant):
    """async_turn_on should delegate to client.turn_on."""
    sw = _make_switch(port=2)
    await sw.async_turn_on()
    sw._client.turn_on.assert_called_once_with("2")


@pytest.mark.asyncio
async def test_turn_off_calls_client(hass: HomeAssistant):
    """async_turn_off should delegate to client.turn_off."""
    sw = _make_switch(port=4)
    await sw.async_turn_off()
    sw._client.turn_off.assert_called_once_with("4")


@pytest.mark.asyncio
async def test_toggle_calls_client(hass: HomeAssistant):
    """async_toggle should delegate to client.toggle."""
    sw = _make_switch(port=7)
    await sw.async_toggle()
    sw._client.toggle.assert_called_once_with("7")


# ---------------------------------------------------------------------------
# 16 relays created
# ---------------------------------------------------------------------------

def test_sixteen_relays_created(hass: HomeAssistant, mock_client, mock_config_entry):
    """devices_from_entities should return exactly 16 R16Switch instances."""
    from custom_components.usr_r16.switch import devices_from_entities

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][TEST_ENTRY_ID] = {DATA_DEVICE_REGISTER: mock_client}

    devices = devices_from_entities(hass, mock_config_entry)

    assert len(devices) == 16
    assert all(isinstance(d, R16Switch) for d in devices)
    # Port numbers should run from 1 to 16
    ports = [d._device_port for d in devices]
    assert ports == [str(i) for i in range(1, 17)]
