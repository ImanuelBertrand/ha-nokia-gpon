from datetime import timedelta

DOMAIN = "nokia_gpon"
DEFAULT_HOST = "192.168.1.254"
DEFAULT_USERNAME = "userAdmin"
CONF_FINGERPRINT = "cert_fingerprint"
# Each poll logs in and out again, so the browser UI is never locked out for long.
SCAN_INTERVAL = timedelta(seconds=60)
