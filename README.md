# Nokia GPON Router for Home Assistant

Presence detection via a Nokia GPON home gateway: one `device_tracker` per
client the router knows, `home` while the router reports it as active.

The router has no API for this, so the integration logs into the web UI
(the same encrypted login the browser performs) and reads the LAN status page.

## Supported hardware

| Model | Firmware | Status |
|---|---|---|
| G-2426G-A | 3FE49226HJLI96 | tested |

Other Nokia/Alcatel-Lucent GPON gateways with the same web UI (encrypted
`login.cgi`, `lan_status.cgi?wlan`) may work, but are untested. Reports welcome.

## Installation (HACS custom repository)

1. HACS → ⋮ → **Custom repositories** → add
   `https://github.com/ImanuelBertrand/ha-nokia-gpon`, type **Integration**.
2. Install **Nokia GPON Router**, restart Home Assistant.
3. Settings → Devices & services → **Add integration** → *Nokia GPON Router*.
   Enter the router's address (default `192.168.1.254`), username
   (default `userAdmin`) and password.

Manual install: copy `custom_components/nokia_gpon/` into your
`/config/custom_components/` and restart.

## Behaviour

- Polls every 60 s. Each poll logs in, reads the device list and logs out again,
  so the router's admin UI is never blocked for long.
- Trackers are created **disabled** (Home Assistant's default for router
  integrations, so guests don't clutter your setup), unless Home Assistant
  already knows the device's MAC address from another integration. Enable the ones you need
  under Settings → Entities, filter by integration *Nokia GPON Router*.
  Devices that join later get a (disabled) tracker automatically; devices the
  router forgets show as `not_home`.
- Trackers are named after the router's device alias (if set) or hostname.
  Attributes: IP, MAC, hostname, `interface` (`wifi_2.4ghz`, `wifi_5ghz`,
  `ethernet`, `ethernet_10g`), `address_source`.
- If the router rejects the password, polling stops and Home Assistant asks for
  the new one. The router locks the account after a few failed logins (also
  ones typed in the browser); a locked account looks like a wrong password, so
  wait a few minutes before re-entering it. Each Home Assistant restart with a
  wrong stored password costs one more failed login. The router's response is
  logged as a warning to help with other firmware versions — **redact it before
  posting it anywhere**, other firmware may return pages with serial numbers or
  WAN details. Router errors (unreachable, 5xx) mark the update as failed and
  are retried; after 3 login attempts in a row answered with 5xx, polling
  stops and Home Assistant asks for the password, to avoid an account lockout.
- Phones that drop Wi-Fi while sleeping can briefly show `not_home`; add a delay
  in automations if that matters.

## Security

The router only offers a self-signed certificate. Instead of skipping TLS
verification, the integration pins the certificate's SHA-256 fingerprint at
setup (trust on first use). If the router later presents a different
certificate, the password is not sent. Home Assistant then shows the old and
new fingerprints and only continues once you explicitly trust the new
certificate. Expect this after a firmware update or factory reset; otherwise,
check your network first.

The command-line tool does not store a pin; it trusts the current certificate
on every run.

## Command-line tool

For debugging without Home Assistant (needs `requests` and `cryptography`):

```bash
python scripts/nokia_devices.py          # online devices
python scripts/nokia_devices.py --all    # include offline devices
python scripts/nokia_devices.py --json
```

Environment variables: `NOKIA_HOST`, `NOKIA_USER`, `NOKIA_PASS`
(otherwise prompts for the password; prefer the prompt, environment variables
end up in shell history).

## License

MIT, see [LICENSE](LICENSE).
