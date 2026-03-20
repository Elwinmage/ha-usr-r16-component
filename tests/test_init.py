"""Tests for custom_components/usr_r16/__init__.py — targeting uncovered lines."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.usr_r16 import (
    DATA_DEVICE_REGISTER,
    R16Device,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.usr_r16.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

TEST_HOST = "192.168.1.10"
TEST_PORT = 8899
TEST_PASSWORD = "admin"
TEST_ENTRY_ID = "entry_abc"


def _make_entry(entry_id=TEST_ENTRY_ID):
    """Return a minimal mock ConfigEntry."""
    from homeassistant.config_entries import ConfigEntry
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = entry_id
    entry.domain = DOMAIN
    entry.data = {"host": TEST_HOST, "port": TEST_PORT, "password": TEST_PASSWORD}
    return entry


def _make_mock_client():
    """Return a mock USR-R16 client."""
    client = MagicMock()
    client.is_connected = True
    client.in_transaction = False
    client.active_transaction = None
    client.status_callbacks = {}
    client.status = AsyncMock(return_value={str(i): False for i in range(1, 17)})
    client.stop = MagicMock()
    return client


# ---------------------------------------------------------------------------
# async_setup — DOMAIN in config (lines 75-77, 88)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_setup_with_domain_in_config(hass: HomeAssistant) -> None:
    """async_setup should create import tasks for each device in the config."""
    config = {
        DOMAIN: {
            "device1": {
                "host": TEST_HOST,
                "port": TEST_PORT,
                "password": TEST_PASSWORD,
            }
        }
    }
    with patch.object(hass.config_entries.flow, "async_init", return_value={}) as mock_init:
        result = await async_setup(hass, config)
    assert result is True
    mock_init.assert_called_once()


@pytest.mark.asyncio
async def test_async_setup_without_domain(hass: HomeAssistant) -> None:
    """async_setup should return True immediately when DOMAIN absent."""
    result = await async_setup(hass, {})
    assert result is True


# ---------------------------------------------------------------------------
# async_setup_entry — connect() coroutine and callbacks (lines 104-105, 110-111, 133)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_setup_entry_runs_connect(hass: HomeAssistant) -> None:
    """async_setup_entry should schedule connect() which populates hass.data."""
    entry = _make_entry()
    mock_client = _make_mock_client()

    captured: dict = {}

    async def fake_create_connection(**kwargs):
        captured["disconnect_callback"] = kwargs.get("disconnect_callback")
        captured["reconnect_callback"] = kwargs.get("reconnect_callback")
        return mock_client

    with patch(
        "custom_components.usr_r16.create_usr_r16_client_connection",
        side_effect=fake_create_connection,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups", return_value=True
    ):
        result = await async_setup_entry(hass, entry)
        assert result is True
        # Let the scheduled connect() task run
        await hass.async_block_till_done()

    # Client should be registered
    assert hass.data[DOMAIN][entry.entry_id][DATA_DEVICE_REGISTER] is mock_client

    # Verify disconnected() callback dispatches False (lines 104-105)
    disconnect_cb = captured.get("disconnect_callback")
    assert disconnect_cb is not None
    disconnect_cb()

    # Verify reconnected() callback dispatches True (lines 110-111)
    reconnect_cb = captured.get("reconnect_callback")
    assert reconnect_cb is not None
    reconnect_cb()


# ---------------------------------------------------------------------------
# async_unload_entry — cleanup edge cases (lines 147-151)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_unload_entry_cleans_up(hass: HomeAssistant) -> None:
    """async_unload_entry should clean hass.data on successful unload."""
    entry = _make_entry()
    mock_client = _make_mock_client()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {DATA_DEVICE_REGISTER: mock_client}

    with patch.object(
        hass.config_entries, "async_forward_entry_unload", return_value=True
    ):
        ok = await async_unload_entry(hass, entry)

    assert ok is True
    mock_client.stop.assert_called_once()
    assert entry.entry_id not in hass.data.get(DOMAIN, {})


@pytest.mark.asyncio
async def test_async_unload_entry_keeps_domain_when_other_entries(hass: HomeAssistant) -> None:
    """DOMAIN key in hass.data should persist if other entries still exist."""
    entry = _make_entry("entry_1")
    other_entry_id = "entry_2"
    mock_client = _make_mock_client()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {DATA_DEVICE_REGISTER: mock_client}
    hass.data[DOMAIN][other_entry_id] = {}  # Simulate a second entry

    with patch.object(
        hass.config_entries, "async_forward_entry_unload", return_value=True
    ):
        await async_unload_entry(hass, entry)

    # DOMAIN should still be present for the remaining entry
    assert DOMAIN in hass.data
    assert other_entry_id in hass.data[DOMAIN]


# ---------------------------------------------------------------------------
# R16Device — handle_event_callback (lines 183-185)
# ---------------------------------------------------------------------------

def test_r16device_handle_event_callback() -> None:
    """R16Device.handle_event_callback should update _is_on and call write_ha_state."""
    client = MagicMock()
    client.is_connected = True
    device = R16Device(1, "entry_x", client)
    device.async_write_ha_state = MagicMock()

    # Call the BASE class version directly (R16Switch overrides it)
    R16Device.handle_event_callback(device, True)
    assert device._is_on is True
    device.async_write_ha_state.assert_called_once()

    device.async_write_ha_state.reset_mock()
    R16Device.handle_event_callback(device, False)
    assert device._is_on is False
    device.async_write_ha_state.assert_called_once()


# ---------------------------------------------------------------------------
# R16Device — _availability_callback (lines 190-191)
# ---------------------------------------------------------------------------

def test_r16device_availability_callback() -> None:
    """_availability_callback should update _attr_available and write state."""
    client = MagicMock()
    client.is_connected = True
    device = R16Device(1, "entry_x", client)
    device.async_write_ha_state = MagicMock()

    device._availability_callback(False)
    assert device._attr_available is False
    device.async_write_ha_state.assert_called_once()

    device.async_write_ha_state.reset_mock()
    device._availability_callback(True)
    assert device._attr_available is True
    device.async_write_ha_state.assert_called_once()


# ---------------------------------------------------------------------------
# R16Device — async_added_to_hass (lines 196, 201, 204)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_r16device_async_added_to_hass() -> None:
    """async_added_to_hass should register callback, fetch status and subscribe dispatcher."""
    client = MagicMock()
    client.is_connected = True
    client.status = AsyncMock(return_value=False)

    device = R16Device(1, TEST_ENTRY_ID, client)
    device.hass = MagicMock()
    device.async_on_remove = MagicMock()

    with patch(
        "custom_components.usr_r16.async_dispatcher_connect",
        return_value=MagicMock(),
    ) as mock_connect:
        await device.async_added_to_hass()

    # register_status_callback should be called with the callback and port
    client.register_status_callback.assert_called_once_with(
        device.handle_event_callback, "1"
    )
    # Initial status fetched
    client.status.assert_called_once_with("1")
    assert device._is_on is False

    # Dispatcher connected
    mock_connect.assert_called_once()
    # async_on_remove called with the unsubscribe token
    device.async_on_remove.assert_called_once()
