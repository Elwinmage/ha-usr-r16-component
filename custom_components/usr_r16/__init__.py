"""USR-R16 relay board — Home Assistant integration."""

import asyncio
import logging

from usr_r16 import create_usr_r16_client_connection
import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_SWITCHES
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONNECTION_TIMEOUT,
    DEFAULT_KEEP_ALIVE_INTERVAL,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_RECONNECT_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Number of relay channels on the USR-R16
RELAY_COUNT = 16

SWITCH_SCHEMA = vol.Schema({vol.Optional(CONF_NAME): cv.string})
RELAY_ID = vol.All(vol.Any(*range(1, 17)), vol.Coerce(str))

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                cv.string: vol.Schema(
                    {
                        vol.Required(CONF_HOST): cv.string,
                        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
                        vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): cv.string,
                        vol.Required(CONF_SWITCHES): vol.Schema({RELAY_ID: SWITCH_SCHEMA}),
                    }
                )
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Import legacy YAML configuration as config entries."""
    if DOMAIN not in config:
        return True

    for device_id in config[DOMAIN]:
        conf = config[DOMAIN][device_id]
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data={
                    CONF_HOST: conf[CONF_HOST],
                    CONF_PORT: conf[CONF_PORT],
                    CONF_PASSWORD: conf[CONF_PASSWORD],
                },
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a USR-R16 device from a config entry."""
    _LOGGER.debug("Setting up USR-R16 entry %s", entry.entry_id)

    coordinator = USR16Coordinator(hass, entry)
    await coordinator.async_connect()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, ["switch"])

    _LOGGER.debug("USR-R16 entry %s setup complete", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading USR-R16 entry %s", entry.entry_id)

    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "switch")

    coordinator: USR16Coordinator = hass.data[DOMAIN].pop(entry.entry_id)
    coordinator.stop()

    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)

    return unload_ok


class USR16Coordinator(DataUpdateCoordinator[dict[str, bool]]):
    """Coordinator for the USR-R16 relay board.

    Holds the TCP client and the authoritative state dict for all 16 relays.
    Entities subscribe to this coordinator — no direct client access needed.
    Push callbacks from the device call async_set_updated_data() so entities
    are refreshed immediately without any polling interval.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"USR-R16 {entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}",
            # No polling interval — the device pushes state changes
        )
        self._entry = entry
        self._host = entry.data[CONF_HOST]
        self._port = entry.data[CONF_PORT]
        self._password = entry.data[CONF_PASSWORD]
        self._client = None
        self._address = f"{self._host}:{self._port}"

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def async_connect(self) -> None:
        """Open the TCP connection and do the initial state fetch."""
        _LOGGER.debug("Connecting to USR-R16 at %s", self._address)

        self._client = await create_usr_r16_client_connection(
            host=self._host,
            port=self._port,
            password=self._password,
            disconnect_callback=self._on_disconnected,
            reconnect_callback=self._on_reconnected,
            loop=asyncio.get_event_loop(),
            timeout=CONNECTION_TIMEOUT,
            reconnect_interval=DEFAULT_RECONNECT_INTERVAL,
            keep_alive_interval=DEFAULT_KEEP_ALIVE_INTERVAL,
        )

        _LOGGER.info("Connected to USR-R16 at %s", self._address)

        # Register a single catch-all callback that refreshes all entities
        for port in range(1, RELAY_COUNT + 1):
            self._client.register_status_callback(
                self._make_relay_callback(str(port)), str(port)
            )
            _LOGGER.debug("Registered status callback for relay %s", port)

        # Fetch initial states and publish to all subscribers
        states = await self._client.status()
        _LOGGER.debug("Initial relay states: %s", states)
        self.async_set_updated_data(states)

    def stop(self) -> None:
        """Stop the TCP client."""
        if self._client:
            _LOGGER.debug("Stopping USR-R16 client for %s", self._address)
            self._client.stop()
            self._client = None

    # ------------------------------------------------------------------
    # Push callbacks from the device
    # ------------------------------------------------------------------

    def _make_relay_callback(self, port: str):
        """Return a closure that updates coordinator data for a single relay."""
        @callback
        def relay_callback(state: bool) -> None:
            _LOGGER.debug("Push: relay %s → %s on %s", port, state, self._address)
            # Merge the new value into a copy of the current data and publish
            current = dict(self.data) if self.data else {}
            current[port] = state
            self.async_set_updated_data(current)

        return relay_callback

    @callback
    def _on_disconnected(self) -> None:
        """Mark coordinator unavailable on TCP disconnect."""
        _LOGGER.warning("USR-R16 %s disconnected", self._address)
        self.last_update_success = False
        self.async_update_listeners()

    @callback
    def _on_reconnected(self) -> None:
        """Re-fetch states after TCP reconnect."""
        _LOGGER.info("USR-R16 %s reconnected", self._address)

        async def _refresh() -> None:
            try:
                states = await self._client.status()
                _LOGGER.debug("States after reconnect: %s", states)
                self.async_set_updated_data(states)
            except Exception as err:
                _LOGGER.error("Failed to refresh states after reconnect: %s", err)

        self.hass.async_create_task(_refresh())

    # ------------------------------------------------------------------
    # DataUpdateCoordinator required method (no scheduled polling)
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, bool]:
        """Fetch data — called on demand only (no interval set)."""
        _LOGGER.debug("Manual data refresh for %s", self._address)
        try:
            return await self._client.status()
        except Exception as err:
            raise UpdateFailed(f"Error fetching USR-R16 status: {err}") from err

    # ------------------------------------------------------------------
    # Relay control helpers (used by switch entities)
    # ------------------------------------------------------------------

    async def async_turn_on(self, port: str) -> None:
        """Close relay (ON)."""
        _LOGGER.debug("Turn ON relay %s on %s", port, self._address)
        await self._client.turn_on(port)

    async def async_turn_off(self, port: str) -> None:
        """Open relay (OFF)."""
        _LOGGER.debug("Turn OFF relay %s on %s", port, self._address)
        await self._client.turn_off(port)

    async def async_toggle(self, port: str) -> None:
        """Toggle relay."""
        _LOGGER.debug("Toggle relay %s on %s", port, self._address)
        await self._client.toggle(port)
