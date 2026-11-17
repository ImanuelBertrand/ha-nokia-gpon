from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import InvalidAuth, NokiaError, fetch_devices
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class NokiaCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Polls the router; data maps MAC address -> device dict."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, dict]:
        data = self.config_entry.data
        try:
            devices = await self.hass.async_add_executor_job(
                fetch_devices, data[CONF_HOST], data[CONF_USERNAME], data[CONF_PASSWORD]
            )
        except InvalidAuth as err:
            # Stops polling (the router locks out after repeated failures) and asks for a new password.
            raise ConfigEntryAuthFailed(str(err)) from err
        except NokiaError as err:
            raise UpdateFailed(str(err)) from err
        return {d["mac"]: d for d in devices}
