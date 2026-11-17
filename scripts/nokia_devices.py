#!/usr/bin/env python3
"""List devices connected to the Nokia G-2426G-A router (CLI for debugging).

Usage:
    python scripts/nokia_devices.py            # online devices
    python scripts/nokia_devices.py --all      # include offline ones
    python scripts/nokia_devices.py --json
    python scripts/nokia_devices.py --file saved_lan_status_page.html
    python scripts/nokia_devices.py --timing   # per-step durations (printed to stderr)

Uses the same client code as the Home Assistant integration
(custom_components/nokia_gpon/api.py). Env: NOKIA_HOST, NOKIA_USER, NOKIA_PASS.
"""

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

# import api.py directly, without the HA-dependent package __init__
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "nokia_gpon"))
import api  # noqa: E402


LABELS = {
    "GET /": "login page",
    "POST /login.cgi": "login",
    "GET /lan_status.cgi?wlan": "device list",
    "GET /login.cgi?out": "logout",
}


def fetch_timed(host: str, user: str, password: str) -> list[dict]:
    """Same steps as api.fetch_devices, timing each HTTP request (api.py stays untouched)."""
    steps: list[tuple[str, float]] = []

    t = time.perf_counter()
    fingerprint = api.get_fingerprint(host)
    steps.append(("certificate fingerprint (CLI only)", time.perf_counter() - t))

    router = api.NokiaRouter(host, user, password, fingerprint)
    request = router._request

    def timed_request(method, path, **kwargs):
        t = time.perf_counter()
        try:
            return request(method, path, **kwargs)
        finally:
            steps.append((LABELS.get(f"{method} {path}", f"{method} {path}"), time.perf_counter() - t))

    router._request = timed_request  # instance attribute shadows the method
    with router:
        html = router.lan_status_page()
        t = time.perf_counter()
        devices = api.parse_devices(html)
        steps.append(("parse device list", time.perf_counter() - t))
    # (logout runs in __exit__, so it is listed after parsing)

    poll = sum(d for name, d in steps if "CLI only" not in name)
    for name, d in steps:
        print(f"{name:36} {d * 1000:8.0f} ms", file=sys.stderr)
    print(f"{'= one Home Assistant poll':36} {poll * 1000:8.0f} ms", file=sys.stderr)
    return devices


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("NOKIA_HOST", "192.168.1.254"))
    ap.add_argument("--all", action="store_true", help="include offline devices")
    ap.add_argument("--json", action="store_true", help="print JSON")
    ap.add_argument("--file", help="parse a saved lan_status.cgi?wlan page instead of logging in")
    ap.add_argument("--timing", action="store_true", help="print how long each step takes (stderr)")
    args = ap.parse_args()

    if args.file:
        devices = api.parse_devices(Path(args.file).read_text(encoding="utf-8"))
    else:
        user = os.environ.get("NOKIA_USER", "userAdmin")
        password = os.environ.get("NOKIA_PASS") or getpass.getpass(f"password for {user}: ")
        if args.timing:
            devices = fetch_timed(args.host, user, password)
        else:
            # The CLI trusts whatever certificate the router presents now (no stored pin).
            fingerprint = api.get_fingerprint(args.host)
            devices = api.fetch_devices(args.host, user, password, fingerprint)

    online = [d for d in devices if d["online"]]
    shown = devices if args.all else online
    if args.json:
        print(json.dumps({"online_count": len(online), "devices": shown}, indent=2))
        return
    for d in sorted(shown, key=lambda d: (not d["online"], d["name"].lower())):
        state = "online " if d["online"] else "offline"
        print(f"{state}  {d['interface']:12} {d['ip']:15} {d['mac']}  {d['name']}")
    print(f"\n{len(online)} online / {len(devices)} known")


if __name__ == "__main__":
    main()
