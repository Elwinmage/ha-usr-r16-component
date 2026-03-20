"""Support for USR-R16 relay switches."""

import asyncio
import logging

from usr_r16 import create_usr_r16_client_connection
import voluptuous as vol

from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SWITCHES,
)
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.dispatcher import (
    async_dispatcher_send,
    async_dispatcher_connect,
)
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONNECTION_TIMEOUT,
    DEFAULT_KEEP_ALIVE_INTERVAL,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_RECONNECT_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

DATA_DEVICE_REGISTER = "usr_r16_device_register"
DATA_DEVICE_LISTENER = "usr_r16_device_listener"

SWITCH_SCHEMA = vol.Schema({vol.Optional(CONF_NAME): cv.string})

RELAY_ID = vol.All(
    vol.Any(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16),
    vol.Coerce(str),
)

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
    """Component setup, do nothing."""
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
    """Set up the USR-R16 switch."""
    hass.data.setdefault(DOMAIN, {})
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    password = entry.data[CONF_PASSWORD]
    address = f"{host}:{port}"

    hass.data[DOMAIN][entry.entry_id] = {}

    @callback
    def disconnected() -> None:
        """Dispatch unavailability when the TCP connection is lost."""
        _LOGGER.warning("USR-R16 %s disconnected", address)
        async_dispatcher_send(hass, f"usr_r16_device_available_{entry.entry_id}", False)

    @callback
    def reconnected() -> None:
        """Dispatch availability when the TCP connection is restored."""
        _LOGGER.info("USR-R16 %s connected", address)
        async_dispatcher_send(hass, f"usr_r16_device_available_{entry.entry_id}", True)

    async def connect() -> None:
        """Set up the TCP connection and register it in hass.data."""
        _LOGGER.info("Initiating USR-R16 connection to %s", address)

        client = await create_usr_r16_client_connection(
            host=host,
            port=port,
            password=password,
            disconnect_callback=disconnected,
            reconnect_callback=reconnected,
            loop=asyncio.get_event_loop(),
            timeout=CONNECTION_TIMEOUT,
            reconnect_interval=DEFAULT_RECONNECT_INTERVAL,
            keep_alive_interval=DEFAULT_KEEP_ALIVE_INTERVAL,
        )

        hass.data[DOMAIN][entry.entry_id][DATA_DEVICE_REGISTER] = client

        await hass.config_entries.async_forward_entry_setups(entry, ["switch"])

        _LOGGER.info("Connected to USR-R16 device: %s", address)

    # Schedule the coroutine without blocking async_setup_entry
    hass.async_create_task(connect())

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    client = hass.data[DOMAIN][entry.entry_id].pop(DATA_DEVICE_REGISTER)
    client.stop()
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "switch")

    if unload_ok:
        if not hass.data[DOMAIN][entry.entry_id]:
            hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    return unload_ok


class R16Device(Entity):
    """Base class for USR-R16 entities.

    Uses _attr_* attributes instead of @property overrides to stay
    compatible with HA's cached_property declarations (avoids pyright
    reportIncompatibleVariableOverride errors).
    """

    # Disable polling — the device pushes state changes via callbacks
    _attr_should_poll = False

    def __init__(self, device_port: int | str, entry_id: str, client) -> None:
        """Initialize the device."""
        self._entry_id = entry_id
        self._device_port = str(device_port)
        self._client = client

        # Set standard HA _attr_ fields
        self._attr_unique_id = f"{entry_id}_{device_port}"
        self._attr_name = f"{DOMAIN}_{device_port}"
        self._attr_available = bool(client.is_connected)

        # Internal relay state (True = ON, False = OFF, None = unknown)
        self._is_on: bool | None = None

    @callback
    def handle_event_callback(self, event: bool) -> None:
        """Handle relay state change pushed by the device."""
        _LOGGER.info("Relay %s new state callback: %r", self._attr_unique_id, event)
        self._is_on = event
        self.async_write_ha_state()

    @callback
    def _availability_callback(self, availability: bool) -> None:
        """Update entity availability when TCP connection state changes."""
        self._attr_available = availability
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register callbacks once the entity is added to HA."""
        # Register relay-state callback for this specific port
        self._client.register_status_callback(
            self.handle_event_callback, self._device_port
        )

        # Fetch initial relay state
        self._is_on = await self._client.status(self._device_port)

        # Subscribe to connection availability dispatches
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"usr_r16_device_available_{self._entry_id}",
                self._availability_callback,
            )
        )
