"""Tests for __init__.py — coordinator lifecycle."""

import asyncio

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.usr_r16 import (
    USR16Coordinator,
    async_setup,
    async_setup_entry,
    async_unload_entry,
    _async_update_options,
)
from custom_components.usr_r16.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

TEST_HOST = "192.168.1.10"
TEST_PORT = 8899
TEST_PASSWORD = "admin"
TEST_ENTRY_ID = "test_entry_abc"

ALL_OFF = {str(i): False for i in range(1, 17)}
ALL_ON = {str(i): True for i in range(1, 17)}


def _make_entry(entry_id=TEST_ENTRY_ID):
    from homeassistant.config_entries import ConfigEntry

    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = entry_id
    entry.domain = DOMAIN
    entry.data = {"host": TEST_HOST, "port": TEST_PORT, "password": TEST_PASSWORD}
    entry.options = {}
    return entry


def _make_mock_client(states=None):
    """Create a mock USR16Client with pre-set states."""
    client = MagicMock()
    client.is_connected = True
    client.in_transaction = False
    client.active_transaction = None
    client.status_callbacks = {}
    client.states = dict(states) if states is not None else dict(ALL_OFF)
    client.stop = MagicMock()
    client.turn_on = AsyncMock()
    client.turn_off = AsyncMock()
    client.toggle = AsyncMock()
    client.register_status_callback = MagicMock(
        side_effect=lambda cb, port: client.status_callbacks.setdefault(
            port, []
        ).append(cb)
    )
    return client


def _setup_coordinator_with_client(hass, mock_client, entry=None):
    """Create coordinator and inject mock client directly (bypasses TCP)."""
    if entry is None:
        entry = _make_entry()
    coordinator = USR16Coordinator(hass, entry)
    coordinator._client = mock_client
    coordinator.async_set_updated_data(dict(mock_client.states))
    return coordinator, entry


# ---------------------------------------------------------------------------
# async_setup — YAML import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_setup_with_domain_creates_tasks(hass: HomeAssistant) -> None:
    config = {
        DOMAIN: {
            "dev1": {"host": TEST_HOST, "port": TEST_PORT, "password": TEST_PASSWORD}
        }
    }
    with patch.object(
        hass.config_entries.flow, "async_init", return_value={}
    ) as mock_init:
        result = await async_setup(hass, config)
    assert result is True
    mock_init.assert_called_once()


@pytest.mark.asyncio
async def test_async_setup_without_domain(hass: HomeAssistant) -> None:
    result = await async_setup(hass, {})
    assert result is True


# ---------------------------------------------------------------------------
# USR16Coordinator — connect and initial state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_connect_publishes_initial_states(
    hass: HomeAssistant,
) -> None:
    mock_client = _make_mock_client(ALL_OFF)
    coordinator, _ = _setup_coordinator_with_client(hass, mock_client)

    assert coordinator.data is not None
    assert coordinator.data["1"] is False
    assert coordinator.data["16"] is False


@pytest.mark.asyncio
async def test_coordinator_relay_callback_updates_data(hass: HomeAssistant) -> None:
    mock_client = _make_mock_client(ALL_OFF)
    coordinator, _ = _setup_coordinator_with_client(hass, mock_client)

    # Simulate a push callback for relay 3 turning ON
    cb = coordinator._make_relay_callback("3")
    cb(True)

    assert coordinator.data["3"] is True
    assert coordinator.data["1"] is False  # others unchanged


@pytest.mark.asyncio
async def test_coordinator_disconnect_callback(hass: HomeAssistant) -> None:
    mock_client = _make_mock_client()
    coordinator, _ = _setup_coordinator_with_client(hass, mock_client)
    coordinator._on_disconnected()
    assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_coordinator_reconnect_refreshes_states(hass: HomeAssistant) -> None:
    new_states = {str(i): True for i in range(1, 17)}
    mock_client = _make_mock_client(ALL_OFF)
    coordinator, _ = _setup_coordinator_with_client(hass, mock_client)

    # Update client.states then call _async_refresh_after_reconnect directly
    # (_on_reconnected schedules it as a task, which is harder to test)
    mock_client.states = new_states
    await coordinator._async_refresh_after_reconnect()

    assert coordinator.data == new_states


def test_coordinator_stop_calls_client_stop(hass) -> None:
    entry = _make_entry()
    coordinator = USR16Coordinator(hass, entry)
    coordinator._entry = entry
    mock_client = MagicMock()
    coordinator._client = mock_client
    coordinator.stop()
    mock_client.stop.assert_called_once()
    assert coordinator._client is None


@pytest.mark.asyncio
async def test_coordinator_async_update_data(hass: HomeAssistant) -> None:
    """_async_update_data should return client.states directly."""
    mock_client = _make_mock_client(ALL_OFF)
    coordinator, _ = _setup_coordinator_with_client(hass, mock_client)

    mock_client.states = dict(ALL_ON)
    result = await coordinator._async_update_data()
    assert result == ALL_ON


@pytest.mark.asyncio
async def test_coordinator_relay_controls(hass: HomeAssistant) -> None:
    mock_client = _make_mock_client()
    coordinator, _ = _setup_coordinator_with_client(hass, mock_client)

    await coordinator.async_turn_on("3")
    mock_client.turn_on.assert_called_once_with("3")

    await coordinator.async_turn_off("5")
    mock_client.turn_off.assert_called_once_with("5")

    await coordinator.async_toggle("7")
    mock_client.toggle.assert_called_once_with("7")


# ---------------------------------------------------------------------------
# async_setup_entry / async_unload_entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_setup_entry_stores_coordinator(hass: HomeAssistant) -> None:
    entry = _make_entry()

    with (
        patch(
            "custom_components.usr_r16.protocol.USR16Client.setup",
            new_callable=AsyncMock,
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", return_value=True
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    assert entry.entry_id in hass.data[DOMAIN]
    assert isinstance(hass.data[DOMAIN][entry.entry_id], USR16Coordinator)


@pytest.mark.asyncio
async def test_async_unload_entry_cleans_up(hass: HomeAssistant) -> None:
    entry = _make_entry()
    mock_client = _make_mock_client()
    coordinator = USR16Coordinator(hass, entry)
    coordinator._client = mock_client
    coordinator.async_set_updated_data(dict(ALL_OFF))

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    with patch.object(
        hass.config_entries, "async_forward_entry_unload", return_value=True
    ):
        ok = await async_unload_entry(hass, entry)

    assert ok is True
    mock_client.stop.assert_called_once()
    assert entry.entry_id not in hass.data.get(DOMAIN, {})


@pytest.mark.asyncio
async def test_coordinator_reconnect_status_error_logged(hass: HomeAssistant) -> None:
    """Exception during _async_refresh_after_reconnect should be logged, not raised."""
    entry = _make_entry()
    mock_client = _make_mock_client(ALL_OFF)
    mock_client.status = AsyncMock(side_effect=Exception("boom"))

    coordinator = USR16Coordinator(hass, entry)
    coordinator._entry = entry
    coordinator._client = mock_client
    coordinator.async_set_updated_data(dict(ALL_OFF))

    # Should not raise — exception is caught and logged
    await coordinator._async_refresh_after_reconnect()


@pytest.mark.asyncio
async def test_coordinator_async_update_data_raises_update_failed(
    hass: HomeAssistant,
) -> None:
    """_async_update_data should raise UpdateFailed when client is None."""
    from homeassistant.helpers.update_coordinator import UpdateFailed

    entry = _make_entry()
    coordinator = USR16Coordinator(hass, entry)
    # _client is None — should raise UpdateFailed
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_coordinator_connect_empty_states_returns_empty(
    hass: HomeAssistant,
) -> None:
    """When client.states is empty, coordinator data is empty dict (not None)."""
    entry = _make_entry()
    mock_client = _make_mock_client()
    mock_client.states = {}  # empty — client.states read directly

    coordinator = USR16Coordinator(hass, entry)
    with patch(
        "custom_components.usr_r16.protocol.USR16Client.setup",
        new_callable=AsyncMock,
    ):
        await coordinator.async_connect()

    # Data should be {} (empty, not None) — device will push actual states
    assert coordinator.data == {}


@pytest.mark.asyncio
async def test_coordinator_refresh_success_path(hass: HomeAssistant) -> None:
    """Directly test _async_refresh_after_reconnect success path."""
    entry = _make_entry()
    new_states = {str(i): True for i in range(1, 17)}
    mock_client = _make_mock_client(ALL_OFF)
    mock_client.states = dict(ALL_OFF)  # non-empty — no fallback on connect

    coordinator = USR16Coordinator(hass, entry)
    with patch(
        "custom_components.usr_r16.protocol.USR16Client.setup",
        new_callable=AsyncMock,
    ):
        await coordinator.async_connect()

    # Call _async_refresh_after_reconnect directly without going through async_connect
    coordinator2 = USR16Coordinator(hass, entry)
    mock_client2 = _make_mock_client(new_states)
    mock_client2.status = AsyncMock(return_value=new_states)
    coordinator2._client = mock_client2
    coordinator2.async_set_updated_data(dict(ALL_OFF))
    await coordinator2._async_refresh_after_reconnect()
    assert coordinator2.data == new_states


@pytest.mark.asyncio
async def test_async_setup_entry_uses_options_when_set(hass: HomeAssistant) -> None:
    """When entry.options is non-empty, coordinator should use options values (line 79)."""
    entry = _make_entry()
    entry.options = {"host": "10.0.0.5", "port": 8899, "password": "newpass"}

    with (
        patch(
            "custom_components.usr_r16.protocol.USR16Client.setup",
            new_callable=AsyncMock,
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", return_value=True
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    coordinator = hass.data[DOMAIN][entry.entry_id]
    # Coordinator should have picked up the host from options
    assert coordinator._host == "10.0.0.5"
    assert coordinator._password == "newpass"


@pytest.mark.asyncio
async def test_async_update_options_reloads_entry(hass: HomeAssistant) -> None:
    """_async_update_options should trigger an entry reload (lines 97-98)."""
    entry = _make_entry()

    with patch.object(
        hass.config_entries, "async_reload", return_value=True
    ) as mock_reload:
        await _async_update_options(hass, entry)

    mock_reload.assert_called_once_with(entry.entry_id)


@pytest.mark.asyncio
async def test_on_reconnected_ignored_when_client_none(hass: HomeAssistant) -> None:
    """_on_reconnected should do nothing when client is None (lines 218-219)."""
    entry = _make_entry()
    coordinator = USR16Coordinator(hass, entry)
    # _client is None by default — should not raise
    coordinator._on_reconnected()
    await hass.async_block_till_done()
    # No task should have been created — data still None
    assert coordinator.data is None


@pytest.mark.asyncio
async def test_coordinator_wait_for_states_succeeds(hass: HomeAssistant) -> None:
    """_async_wait_for_states should publish once client.states is populated (lines 196-202)."""
    entry = _make_entry()
    mock_client = _make_mock_client()
    mock_client.states = {}  # empty initially

    coordinator = USR16Coordinator(hass, entry)
    coordinator._client = mock_client
    coordinator._address = "test:8899"

    # Populate states after first sleep
    async def set_states_after_delay():
        await asyncio.sleep(0.05)
        mock_client.states = dict(ALL_OFF)

    hass.async_create_task(set_states_after_delay())
    await coordinator._async_wait_for_states()

    assert coordinator.data == ALL_OFF


@pytest.mark.asyncio
async def test_coordinator_wait_for_states_timeout_publishes_empty(
    hass: HomeAssistant,
) -> None:
    """_async_wait_for_states should publish {} after 2s if still empty (line 208+)."""
    entry = _make_entry()
    mock_client = _make_mock_client()
    mock_client.states = {}

    coordinator = USR16Coordinator(hass, entry)
    coordinator._client = mock_client
    coordinator._address = "test:8899"

    # Patch sleep to be instant so test doesn't take 2s
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await coordinator._async_wait_for_states()

    assert coordinator.data == {}


def test_on_reconnected_when_client_assigned(hass: HomeAssistant) -> None:
    """_on_reconnected should schedule task when client is set (line 254)."""
    mock_client = _make_mock_client()
    coordinator, _ = _setup_coordinator_with_client(hass, mock_client)

    with patch.object(hass, "async_create_task") as mock_task:
        coordinator._on_reconnected()
    mock_task.assert_called_once()


@pytest.mark.asyncio
async def test_refresh_after_reconnect_error_logged(hass: HomeAssistant) -> None:
    """Exception in _async_refresh_after_reconnect should be caught (lines 262-263)."""
    mock_client = _make_mock_client()
    mock_client.states = MagicMock(side_effect=Exception("boom"))

    coordinator, _ = _setup_coordinator_with_client(hass, mock_client)
    # Should not raise
    await coordinator._async_refresh_after_reconnect()


@pytest.mark.asyncio
async def test_refresh_after_reconnect_success(hass: HomeAssistant) -> None:
    """_async_refresh_after_reconnect success path publishes data (lines 262-263)."""
    mock_client = _make_mock_client(ALL_ON)
    coordinator, _ = _setup_coordinator_with_client(hass, mock_client)
    # Reset data to OFF so we can verify the refresh updates it to ON
    coordinator.async_set_updated_data(dict(ALL_OFF))
    assert coordinator.data["1"] is False

    # Refresh reads client.states (ALL_ON) and publishes
    await coordinator._async_refresh_after_reconnect()
    assert coordinator.data == ALL_ON
