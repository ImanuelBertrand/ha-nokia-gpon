from __future__ import annotations

from homeassistant.components.device_tracker import ScannerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import NokiaConfigEntry
from .coordinator import NokiaCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: NokiaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new() -> None:
        new = [mac for mac in coordinator.data if mac not in known]
        known.update(new)
        if new:
            async_add_entities(NokiaDeviceTracker(coordinator, mac) for mac in new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class NokiaDeviceTracker(CoordinatorEntity[NokiaCoordinator], ScannerEntity):
    """A client seen by the router. Devices the router forgets show as not home."""

    def __init__(self, coordinator: NokiaCoordinator, mac: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = mac
        self._attr_name = coordinator.data[mac]["name"]

    @property
    def _device(self) -> dict | None:
        return self.coordinator.data.get(self._mac)

    @property
    def entity_registry_enabled_default(self) -> bool:
        # ScannerEntity disables unknown MACs by default; this is a small household, enable all.
        return True

    @property
    def is_connected(self) -> bool:
        return bool(self._device and self._device["online"])

    @property
    def mac_address(self) -> str:
        return self._mac

    @property
    def ip_address(self) -> str | None:
        return self._device["ip"] if self._device else None

    @property
    def hostname(self) -> str | None:
        return self._device["hostname"] if self._device else None

    @property
    def extra_state_attributes(self) -> dict:
        d = self._device
        if not d:
            return {}
        return {
            "interface": d["interface"],
            "address_source": d["address_source"],
            "last_active": d["last_active"],
        }
