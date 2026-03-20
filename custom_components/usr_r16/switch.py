"""Support for USR-R16 switches."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DATA_DEVICE_REGISTER, R16Device
from .const import DOMAIN


def devices_from_entities(hass: HomeAssistant, entry: ConfigEntry) -> list["R16Switch"]:
    """Instantiate one R16Switch per relay channel (1-16)."""
    device_client = hass.data[DOMAIN][entry.entry_id][DATA_DEVICE_REGISTER]
    return [R16Switch(port, entry.entry_id, device_client) for port in range(1, 17)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the USR-R16 switch platform."""
    async_add_entities(devices_from_entities(hass, entry))


class R16Switch(R16Device, SwitchEntity):
    """Representation of a single USR-R16 relay channel."""

    _attr_translation_key = "relay"

    # _attr_is_on is inherited from SwitchEntity/_attr_* pattern;
    # we shadow _is_on with _attr_is_on to avoid @property override issues.

    @callback
    def handle_event_callback(self, event: bool) -> None:
        """Override parent to also update _attr_is_on."""
        self._attr_is_on = event
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        """Close the relay (turn ON)."""
        await self._client.turn_on(self._device_port)

    async def async_turn_off(self, **kwargs) -> None:
        """Open the relay (turn OFF)."""
        await self._client.turn_off(self._device_port)

    async def async_toggle(self, **kwargs) -> None:
        """Toggle the relay state."""
        await self._client.toggle(self._device_port)

    async def async_added_to_hass(self) -> None:
        """Register callbacks and fetch initial state."""
        # Register relay-state callback for this specific port
        self._client.register_status_callback(
            self.handle_event_callback, self._device_port
        )

        # Fetch ALL relay states at once to avoid a race condition:
        # calling status(port) concurrently for 16 entities can trigger a KeyError
        # if a single-relay push response resolves a waiter before the full-state
        # response arrives. status() with no arg always returns the complete dict.
        all_states = await self._client.status()
        if isinstance(all_states, dict):
            self._attr_is_on = all_states.get(self._device_port)
        else:
            self._attr_is_on = None

        # Subscribe to connection availability dispatches
        from homeassistant.helpers.dispatcher import async_dispatcher_connect

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"usr_r16_device_available_{self._entry_id}",
                self._availability_callback,
            )
        )
