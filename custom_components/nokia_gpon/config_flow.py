from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from .api import (
    CannotConnect,
    CertificateMismatch,
    InvalidAuth,
    NokiaError,
    fetch_devices,
    format_fingerprint,
    get_fingerprint,
    normalize_host,
)
from .const import CONF_FINGERPRINT, DEFAULT_HOST, DEFAULT_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_TRUST_CERT = "trust_new_certificate"


class NokiaConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    _new_fingerprint: str | None = None

    async def _validate(self, data: Mapping[str, Any], fingerprint: str) -> str | None:
        """Full login + fetch against the given certificate pin; return an error key or None."""
        try:
            await self.hass.async_add_executor_job(
                fetch_devices, data[CONF_HOST], data[CONF_USERNAME], data[CONF_PASSWORD], fingerprint
            )
        except InvalidAuth:
            return "invalid_auth"
        except CertificateMismatch:
            return "certificate_changed"
        except NokiaError:
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error while validating the router login")
            return "unknown"
        return None

    async def _current_fingerprint(self, host: str) -> str | None:
        try:
            return await self.hass.async_add_executor_job(get_fingerprint, host)
        except NokiaError:
            return None

    # ------------------------------------------------------------ initial setup
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                user_input[CONF_HOST] = normalize_host(user_input[CONF_HOST])
            except CannotConnect:
                errors[CONF_HOST] = "invalid_host"
            else:
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()
                # Trust on first use: pin whatever certificate the router presents now.
                if not (fingerprint := await self._current_fingerprint(user_input[CONF_HOST])):
                    errors["base"] = "cannot_connect"
                elif not (error := await self._validate(user_input, fingerprint)):
                    return self.async_create_entry(
                        title=f"Nokia router ({user_input[CONF_HOST]})",
                        data={**user_input, CONF_FINGERPRINT: fingerprint},
                    )
                else:
                    errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=self.add_suggested_values_to_schema(schema, user_input), errors=errors
        )

    # ------------------------------------------------------------ reauth
    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Password rejected or certificate changed: find out which."""
        entry = self._get_reauth_entry()
        current = await self._current_fingerprint(entry.data[CONF_HOST])
        if current and current != entry.data.get(CONF_FINGERPRINT):
            self._new_fingerprint = current
            return await self.async_step_reauth_certificate()
        # Same certificate, or the router is unreachable right now (the password form then
        # retries; an entry without a pin is routed to the certificate step on submit).
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Same certificate as before: only ask for the password, keep the pin."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            if pin := entry.data.get(CONF_FINGERPRINT):
                error = await self._validate({**entry.data, **user_input}, pin)
            else:
                error = "certificate_changed"  # nothing pinned yet: the user must confirm one
            if error == "certificate_changed":
                # changed between opening the form and submitting it
                self._new_fingerprint = await self._current_fingerprint(entry.data[CONF_HOST])
                if self._new_fingerprint:
                    return await self.async_step_reauth_certificate()
                error = "cannot_connect"
            if not error:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    async def async_step_reauth_certificate(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Certificate changed: show old and new fingerprint, require explicit trust."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        assert self._new_fingerprint
        if user_input is not None:
            if not user_input.get(CONF_TRUST_CERT):
                errors[CONF_TRUST_CERT] = "certificate_not_trusted"
            else:
                # Pin exactly the certificate the user was shown; if the router now presents
                # yet another one, the login fails instead of silently trusting it.
                error = await self._validate(
                    {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}, self._new_fingerprint
                )
                if not error:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                            CONF_FINGERPRINT: self._new_fingerprint,
                        },
                    )
                if error == "certificate_changed" and (
                    newer := await self._current_fingerprint(entry.data[CONF_HOST])
                ):
                    # Changed again while the form was open: nothing is pinned; the form is
                    # shown again with the new fingerprint and must be submitted again.
                    self._new_fingerprint = newer
                    error = "certificate_changed_again"
                errors["base"] = error
        return self.async_show_form(
            step_id="reauth_certificate",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TRUST_CERT, default=False): bool,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            description_placeholders={
                "host": entry.data[CONF_HOST],
                "router_url": f"https://{entry.data[CONF_HOST]}",
                "old_fingerprint": format_fingerprint(entry.data.get(CONF_FINGERPRINT) or ""),
                "new_fingerprint": format_fingerprint(self._new_fingerprint),
            },
            errors=errors,
        )
