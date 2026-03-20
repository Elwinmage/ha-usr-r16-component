"""Tests for the USR-R16 switch entities (coordinator-based)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.usr_r16 import USR16Coordinator
from custom_components.usr_r16.const import DOMAIN
from custom_components.usr_r16.switch import R16Switch

TEST_ENTRY_ID = "test_entry_id"
ALL_OFF = {str(i): False for i in range(1, 17)}


def _make_coordinator(hass, states=None):
    """Return a mock-backed coordinator with data pre-set."""
    from homeassistant.config_entries import ConfigEntry
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = TEST_ENTRY_ID
    entry.domain = DOMAIN
    entry.data = {"host": "192.168.1.1", "port": 8899, "password": "admin"}

    coord = USR16Coordinator(hass, entry)
    coord._entry = entry  # ensure _entry is always set
    coord.async_set_updated_data(states if states is not None else dict(ALL_OFF))
    return coord


# ---------------------------------------------------------------------------
# Basic attributes
# ---------------------------------------------------------------------------

def test_unique_id(hass: HomeAssistant) -> None:
    coord = _make_coordinator(hass)
    sw = R16Switch(coord, "3")
    assert sw.unique_id == f"{TEST_ENTRY_ID}_3"


def test_name(hass: HomeAssistant) -> None:
    coord = _make_coordinator(hass)
    sw = R16Switch(coord, "5")
    assert "5" in str(sw.name)


def test_translation_key(hass: HomeAssistant) -> None:
    coord = _make_coordinator(hass)
    sw = R16Switch(coord, "1")
    assert sw._attr_translation_key == "relay"


# ---------------------------------------------------------------------------
# is_on reads from coordinator data
# ---------------------------------------------------------------------------

def test_is_on_false(hass: HomeAssistant) -> None:
    coord = _make_coordinator(hass, {str(i): False for i in range(1, 17)})
    sw = R16Switch(coord, "1")
    assert sw.is_on is False


def test_is_on_true(hass: HomeAssistant) -> None:
    states = {str(i): False for i in range(1, 17)}
    states["4"] = True
    coord = _make_coordinator(hass, states)
    sw = R16Switch(coord, "4")
    assert sw.is_on is True


def test_is_on_none_when_no_data(hass: HomeAssistant) -> None:
    """is_on should return None when coordinator has no data yet."""
    from homeassistant.config_entries import ConfigEntry
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = TEST_ENTRY_ID
    entry.domain = DOMAIN
    entry.data = {"host": "192.168.1.1", "port": 8899, "password": "admin"}
    coord = USR16Coordinator(hass, entry)
    # Do NOT call async_set_updated_data — data stays None
    sw = R16Switch(coord, "1")
    assert sw.is_on is None


def test_coordinator_update_triggers_state_write(hass: HomeAssistant) -> None:
    coord = _make_coordinator(hass)
    sw = R16Switch(coord, "2")
    sw.hass = hass
    sw.async_write_ha_state = MagicMock()

    # Simulate coordinator pushing new data
    coord.async_set_updated_data({"2": True})
    sw._handle_coordinator_update()

    sw.async_write_ha_state.assert_called_once()


# ---------------------------------------------------------------------------
# Relay commands delegate to coordinator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_turn_on(hass: HomeAssistant) -> None:
    coord = _make_coordinator(hass)
    coord.async_turn_on = AsyncMock()
    sw = R16Switch(coord, "2")
    await sw.async_turn_on()
    coord.async_turn_on.assert_called_once_with("2")


@pytest.mark.asyncio
async def test_turn_off(hass: HomeAssistant) -> None:
    coord = _make_coordinator(hass)
    coord.async_turn_off = AsyncMock()
    sw = R16Switch(coord, "4")
    await sw.async_turn_off()
    coord.async_turn_off.assert_called_once_with("4")


@pytest.mark.asyncio
async def test_toggle(hass: HomeAssistant) -> None:
    coord = _make_coordinator(hass)
    coord.async_toggle = AsyncMock()
    sw = R16Switch(coord, "7")
    await sw.async_toggle()
    coord.async_toggle.assert_called_once_with("7")


# ---------------------------------------------------------------------------
# 16 entities created
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_sixteen_switches_created(hass: HomeAssistant) -> None:
    from custom_components.usr_r16.switch import async_setup_entry
    from unittest.mock import patch

    coord = _make_coordinator(hass)
    hass.data.setdefault(DOMAIN, {})[TEST_ENTRY_ID] = coord

    from homeassistant.config_entries import ConfigEntry
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = TEST_ENTRY_ID

    added = []
    def fake_add(entities, update_before_add: bool = False):
        added.extend(entities)

    await async_setup_entry(hass, entry, fake_add)

    assert len(added) == 16
    assert all(isinstance(e, R16Switch) for e in added)
    assert [e._port for e in added] == [str(i) for i in range(1, 17)]
