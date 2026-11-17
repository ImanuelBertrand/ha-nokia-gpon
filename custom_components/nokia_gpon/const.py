from datetime import timedelta

DOMAIN = "nokia_gpon"
DEFAULT_HOST = "192.168.1.254"
DEFAULT_USERNAME = "userAdmin"
CONF_FINGERPRINT = "cert_fingerprint"
SCAN_INTERVAL = timedelta(seconds=60)
# The router allows one session per account. When another login (the web UI) takes it
# over, wait this long before logging in again, which would kick that user out.
SESSION_LOST_PAUSE = timedelta(minutes=15)
