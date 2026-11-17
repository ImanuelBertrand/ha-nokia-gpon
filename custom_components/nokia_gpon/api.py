"""Client for the Nokia G-2426G-A GPON router web UI (no Home Assistant imports).

Login is an RSA+AES encrypted POST to /login.cgi (port of the router's
js_glb/crypto_page.js); the device list is embedded as JavaScript in
/lan_status.cgi?wlan.
"""

from __future__ import annotations

import base64
import json
import os
import re
from urllib.parse import quote

import requests
import urllib3
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

INTERFACE_NAMES = {
    "802.11": "wifi_2.4ghz",
    "802.11ac": "wifi_5ghz",
    "Ethernet": "ethernet",
    "XGEthernet": "ethernet_10g",
}


class NokiaError(Exception):
    """Base error."""


class CannotConnect(NokiaError):
    """Router unreachable or returned something unexpected."""


class InvalidAuth(NokiaError):
    """Router rejected the credentials."""


# ---------------------------------------------------------------- crypto
def _b64url_escape(s: str) -> str:
    return s.replace("+", "-").replace("/", "_").replace("=", ".")


def _encrypt_post_data(pubkey_pem: str, plaintext: str) -> dict:
    key, iv = os.urandom(16), os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct = enc.update(data) + enc.finalize()
    pub = serialization.load_pem_public_key(pubkey_pem.encode())
    aesinfo = f"{base64.b64encode(key).decode()} {base64.b64encode(iv).decode()}"
    ck = pub.encrypt(aesinfo.encode(), rsa_padding.PKCS1v15())
    return {
        "encrypted": "1",
        # sjcl base64url: unpadded, -/_ alphabet
        "ct": base64.urlsafe_b64encode(ct).decode().rstrip("="),
        "ck": _b64url_escape(base64.b64encode(ck).decode()),
    }


def _find(pattern: str, html: str, name: str) -> str:
    m = re.search(pattern, html, re.S)
    if not m:
        raise CannotConnect(f"could not find {name} on login page")
    return m.group(1)


# ---------------------------------------------------------------- client
class NokiaRouter:
    def __init__(self, host: str, user: str, password: str, timeout: float = 10):
        self.base = f"https://{host}"
        self.user, self.password, self.timeout = user, password, timeout
        self.s = requests.Session()
        self.s.verify = False  # self-signed certificate
        self.s.trust_env = False  # never go through HTTP(S)_PROXY

    def _get(self, path: str) -> requests.Response:
        try:
            return self.s.get(self.base + path, timeout=self.timeout)
        except requests.RequestException as err:
            raise CannotConnect(str(err)) from err

    def login(self) -> None:
        html = self._get("/").text
        pubkey = _find(r"var pubkey = '(.*?)';", html, "pubkey").replace("\\\n", "\n").replace("\\", "")
        nonce = _find(r'var nonce = "(.*?)"', html, "nonce")
        token = _find(r'var token ?= ?"(.*?)"', html, "csrf token")
        enckey = _b64url_escape(base64.b64encode(os.urandom(16)).decode())
        enciv = _b64url_escape(base64.b64encode(os.urandom(16)).decode())
        postdata = (
            f"&username={self.user}&password={quote(self.password, safe='')}"
            f"&csrf_token={token}&nonce={nonce}&enckey={enckey}&enciv={enciv}"
        )
        try:
            r = self.s.post(
                self.base + "/login.cgi",
                data=_encrypt_post_data(pubkey, postdata),
                headers={"X-Requested-With": "XMLHttpRequest", "Origin": self.base},
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as err:
            raise CannotConnect(str(err)) from err
        if r.status_code != 299:
            raise InvalidAuth(f"login rejected (HTTP {r.status_code})")

    def logout(self) -> None:
        try:
            self.s.get(self.base + "/login.cgi?out", timeout=self.timeout)
        except requests.RequestException:
            pass

    def lan_status_page(self) -> str:
        return self._get("/lan_status.cgi?wlan").text

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *exc):
        self.logout()
        self.s.close()


def fetch_devices(host: str, user: str, password: str) -> list[dict]:
    """Log in, read the device list, log out. Blocking."""
    with NokiaRouter(host, user, password) as router:
        return parse_devices(router.lan_status_page())


# ---------------------------------------------------------------- parsing
_TOKEN = re.compile(
    r"""'((?:[^'\\]|\\.)*)'"""            # single-quoted string
    r"""|("(?:[^"\\]|\\.)*")"""           # double-quoted string
    r"""|([{,]\s*)([A-Za-z_]\w*)(\s*:)"""  # bare object key
)


def _js_to_json(src: str) -> str:
    def repl(m):
        if m.group(1) is not None:
            return json.dumps(m.group(1).replace("\\'", "'"))
        if m.group(2) is not None:
            return m.group(2)
        return f'{m.group(3)}"{m.group(4)}"{m.group(5)}'

    return _TOKEN.sub(repl, src)


def _js_var(html: str, name: str):
    m = re.search(rf"var {name} = (\[.*?\]);\s*\n", html, re.S)
    if not m:
        raise CannotConnect(f"variable {name} not found in lan_status page")
    return json.loads(_js_to_json(m.group(1)))


def parse_devices(html: str) -> list[dict]:
    aliases = {a["MACAddress"].lower(): (a.get("HostAlias") or "").strip() for a in _js_var(html, "alias_cfg")}
    devices = []
    for d in _js_var(html, "device_cfg"):
        mac = d.get("MACAddress", "").lower()
        if not mac:
            continue
        devices.append({
            "name": aliases.get(mac) or d.get("HostName", "") or mac,
            "hostname": d.get("HostName", ""),
            "ip": d.get("IPAddress", ""),
            "mac": mac,
            "online": bool(d.get("Active")),
            "interface": INTERFACE_NAMES.get(d.get("InterfaceType", ""), d.get("InterfaceType", "")),
            "address_source": d.get("AddressSource", ""),
            "lease_remaining_s": d.get("LeaseTimeRemaining"),
            "last_active": d.get("X_ALU_COM_LastActiveTime", ""),
        })
    return devices
