"""USR-R16 relay board — Home Assistant integration."""

from datetime import timedelta

import asyncio
import logging

from .protocol import USR16Client
import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SWITCHES,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SCAN_INTERVAL,
    CONNECTION_TIMEOUT,
    DEFAULT_KEEP_ALIVE_INTERVAL,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_RECONNECT_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
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
                        vol.Optional(
                            CONF_PASSWORD, default=DEFAULT_PASSWORD
                        ): cv.string,
                        vol.Required(CONF_SWITCHES): vol.Schema(
                            {RELAY_ID: SWITCH_SCHEMA}
                        ),
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

    # Merge options into entry data — options take precedence (set via configure)
    if entry.options:
        _LOGGER.debug("Using options over data for entry %s", entry.entry_id)

    coordinator = USR16Coordinator(hass, entry)
    await coordinator.async_connect()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, ["switch"])

    # Reload the entry when options are changed (host/port/password update)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    _LOGGER.debug("USR-R16 entry %s setup complete", entry.entry_id)
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options are updated."""
    _LOGGER.debug("Options updated for %s, reloading", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


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
        # Resolve source before super().__init__ to get scan_interval
        source = entry.options if entry.options else entry.data
        self._host = source[CONF_HOST]
        self._port = source[CONF_PORT]
        self._password = source[CONF_PASSWORD]
        self._scan_interval = source.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        self._address = f"{self._host}:{self._port}"

        super().__init__(
            hass,
            _LOGGER,
            name=f"USR-R16 {self._address}",
            # Periodic polling as fallback — push callbacks update data immediately
            update_interval=timedelta(seconds=self._scan_interval),
        )
        self._entry = entry
        self._client = None
        _LOGGER.debug(
            "Coordinator scan_interval=%s for %s", self._scan_interval, self._address
        )

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def async_connect(self) -> None:
        """Open the TCP connection and register push callbacks."""
        _LOGGER.debug("Connecting to USR-R16 at %s", self._address)

        self._client = USR16Client(
            host=self._host,
            port=self._port,
            password=self._password,
            disconnect_callback=self._on_disconnected,
            reconnect_callback=self._on_reconnected,
            timeout=CONNECTION_TIMEOUT,
            reconnect_interval=DEFAULT_RECONNECT_INTERVAL,
            keep_alive_interval=DEFAULT_KEEP_ALIVE_INTERVAL,
        )
        await self._client.setup()

        _LOGGER.info("Connected to USR-R16 at %s", self._address)

        # Register a push callback per relay — immediate updates on state change
        for port in range(1, RELAY_COUNT + 1):
            self._client.register_status_callback(
                self._make_relay_callback(str(port)), str(port)
            )
            _LOGGER.debug("Registered status callback for relay %s", port)

        # The lib's setup() calls status() internally which may return {} if a
        # 0xff heartbeat packet resolves the future first. The real 0x8a response
        # arrives shortly after and updates client.states directly. We wait a
        # short time then read client.states — if still empty we wait a bit more.
        await self._async_wait_for_states()

    async def _async_wait_for_states(self) -> None:
        """Wait for client.states to be populated then publish to entities.

        The lib sends a 0x0a status request during setup(). If a 0xff heartbeat
        arrives first it resolves the internal future with {} — but the device
        still sends the real 0x8a full-state response afterwards, which
        _handle_raw_packet processes and stores into client.states.
        We poll client.states with short sleeps until it is populated.
        """
        for attempt in range(20):  # up to 2 seconds total
            assert self._client is not None
            states = dict(self._client.states)
            if states:
                _LOGGER.debug(
                    "client.states populated after %dms: %s",
                    attempt * 100,
                    states,
                )
                self.async_set_updated_data(states)
                return
            _LOGGER.debug(
                "client.states empty, waiting 100ms (attempt %d/20)", attempt + 1
            )
            await asyncio.sleep(0.1)

        # Still empty after 2s — publish empty dict so entities show unknown
        # rather than blocking. Push callbacks will update them on next change.
        _LOGGER.warning(
            "client.states still empty after 2s for %s — relying on push callbacks",
            self._address,
        )
        self.async_set_updated_data({})

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
        if self._client is None:
            # Called during initial connect before self._client is assigned — ignore
            _LOGGER.debug("_on_reconnected ignored: client not yet assigned")
            return
        self.hass.async_create_task(self._async_refresh_after_reconnect())

    async def _async_refresh_after_reconnect(self) -> None:
        """Re-read client.states after a reconnect and push to all entities."""
        try:
            assert self._client is not None
            states = dict(self._client.states)
            _LOGGER.debug("States after reconnect: %s", states)  # pragma: no cover
            self.async_set_updated_data(states)  # pragma: no cover
        except Exception as err:  # pragma: no cover
            _LOGGER.error(
                "Failed to refresh states after reconnect: %s", err
            )  # pragma: no cover

    # ------------------------------------------------------------------
    # DataUpdateCoordinator required method (no scheduled polling)
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, bool]:
        """Return current relay states from client.states.

        We read client.states directly instead of calling status() because:
        - status() sends a 0x0a packet and waits for a response
        - if a 0xff heartbeat arrives before the 0x8a state response, the lib
          resolves the future with states={} (empty dict) — a lib bug
        - client.states is always kept up-to-date by _handle_raw_packet for
          every incoming 0x81-0x8a packet, including the setup() initial fetch
        """
        _LOGGER.debug("Manual data refresh for %s", self._address)
        if self._client is None:
            raise UpdateFailed("Client not connected")
        states = dict(self._client.states)
        _LOGGER.debug("States from client.states: %s", states)
        return states

    # ------------------------------------------------------------------
    # Relay control helpers (used by switch entities)
    # ------------------------------------------------------------------

    async def async_turn_on(self, port: str) -> None:
        """Close relay (ON)."""
        _LOGGER.debug("Turn ON relay %s on %s", port, self._address)
        assert self._client is not None
        await self._client.turn_on(port)

    async def async_turn_off(self, port: str) -> None:
        """Open relay (OFF)."""
        _LOGGER.debug("Turn OFF relay %s on %s", port, self._address)
        assert self._client is not None
        await self._client.turn_off(port)

    async def async_toggle(self, port: str) -> None:
        """Toggle relay."""
        _LOGGER.debug("Toggle relay %s on %s", port, self._address)
        assert self._client is not None
        await self._client.toggle(port)
