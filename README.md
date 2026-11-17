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
- Devices that join later get a tracker automatically; devices the router
  forgets show as `not_home`.
- Trackers are named after the router's device alias (if set) or hostname.
  Attributes: IP, MAC, hostname, `interface` (`wifi_2.4ghz`, `wifi_5ghz`,
  `ethernet`), `address_source`, `last_active`.
- If the router rejects the password, polling stops and Home Assistant asks for
  the new one (the router locks logins after a few failed attempts).
- Phones that drop Wi-Fi while sleeping can briefly show `not_home`; add a delay
  in automations if that matters.

## Command-line tool

For debugging without Home Assistant (needs `requests` and `cryptography`):

```bash
python scripts/nokia_devices.py          # online devices
python scripts/nokia_devices.py --all    # include offline devices
python scripts/nokia_devices.py --json
```

Environment variables: `NOKIA_HOST`, `NOKIA_USER`, `NOKIA_PASS`
(otherwise prompts for the password).

## License

MIT, see [LICENSE](LICENSE).
