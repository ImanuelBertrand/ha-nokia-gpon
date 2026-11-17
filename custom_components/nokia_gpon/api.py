"""Client for the Nokia G-2426G-A GPON router web UI (no Home Assistant imports).

Login is an RSA+AES encrypted POST to /login.cgi (port of the router's
js_glb/crypto_page.js); the device list is embedded as JavaScript in
/lan_status.cgi?wlan.

The router uses a self-signed certificate, so instead of CA validation the
certificate's SHA-256 fingerprint is pinned (trust on first use).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import socket
import ssl
from urllib.parse import quote, urlsplit

import requests
from requests.adapters import HTTPAdapter
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

INTERFACE_NAMES = {
    "802.11": "wifi_2.4ghz",
    "802.11ac": "wifi_5ghz",
    "Ethernet": "ethernet",
    "XGEthernet": "ethernet_10g",
}
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_LOGGER = logging.getLogger(__name__)


class NokiaError(Exception):
    """Base error."""


class CannotConnect(NokiaError):
    """Router unreachable, busy, or returned something unexpected (transient)."""


class InvalidAuth(NokiaError):
    """Router rejected the credentials."""


class LoginServerError(CannotConnect):
    """Router answered the login POST with a 5xx error."""


class CertificateMismatch(NokiaError):
    """Router presented a different TLS certificate than the pinned one."""


def normalize_host(host: str) -> str:
    """Accept '192.168.1.254', 'https://192.168.1.254/', 'router:8443' etc."""
    raw = host.strip()
    parts = urlsplit(raw if "://" in raw else "https://" + raw)
    try:
        hostname, port = parts.hostname, parts.port
    except ValueError as err:  # e.g. non-numeric port, unbracketed IPv6
        raise CannotConnect(f"invalid host {raw!r}") from err
    if not hostname or parts.username or parts.password:
        raise CannotConnect(f"invalid host {raw!r}")
    if ":" in hostname:  # IPv6 literal
        hostname = f"[{hostname}]"
    return f"{hostname}:{port}" if port else hostname


def format_fingerprint(fingerprint: str) -> str:
    """'ab12cd…' -> 'AB:12:CD:…' for display."""
    if not fingerprint:
        return "(none)"
    return ":".join(fingerprint[i : i + 2] for i in range(0, len(fingerprint), 2)).upper()


def get_fingerprint(host: str, timeout: float = 10) -> str:
    """SHA-256 fingerprint (hex) of the certificate the router presents right now."""
    parts = urlsplit("https://" + normalize_host(host))
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((parts.hostname, parts.port or 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=parts.hostname) as tls:
                der = tls.getpeercert(binary_form=True)
    except OSError as err:
        raise CannotConnect(str(err)) from err
    return hashlib.sha256(der).hexdigest()


# ---------------------------------------------------------------- crypto
def _b64url_escape(s: str) -> str:
    return s.replace("+", "-").replace("/", "_").replace("=", ".")


def _encrypt_post_data(pubkey_pem: str, plaintext: str) -> dict:
    try:
        pub = serialization.load_pem_public_key(pubkey_pem.encode())
    except Exception as err:  # ValueError, UnsupportedAlgorithm, ...
        raise CannotConnect(f"invalid public key on login page: {err}") from err
    if not isinstance(pub, rsa.RSAPublicKey):
        raise CannotConnect("public key on login page is not RSA")
    key, iv = os.urandom(16), os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct = enc.update(data) + enc.finalize()
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
class _PinnedAdapter(HTTPAdapter):
    """Skips CA validation but requires the exact pinned certificate."""

    def __init__(self, fingerprint: str):
        self._fingerprint = fingerprint
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        kwargs["assert_fingerprint"] = self._fingerprint
        return super().init_poolmanager(*args, **kwargs)


class NokiaRouter:
    def __init__(self, host: str, user: str, password: str, fingerprint: str, timeout: float = 10):
        if not fingerprint:
            raise ValueError("a certificate fingerprint is required")  # never talk to an unpinned router
        self.base = f"https://{normalize_host(host)}"
        self.user, self.password, self.timeout = user, password, timeout
        self.s = requests.Session()
        self.s.verify = False  # self-signed; replaced by fingerprint pinning below
        self.s.trust_env = False  # never go through HTTP(S)_PROXY
        self.s.mount("https://", _PinnedAdapter(fingerprint))

    def _request(self, method: str, path: str, **kwargs) -> tuple[int, str]:
        try:
            with self.s.request(method, self.base + path, timeout=self.timeout, stream=True, **kwargs) as r:
                body, size = [], 0
                for chunk in r.iter_content(65536):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise CannotConnect(f"response from {path} exceeds {MAX_RESPONSE_BYTES} bytes")
                    body.append(chunk)
                return r.status_code, b"".join(body).decode(r.encoding or "utf-8", "replace")
        except requests.exceptions.SSLError as err:
            if "fingerprint" in str(err).lower():
                raise CertificateMismatch("router certificate changed") from err
            raise CannotConnect(str(err)) from err
        except requests.RequestException as err:
            raise CannotConnect(str(err)) from err

    def login(self) -> None:
        _, html = self._request("GET", "/")
        pubkey = _find(r"var pubkey = '(.*?)';", html, "pubkey").replace("\\\n", "\n").replace("\\", "")
        nonce = _find(r'var nonce = "(.*?)"', html, "nonce")
        token = _find(r'var token ?= ?"(.*?)"', html, "csrf token")
        enckey = _b64url_escape(base64.b64encode(os.urandom(16)).decode())
        enciv = _b64url_escape(base64.b64encode(os.urandom(16)).decode())
        password = quote(self.password, safe="!*'()")  # same as JS encodeURIComponent
        postdata = (
            f"&username={self.user}&password={password}"
            f"&csrf_token={token}&nonce={nonce}&enckey={enckey}&enciv={enciv}"
        )
        status, body = self._request(
            "POST",
            "/login.cgi",
            data=_encrypt_post_data(pubkey, postdata),
            headers={"X-Requested-With": "XMLHttpRequest", "Origin": self.base},
            allow_redirects=False,
        )
        if status == 299:
            return
        # Logged so users can report what a rejected / locked / busy login looks like on their firmware.
        snippet = re.sub(r"\s+", " ", re.sub(r"<(script|style)\b.*?</\1>|<[^>]+>", " ", body, flags=re.S | re.I)).strip()
        _LOGGER.warning("Router refused login: HTTP %s, response text: %.300s", status, snippet)
        # A rejected login answers 200 with a page instead of 299. Treat every 2xx/4xx as rejected:
        # retrying a wrong password would lock the account. Only 5xx (rebooting, overloaded) is transient.
        if status >= 500:
            raise LoginServerError(f"router error during login (HTTP {status})")
        raise InvalidAuth(f"router rejected the login (HTTP {status})")

    def logout(self) -> None:
        try:
            self._request("GET", "/login.cgi?out")
        except NokiaError:
            pass

    def lan_status_page(self) -> str:
        status, body = self._request("GET", "/lan_status.cgi?wlan")
        if status != 200:
            raise CannotConnect(f"lan_status.cgi returned HTTP {status}")
        return body

    def __enter__(self):
        try:
            self.login()
        except BaseException:
            self.s.close()
            raise
        return self

    def __exit__(self, *exc):
        try:
            self.logout()
        finally:
            self.s.close()


def fetch_devices(host: str, user: str, password: str, fingerprint: str) -> list[dict]:
    """Log in, read the device list, log out. Blocking."""
    with NokiaRouter(host, user, password, fingerprint) as router:
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


def _js_var(html: str, name: str) -> list:
    m = re.search(rf"var {name} = (\[.*?\]);\s*\n", html, re.S)
    if not m:
        raise CannotConnect(f"variable {name} not found in lan_status page")
    value = json.loads(_js_to_json(m.group(1)))
    if not isinstance(value, list):
        raise CannotConnect(f"variable {name} is not a list")
    return value


def _str(d: dict, key: str) -> str:
    v = d.get(key)
    return "" if v is None else str(v).strip()


def parse_devices(html: str) -> list[dict]:
    try:
        aliases = {
            _str(a, "MACAddress").lower(): _str(a, "HostAlias")
            for a in _js_var(html, "alias_cfg")
            if isinstance(a, dict)
        }
        devices = []
        for d in _js_var(html, "device_cfg"):
            if not isinstance(d, dict):
                continue
            mac = _str(d, "MACAddress").lower()
            if not _MAC_RE.match(mac):
                continue
            iface = _str(d, "InterfaceType")
            devices.append({
                "name": aliases.get(mac) or _str(d, "HostName") or mac,
                "hostname": _str(d, "HostName"),
                "ip": _str(d, "IPAddress"),
                "mac": mac,
                "online": str(d.get("Active")) == "1" or d.get("Active") is True,
                "interface": INTERFACE_NAMES.get(iface, iface),
                "address_source": _str(d, "AddressSource"),
                "lease_remaining_s": d.get("LeaseTimeRemaining"),
                "last_active": _str(d, "X_ALU_COM_LastActiveTime"),
            })
    except (ValueError, KeyError, TypeError, AttributeError) as err:  # JSONDecodeError is a ValueError
        raise CannotConnect(f"could not parse device list: {err}") from err
    return devices
