"""Tests for __init__.py — coordinator lifecycle."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.usr_r16 import USR16Coordinator, async_setup, async_setup_entry, async_unload_entry
from custom_components.usr_r16.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

TEST_HOST = "192.168.1.10"
TEST_PORT = 8899
TEST_PASSWORD = "admin"
TEST_ENTRY_ID = "test_entry_abc"

ALL_OFF = {str(i): False for i in range(1, 17)}
ALL_ON  = {str(i): True  for i in range(1, 17)}


def _make_entry(entry_id=TEST_ENTRY_ID):
    from homeassistant.config_entries import ConfigEntry
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = entry_id
    entry.domain = DOMAIN
    entry.data = {"host": TEST_HOST, "port": TEST_PORT, "password": TEST_PASSWORD}
    return entry


def _make_mock_client(states=None):
    client = MagicMock()
    client.is_connected = True
    client.in_transaction = False
    client.active_transaction = None
    client.status_callbacks = {}
    client.states = states or dict(ALL_OFF)
    client.status = AsyncMock(return_value=states or dict(ALL_OFF))
    client.stop = MagicMock()
    return client


# ---------------------------------------------------------------------------
# async_setup — YAML import
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_setup_with_domain_creates_tasks(hass: HomeAssistant) -> None:
    config = {DOMAIN: {"dev1": {"host": TEST_HOST, "port": TEST_PORT, "password": TEST_PASSWORD}}}
    with patch.object(hass.config_entries.flow, "async_init", return_value={}) as mock_init:
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
async def test_coordinator_connect_publishes_initial_states(hass: HomeAssistant) -> None:
    entry = _make_entry()
    mock_client = _make_mock_client(ALL_OFF)

    coordinator = USR16Coordinator(hass, entry)
    with patch(
        "custom_components.usr_r16.create_usr_r16_client_connection",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):
        await coordinator.async_connect()

    assert coordinator.data is not None
    assert coordinator.data["1"] is False
    assert coordinator.data["16"] is False


@pytest.mark.asyncio
async def test_coordinator_relay_callback_updates_data(hass: HomeAssistant) -> None:
    entry = _make_entry()
    mock_client = _make_mock_client(ALL_OFF)

    coordinator = USR16Coordinator(hass, entry)
    with patch(
        "custom_components.usr_r16.create_usr_r16_client_connection",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):
        await coordinator.async_connect()

    # Simulate a push callback for relay 3 turning ON
    cb = coordinator._make_relay_callback("3")
    cb(True)

    assert coordinator.data["3"] is True
    assert coordinator.data["1"] is False  # others unchanged


@pytest.mark.asyncio
async def test_coordinator_disconnect_callback(hass: HomeAssistant) -> None:
    entry = _make_entry()
    mock_client = _make_mock_client()

    coordinator = USR16Coordinator(hass, entry)
    with patch(
        "custom_components.usr_r16.create_usr_r16_client_connection",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):
        await coordinator.async_connect()

    coordinator._on_disconnected()
    assert coordinator.last_update_success is False


@pytest.mark.asyncio
async def test_coordinator_reconnect_refreshes_states(hass: HomeAssistant) -> None:
    entry = _make_entry()
    new_states = {str(i): True for i in range(1, 17)}
    mock_client = _make_mock_client(ALL_OFF)
    mock_client.status = AsyncMock(side_effect=[dict(ALL_OFF), new_states])

    coordinator = USR16Coordinator(hass, entry)
    with patch(
        "custom_components.usr_r16.create_usr_r16_client_connection",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):
        await coordinator.async_connect()

    coordinator._on_reconnected()
    await hass.async_block_till_done()

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
    entry = _make_entry()
    mock_client = _make_mock_client(ALL_OFF)

    coordinator = USR16Coordinator(hass, entry)
    with patch(
        "custom_components.usr_r16.create_usr_r16_client_connection",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):
        await coordinator.async_connect()

    mock_client.status = AsyncMock(return_value=ALL_ON)
    result = await coordinator._async_update_data()
    assert result == ALL_ON


@pytest.mark.asyncio
async def test_coordinator_relay_controls(hass: HomeAssistant) -> None:
    entry = _make_entry()
    mock_client = _make_mock_client()
    mock_client.turn_on  = AsyncMock()
    mock_client.turn_off = AsyncMock()
    mock_client.toggle   = AsyncMock()

    coordinator = USR16Coordinator(hass, entry)
    with patch(
        "custom_components.usr_r16.create_usr_r16_client_connection",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):
        await coordinator.async_connect()

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
    mock_client = _make_mock_client()

    with patch(
        "custom_components.usr_r16.create_usr_r16_client_connection",
        new_callable=AsyncMock,
        return_value=mock_client,
    ), patch.object(
        hass.config_entries, "async_forward_entry_setups", return_value=True
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

    with patch.object(hass.config_entries, "async_forward_entry_unload", return_value=True):
        ok = await async_unload_entry(hass, entry)

    assert ok is True
    mock_client.stop.assert_called_once()
    assert entry.entry_id not in hass.data.get(DOMAIN, {})


@pytest.mark.asyncio
async def test_coordinator_reconnect_status_error_logged(hass: HomeAssistant) -> None:
    """Exception during post-reconnect status() should be logged, not raised."""
    entry = _make_entry()
    mock_client = _make_mock_client(ALL_OFF)
    mock_client.status = AsyncMock(side_effect=[dict(ALL_OFF), Exception("boom")])

    coordinator = USR16Coordinator(hass, entry)
    with patch(
        "custom_components.usr_r16.create_usr_r16_client_connection",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):
        await coordinator.async_connect()

    # Should not raise
    coordinator._on_reconnected()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_coordinator_async_update_data_raises_update_failed(hass: HomeAssistant) -> None:
    """_async_update_data should wrap exceptions as UpdateFailed."""
    from homeassistant.helpers.update_coordinator import UpdateFailed
    entry = _make_entry()
    mock_client = _make_mock_client(ALL_OFF)

    coordinator = USR16Coordinator(hass, entry)
    with patch(
        "custom_components.usr_r16.create_usr_r16_client_connection",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):
        await coordinator.async_connect()

    mock_client.status = AsyncMock(side_effect=Exception("network error"))
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
