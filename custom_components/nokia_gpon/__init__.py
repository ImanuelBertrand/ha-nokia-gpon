"""Nokia GPON router (G-2426G-A) device tracker."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .coordinator import NokiaCoordinator

PLATFORMS = [Platform.DEVICE_TRACKER]

type NokiaConfigEntry = ConfigEntry[NokiaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: NokiaConfigEntry) -> bool:
    # 0.1.0 used the bare MAC as unique_id; prefix it with the entry id, keeping entity ids/settings.
    @callback
    def migrate_unique_id(reg_entry: er.RegistryEntry) -> dict | None:
        if not reg_entry.unique_id.startswith(f"{entry.entry_id}_"):
            return {"new_unique_id": f"{entry.entry_id}_{reg_entry.unique_id}"}
        return None

    await er.async_migrate_entries(hass, entry.entry_id, migrate_unique_id)

    coordinator = NokiaCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NokiaConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
