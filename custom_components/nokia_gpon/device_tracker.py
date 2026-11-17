from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.device_tracker import ScannerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

if TYPE_CHECKING:  # type-only import: keeps older HA versions working
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NokiaConfigEntry
from .coordinator import NokiaCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: NokiaConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new() -> None:
        new = [mac for mac in coordinator.data if mac not in known]
        known.update(new)
        if new:
            async_add_entities(NokiaDeviceTracker(coordinator, entry.entry_id, mac) for mac in new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class NokiaDeviceTracker(CoordinatorEntity[NokiaCoordinator], ScannerEntity):
    """A client seen by the router. Devices the router forgets show as not home.

    Like other router integrations, trackers are created disabled unless Home
    Assistant already knows the MAC address; enable the ones you need.
    """

    def __init__(self, coordinator: NokiaCoordinator, entry_id: str, mac: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._unique_id = f"{entry_id}_{mac}"  # one router's MACs never collide with another's
        self._attr_name = coordinator.data[mac]["name"]

    @property
    def _device(self) -> dict | None:
        return self.coordinator.data.get(self._mac)

    @callback
    def _handle_coordinator_update(self) -> None:
        # follow alias changes on the router; keep the last name once the router forgets the device
        if self._device:
            self._attr_name = self._device["name"]
        super()._handle_coordinator_update()

    @property
    def unique_id(self) -> str:
        # ScannerEntity defines unique_id as the bare MAC, ignoring _attr_unique_id
        return self._unique_id

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
        # last_active is deliberately not exposed: it changes constantly and would
        # write a recorder row per tracker per poll
        return {
            "interface": d["interface"],
            "address_source": d["address_source"],
        }
