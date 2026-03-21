"""USR-R16 switch entities — one per relay channel."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import USR16Coordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

RELAY_COUNT = 16


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up USR-R16 relay switches from a config entry."""
    coordinator: USR16Coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [R16Switch(coordinator, str(port)) for port in range(1, RELAY_COUNT + 1)]
    _LOGGER.debug(
        "Adding %d relay switch entities for %s", len(entities), entry.entry_id
    )
    async_add_entities(entities)


class R16Switch(CoordinatorEntity[USR16Coordinator], SwitchEntity):  # type: ignore[misc]
    """Representation of a single USR-R16 relay channel."""

    _attr_translation_key = "relay"

    def __init__(self, coordinator: USR16Coordinator, port: str) -> None:
        """Initialize the relay switch."""
        super().__init__(coordinator)
        self._port = port
        self._attr_unique_id = f"{coordinator._entry.entry_id}_{port}"
        self._attr_name = f"{DOMAIN}_{port}"

        # Group all relays under a single device representing the USR-R16 board
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator._entry.entry_id)},
            name=f"USR-R16 {coordinator._host}",
            manufacturer="USR IOT",
            model="USR-R16",
            configuration_url=f"http://{coordinator._host}",
        )

        _LOGGER.debug(
            "Initialized R16Switch relay=%s unique_id=%s",
            self._port,
            self._attr_unique_id,
        )

    @property
    def is_on(self) -> bool | None:  # type: ignore[override]
        """Return True if the relay is closed (ON)."""
        if self.coordinator.data is None:
            _LOGGER.debug("Relay %s: coordinator data is None → unknown", self._port)
            return None
        state = self.coordinator.data.get(self._port)
        _LOGGER.debug("Relay %s: is_on=%s", self._port, state)
        return state

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        _LOGGER.debug(
            "Coordinator update received for relay %s: %s",
            self._port,
            self.coordinator.data.get(self._port) if self.coordinator.data else None,
        )
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        """Close the relay (turn ON)."""
        _LOGGER.debug("User action: turn ON relay %s", self._port)
        await self.coordinator.async_turn_on(self._port)

    async def async_turn_off(self, **kwargs) -> None:
        """Open the relay (turn OFF)."""
        _LOGGER.debug("User action: turn OFF relay %s", self._port)
        await self.coordinator.async_turn_off(self._port)

    async def async_toggle(self, **kwargs) -> None:
        """Toggle the relay state."""
        _LOGGER.debug("User action: toggle relay %s", self._port)
        await self.coordinator.async_toggle(self._port)
