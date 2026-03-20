"""Pytest configuration and shared fixtures for ha-usr-r16-component tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.usr_r16.const import DEFAULT_PASSWORD, DEFAULT_PORT, DOMAIN

TEST_HOST = "192.168.1.100"
TEST_PORT = DEFAULT_PORT
TEST_PASSWORD = DEFAULT_PASSWORD
TEST_ENTRY_ID = "test_entry_id"


@pytest.fixture
def mock_client():
    """Return a mock USR-R16 client."""
    client = MagicMock()
    client.is_connected = True
    client.in_transaction = False
    client.active_transaction = None
    client.status_callbacks = {}
    client.states = {}
    client.status = AsyncMock(return_value={str(i): False for i in range(1, 17)})
    client.turn_on = AsyncMock()
    client.turn_off = AsyncMock()
    client.toggle = AsyncMock()
    client.stop = MagicMock()
    return client


@pytest.fixture
def mock_config_entry(hass: HomeAssistant):
    """Return a mock config entry for usr_r16."""
    from homeassistant.config_entries import ConfigEntry

    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = TEST_ENTRY_ID
    entry.domain = DOMAIN
    entry.data = {
        "host": TEST_HOST,
        "port": TEST_PORT,
        "password": TEST_PASSWORD,
    }
    return entry
