from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CertificateMismatch, InvalidAuth, LoginServerError, NokiaError, fetch_devices
from .const import CONF_FINGERPRINT, DOMAIN, SCAN_INTERVAL

# If the router keeps answering the login with 5xx, it may be rejecting the password that
# way; stop and ask instead of retrying into an account lockout.
MAX_LOGIN_SERVER_ERRORS = 3

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
        self._login_server_errors = 0

    async def _async_update_data(self) -> dict[str, dict]:
        data = self.config_entry.data
        if not (fingerprint := data.get(CONF_FINGERPRINT)):
            # Entry from a version without certificate pinning: let the user confirm the
            # certificate in the reauth flow instead of trusting it silently.
            raise ConfigEntryAuthFailed("no router certificate pinned yet")
        try:
            devices = await self.hass.async_add_executor_job(
                fetch_devices, data[CONF_HOST], data[CONF_USERNAME], data[CONF_PASSWORD], fingerprint
            )
        except InvalidAuth as err:
            # Stop polling (the router locks out after repeated failures) and ask for a new password.
            raise ConfigEntryAuthFailed(str(err)) from err
        except CertificateMismatch as err:
            # Possible man-in-the-middle, or the router regenerated its certificate:
            # never send the password; let the user confirm via the reauth flow.
            raise ConfigEntryAuthFailed(
                "router TLS certificate changed; re-enter the password to trust the new certificate"
            ) from err
        except LoginServerError as err:
            self._login_server_errors += 1
            if self._login_server_errors >= MAX_LOGIN_SERVER_ERRORS:
                self._login_server_errors = 0
                raise ConfigEntryAuthFailed(
                    f"router answered {MAX_LOGIN_SERVER_ERRORS} logins in a row with a server error"
                ) from err
            raise UpdateFailed(str(err)) from err
        except NokiaError as err:
            raise UpdateFailed(str(err)) from err
        self._login_server_errors = 0
        return {d["mac"]: d for d in devices}
