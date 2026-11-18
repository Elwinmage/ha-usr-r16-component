"""Constants for USR-R16 component."""

DOMAIN = "usr_r16"

DEFAULT_NAME = "USR-R16"
DEFAULT_PORT = 8899
DEFAULT_RECONNECT_INTERVAL = 10
DEFAULT_KEEP_ALIVE_INTERVAL = 3
CONNECTION_TIMEOUT = 10
# Startup wait for the initial relay bitmap: 50 x 100 ms = 5 s (> keepalive)
STATE_WAIT_ATTEMPTS = 50
DEFAULT_PASSWORD = "admin"
DEFAULT_SCAN_INTERVAL = 5  # seconds
CONF_SCAN_INTERVAL = "scan_interval"
