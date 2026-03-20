"""Config flow for USR-R16."""
import asyncio
import socket

import voluptuous as vol
from usr_r16 import create_usr_r16_client_connection

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant

from .const import (
    CONNECTION_TIMEOUT,
    DEFAULT_KEEP_ALIVE_INTERVAL,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_RECONNECT_INTERVAL,
    DOMAIN,
)
from .errors import AlreadyConfigured, CannotConnect

# ---- Discovery constants ---------------------------------------------------
UDP_DISCOVERY_PORT = 1901
UDP_DISCOVERY_MSG  = bytes.fromhex("ff010102")
UDP_DISCOVERY_TIMEOUT = 3.0   # seconds to wait for UDP replies
TCP_SCAN_TIMEOUT   = 1.0      # seconds per TCP probe
TCP_SCAN_WORKERS   = 50       # parallel TCP connections

# Sentinel value shown in the select list to trigger manual entry
MANUAL_ENTRY = "__manual__"

# ---- Form schemas ----------------------------------------------------------
SCHEMA_MANUAL = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
        vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
    }
)

SCHEMA_INIT = vol.Schema(
    {
        vol.Required("discovery_method"): vol.In(["auto", "manual"]),
    }
)


# ---- Helpers ---------------------------------------------------------------

def _get_local_subnet() -> str:
    """Detect the local /24 subnet from the outbound interface."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        parts = local_ip.rsplit(".", 1)
        return f"{parts[0]}.0/24"
    except Exception:
        return "192.168.1.0/24"


def _is_already_configured(hass: HomeAssistant, host: str, port: int) -> bool:
    """Return True if a config entry already exists for host:port."""
    return any(
        entry.data[CONF_HOST] == host and entry.data[CONF_PORT] == port
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


async def _discover_udp(timeout: float) -> list[dict]:
    """
    Send a USR UDP broadcast and collect replies.
    Returns a list of dicts with keys: host, name.
    """
    loop = asyncio.get_event_loop()
    found: dict[str, dict] = {}

    class _Proto(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            sock = transport.get_extra_info("socket")
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            transport.sendto(UDP_DISCOVERY_MSG, ("<broadcast>", UDP_DISCOVERY_PORT))

        def datagram_received(self, data, addr):
            if data == UDP_DISCOVERY_MSG:
                return
            ip = addr[0]
            name = ""
            if len(data) >= 35 and data[0] == 0xFF and data[1] == len(data):
                ip = f"{data[5]}.{data[6]}.{data[7]}.{data[8]}"
                name = data[19:35].decode("ascii", errors="replace").rstrip("\x00").strip()
            if ip not in found:
                found[ip] = {"host": ip, "name": name}

        def error_received(self, exc):
            pass

    transport = None
    try:
        transport, _ = await loop.create_datagram_endpoint(
            _Proto,
            local_addr=("0.0.0.0", 0),
            family=socket.AF_INET,
        )
        await asyncio.sleep(timeout)
    except OSError:
        pass
    finally:
        if transport:
            transport.close()

    return list(found.values())


async def _probe_tcp(ip: str, port: int, timeout: float) -> str | None:
    """Return ip if port is open, else None."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=timeout
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return ip
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return None


async def _discover_tcp(subnet: str, port: int, timeout: float, workers: int) -> list[dict]:
    """Scan subnet for open TCP port. Returns list of dicts with key: host."""
    import ipaddress
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        return []

    semaphore = asyncio.Semaphore(workers)

    async def probe(ip):
        async with semaphore:
            return await _probe_tcp(str(ip), port, timeout)

    results = await asyncio.gather(*[probe(h) for h in network.hosts()])
    return [{"host": ip, "name": ""} for ip in results if ip is not None]


async def discover_devices(hass: HomeAssistant) -> list[dict]:
    """
    Run UDP broadcast then TCP scan on the local subnet.
    Merge results and return list of dicts: {host, name}.
    Already-configured devices are flagged with 'already_configured': True.
    """
    # UDP first (fast)
    udp_results = await _discover_udp(UDP_DISCOVERY_TIMEOUT)
    found: dict[str, dict] = {d["host"]: d for d in udp_results}

    # TCP scan to catch devices that don't respond to broadcast
    subnet = _get_local_subnet()
    tcp_results = await _discover_tcp(subnet, DEFAULT_PORT, TCP_SCAN_TIMEOUT, TCP_SCAN_WORKERS)
    for d in tcp_results:
        if d["host"] not in found:
            found[d["host"]] = d

    # Flag already-configured entries
    for d in found.values():
        d["already_configured"] = _is_already_configured(hass, d["host"], DEFAULT_PORT)

    return list(found.values())


async def connect_client(hass, user_input):
    """Open a test connection to the USR-R16 device."""
    client_aw = create_usr_r16_client_connection(
        host=user_input[CONF_HOST],
        port=user_input[CONF_PORT],
        password=user_input[CONF_PASSWORD],
        loop=hass.loop,
        timeout=CONNECTION_TIMEOUT,
        reconnect_interval=DEFAULT_RECONNECT_INTERVAL,
        keep_alive_interval=DEFAULT_KEEP_ALIVE_INTERVAL,
    )
    return await asyncio.wait_for(client_aw, timeout=CONNECTION_TIMEOUT)


async def validate_input(hass: HomeAssistant, user_input):
    """Validate connection credentials and check for duplicates."""
    if _is_already_configured(hass, user_input[CONF_HOST], user_input[CONF_PORT]):
        raise AlreadyConfigured

    try:
        client = await connect_client(hass, user_input)
    except asyncio.TimeoutError:
        raise CannotConnect

    try:
        def disconnect_callback():
            if client.in_transaction:
                client.active_transaction.set_exception(CannotConnect)

        client.disconnect_callback = disconnect_callback
        await client.status()
    except CannotConnect:
        client.disconnect_callback = None
        client.stop()
        raise CannotConnect
    else:
        client.disconnect_callback = None
        client.stop()


# ---- Config flow -----------------------------------------------------------

class R16FlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a USR-R16 config flow."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self):
        """Initialize flow state."""
        self._discovered: list[dict] = []

    async def async_step_import(self, user_input):
        """Handle import from configuration.yaml."""
        return await self.async_step_user(user_input)

    # -- Step 1: choose method -----------------------------------------------

    async def async_step_user(self, user_input=None):
        """Initial step: ask whether to auto-discover or enter manually."""
        if user_input is not None:
            if user_input["discovery_method"] == "auto":
                return await self.async_step_discover()
            return await self.async_step_manual()

        return self.async_show_form(
            step_id="user",
            data_schema=SCHEMA_INIT,
        )

    # -- Step 2a: run discovery ----------------------------------------------

    async def async_step_discover(self, user_input=None):
        """Run auto-discovery and show a 'searching…' progress step."""
        self._discovered = await discover_devices(self.hass)

        # Filter out already-configured devices from the selectable list
        new_devices = [d for d in self._discovered if not d["already_configured"]]

        if not new_devices:
            # Nothing new found — fall back to manual with an info error
            return await self.async_step_manual(errors={"base": "no_devices_found"})

        return await self.async_step_select()

    # -- Step 2b: select from discovered list --------------------------------

    async def async_step_select(self, user_input=None):
        """Let the user pick a discovered device or enter manually."""
        errors = {}

        if user_input is not None:
            selected = user_input["selected_device"]

            if selected == MANUAL_ENTRY:
                return await self.async_step_manual()

            # Find the chosen device
            host = selected
            data = {
                CONF_HOST: host,
                CONF_PORT: DEFAULT_PORT,
                CONF_PASSWORD: DEFAULT_PASSWORD,
            }
            return await self.async_step_manual(prefill=data)

        # Build the selector options — already-configured devices shown as disabled
        options = {}
        for d in self._discovered:
            label = f"{d['host']}"
            if d["name"]:
                label += f"  ({d['name']})"
            if d["already_configured"]:
                label += f"  — already configured"
            options[d["host"]] = label

        options[MANUAL_ENTRY] = "Enter manually…"

        return self.async_show_form(
            step_id="select",
            data_schema=vol.Schema(
                {vol.Required("selected_device"): vol.In(options)}
            ),
            errors=errors,
        )

    # -- Step 3: manual / confirm credentials --------------------------------

    async def async_step_manual(self, user_input=None, prefill=None, errors=None):
        """Manual entry or credential confirmation for a discovered device."""
        errors = errors or {}

        # Pre-fill form if coming from discovery selection
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=(prefill or {}).get(CONF_HOST, "")): str,
                vol.Optional(CONF_PORT, default=(prefill or {}).get(CONF_PORT, DEFAULT_PORT)): vol.Coerce(int),
                vol.Optional(CONF_PASSWORD, default=(prefill or {}).get(CONF_PASSWORD, DEFAULT_PASSWORD)): str,
            }
        )

        if user_input is not None:
            try:
                await validate_input(self.hass, user_input)
                address = f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                return self.async_create_entry(title=address, data=user_input)
            except AlreadyConfigured:
                errors["base"] = "already_configured"
            except CannotConnect:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="manual",
            data_schema=schema,
            errors=errors,
        )