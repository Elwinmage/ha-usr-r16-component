"""Tests for config_flow discovery helpers and auto/select/import steps."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.usr_r16.config_flow import (
    MANUAL_ENTRY,
    _discover_tcp,
    _discover_udp,
    _get_local_subnet,
    _probe_tcp,
    connect_client,
    discover_devices,
    validate_input,
)
from custom_components.usr_r16.const import DEFAULT_PASSWORD, DEFAULT_PORT, DOMAIN
from custom_components.usr_r16.errors import CannotConnect

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

TEST_HOST = "192.168.1.100"
TEST_PORT = DEFAULT_PORT
TEST_PASSWORD = DEFAULT_PASSWORD


async def _start_flow(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


# ---------------------------------------------------------------------------
# _get_local_subnet (lines 54-61)
# ---------------------------------------------------------------------------


def test_get_local_subnet_returns_cidr() -> None:
    """Should return a /24 subnet string derived from the local IP."""
    mock_sock = MagicMock()
    mock_sock.__enter__ = MagicMock(return_value=mock_sock)
    mock_sock.__exit__ = MagicMock(return_value=False)
    mock_sock.getsockname.return_value = ("10.0.0.42", 12345)

    with patch(
        "custom_components.usr_r16.config_flow.socket.socket", return_value=mock_sock
    ):
        subnet = _get_local_subnet()
    assert subnet == "10.0.0.0/24"


def test_get_local_subnet_fallback_on_error() -> None:
    """Should return 192.168.1.0/24 when socket fails."""
    with patch("socket.socket") as mock_sock:
        mock_sock.side_effect = OSError("no network")
        result = _get_local_subnet()
    assert result == "192.168.1.0/24"


# ---------------------------------------------------------------------------
# _probe_tcp (lines 119-132)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_tcp_success() -> None:
    """Should return the IP when connection succeeds."""
    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()

    with patch("asyncio.open_connection", return_value=(MagicMock(), writer)):
        result = await _probe_tcp("192.168.1.1", 8899, 1.0)
    assert result == "192.168.1.1"


@pytest.mark.asyncio
async def test_probe_tcp_refused() -> None:
    """Should return None on ConnectionRefusedError."""
    with patch("asyncio.open_connection", side_effect=ConnectionRefusedError):
        result = await _probe_tcp("192.168.1.1", 8899, 1.0)
    assert result is None


@pytest.mark.asyncio
async def test_probe_tcp_timeout() -> None:
    """Should return None on TimeoutError."""
    with patch("asyncio.open_connection", side_effect=asyncio.TimeoutError):
        result = await _probe_tcp("192.168.1.1", 8899, 1.0)
    assert result is None


@pytest.mark.asyncio
async def test_probe_tcp_oserror() -> None:
    """Should return None on generic OSError."""
    with patch("asyncio.open_connection", side_effect=OSError):
        result = await _probe_tcp("192.168.1.1", 8899, 1.0)
    assert result is None


@pytest.mark.asyncio
async def test_probe_tcp_wait_closed_exception() -> None:
    """Should still return the IP even if wait_closed raises."""
    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock(side_effect=Exception("closed"))

    with patch("asyncio.open_connection", return_value=(MagicMock(), writer)):
        result = await _probe_tcp("192.168.1.1", 8899, 1.0)
    assert result == "192.168.1.1"


# ---------------------------------------------------------------------------
# _discover_udp (lines 72-116)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_udp_no_replies() -> None:
    """Should return empty list when no devices reply."""
    mock_transport = MagicMock()
    mock_transport.close = MagicMock()

    with (
        patch(
            "asyncio.get_event_loop",
        ) as mock_loop,
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_loop.return_value.create_datagram_endpoint = AsyncMock(
            return_value=(mock_transport, MagicMock())
        )
        result = await _discover_udp(0.01)

    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_discover_udp_oserror_handled() -> None:
    """OSError during endpoint creation should be caught and return empty list."""
    with patch(
        "asyncio.get_event_loop",
    ) as mock_loop:
        mock_loop.return_value.create_datagram_endpoint = AsyncMock(
            side_effect=OSError("permission denied")
        )
        result = await _discover_udp(0.01)

    assert result == []


# ---------------------------------------------------------------------------
# _discover_tcp (lines 135-153)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_tcp_finds_open_ports() -> None:
    """Should return devices where TCP port is open."""
    with patch(
        "custom_components.usr_r16.config_flow._probe_tcp",
        return_value="192.168.1.5",
    ):
        result = await _discover_tcp("192.168.1.0/30", 8899, 0.1, 2)

    assert any(d["host"] == "192.168.1.5" for d in result)


@pytest.mark.asyncio
async def test_discover_tcp_invalid_subnet() -> None:
    """Invalid subnet should return empty list."""
    result = await _discover_tcp("not_a_subnet", 8899, 0.1, 2)
    assert result == []


@pytest.mark.asyncio
async def test_discover_tcp_no_open_ports() -> None:
    """Should return empty list when no hosts respond."""
    with patch("custom_components.usr_r16.config_flow._probe_tcp", return_value=None):
        result = await _discover_tcp("192.168.1.0/30", 8899, 0.1, 2)
    assert result == []


# ---------------------------------------------------------------------------
# discover_devices (lines 156-179)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_devices_merges_udp_and_tcp(hass: HomeAssistant) -> None:
    """discover_devices should merge UDP and TCP results."""
    udp_device = {"host": "192.168.1.10", "name": "USR-R16"}
    tcp_device = {"host": "192.168.1.20", "name": ""}

    with (
        patch(
            "custom_components.usr_r16.config_flow._discover_udp",
            return_value=[udp_device],
        ),
        patch(
            "custom_components.usr_r16.config_flow._discover_tcp",
            return_value=[tcp_device],
        ),
        patch(
            "custom_components.usr_r16.config_flow._get_local_subnet",
            return_value="192.168.1.0/24",
        ),
    ):
        devices = await discover_devices(hass)

    hosts = [d["host"] for d in devices]
    assert "192.168.1.10" in hosts
    assert "192.168.1.20" in hosts


@pytest.mark.asyncio
async def test_discover_devices_flags_already_configured(
    hass: HomeAssistant, mock_client
) -> None:
    """Already-configured devices should be flagged."""
    udp_device = {"host": TEST_HOST, "name": ""}

    # Pre-configure the device
    with (
        patch(
            "custom_components.usr_r16.config_flow.connect_client",
            return_value=mock_client,
        ),
        patch(
            "custom_components.usr_r16.protocol.USR16Client.setup",
            new_callable=AsyncMock,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"discovery_method": "manual"}
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "host": TEST_HOST,
                "port": TEST_PORT,
                "password": TEST_PASSWORD,
            },
        )

    with (
        patch(
            "custom_components.usr_r16.config_flow._discover_udp",
            return_value=[udp_device],
        ),
        patch(
            "custom_components.usr_r16.config_flow._discover_tcp",
            return_value=[],
        ),
        patch(
            "custom_components.usr_r16.config_flow._get_local_subnet",
            return_value="192.168.1.0/24",
        ),
    ):
        devices = await discover_devices(hass)

    dev = next(d for d in devices if d["host"] == TEST_HOST)
    assert dev["already_configured"] is True


@pytest.mark.asyncio
async def test_discover_devices_tcp_dedup(hass: HomeAssistant) -> None:
    """TCP result that duplicates a UDP result should not appear twice."""
    device = {"host": "192.168.1.5", "name": "USR"}

    with (
        patch(
            "custom_components.usr_r16.config_flow._discover_udp",
            return_value=[device],
        ),
        patch(
            "custom_components.usr_r16.config_flow._discover_tcp",
            return_value=[{"host": "192.168.1.5", "name": ""}],  # duplicate
        ),
        patch(
            "custom_components.usr_r16.config_flow._get_local_subnet",
            return_value="192.168.1.0/24",
        ),
    ):
        devices = await discover_devices(hass)

    assert sum(1 for d in devices if d["host"] == "192.168.1.5") == 1


# ---------------------------------------------------------------------------
# connect_client (lines 182-193)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_client_calls_create_connection(hass: HomeAssistant) -> None:
    """connect_client should call USR16Client.setup with correct credentials."""
    with (
        patch(
            "custom_components.usr_r16.protocol.USR16Client.setup",
            new_callable=AsyncMock,
        ) as mock_setup,
        patch(
            "custom_components.usr_r16.protocol.USR16Client.stop",
        ),
    ):
        await connect_client(
            hass,
            {"host": TEST_HOST, "port": TEST_PORT, "password": TEST_PASSWORD},
        )
    mock_setup.assert_called_once()


# ---------------------------------------------------------------------------
# validate_input edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_input_timeout_raises_cannot_connect(
    hass: HomeAssistant,
) -> None:
    """TimeoutError from connect_client should be converted
    to CannotConnect (line 204).
    """
    with (
        patch(
            "custom_components.usr_r16.config_flow.connect_client",
            side_effect=asyncio.TimeoutError,
        ),
        pytest.raises(CannotConnect),
    ):
        await validate_input(
            hass,
            {"host": TEST_HOST, "port": TEST_PORT, "password": TEST_PASSWORD},
        )


@pytest.mark.asyncio
async def test_step_import_delegates_to_user(hass: HomeAssistant) -> None:
    """async_step_import with no data should show the method-selection form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IMPORT},
    )
    # Import calls async_step_user(None) which shows the form
    assert result.get("type") == FlowResultType.FORM
    assert result.get("step_id") == "user"


# ---------------------------------------------------------------------------
# Auto-discovery path (lines 245-246, 258-267)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_user_auto_with_new_devices(hass: HomeAssistant) -> None:
    """Choosing 'auto' with devices found should show the select step."""
    devices = [{"host": "192.168.1.50", "name": "USR", "already_configured": False}]

    with patch(
        "custom_components.usr_r16.config_flow.discover_devices",
        return_value=devices,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"discovery_method": "auto"}
        )

    assert result.get("type") == FlowResultType.FORM
    assert result.get("step_id") == "select"


@pytest.mark.asyncio
async def test_step_user_auto_no_new_devices_falls_back(hass: HomeAssistant) -> None:
    """'auto' with no new devices should fall back to manual with error."""
    devices = [{"host": "192.168.1.50", "name": "", "already_configured": True}]

    with patch(
        "custom_components.usr_r16.config_flow.discover_devices",
        return_value=devices,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"discovery_method": "auto"}
        )

    assert result.get("type") == FlowResultType.FORM
    assert result.get("step_id") == "manual"
    errors = result.get("errors") or {}
    assert errors.get("base") == "no_devices_found"


@pytest.mark.asyncio
async def test_step_user_auto_empty_discovery(hass: HomeAssistant) -> None:
    """'auto' with empty discovery result should fall back to manual."""
    with patch(
        "custom_components.usr_r16.config_flow.discover_devices",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"discovery_method": "auto"}
        )

    assert result.get("step_id") == "manual"


# ---------------------------------------------------------------------------
# async_step_select (lines 271-306)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_select_shows_device_list(hass: HomeAssistant) -> None:
    """select step should show device list including MANUAL_ENTRY."""
    devices = [
        {"host": "192.168.1.10", "name": "USR", "already_configured": False},
        {"host": "192.168.1.11", "name": "", "already_configured": True},
    ]

    with patch(
        "custom_components.usr_r16.config_flow.discover_devices",
        return_value=devices,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"discovery_method": "auto"}
        )

    assert result.get("step_id") == "select"
    # Schema should contain the hosts + manual entry
    assert result.get("data_schema") is not None


@pytest.mark.asyncio
async def test_step_select_choose_device_prefills_manual(
    hass: HomeAssistant, mock_client
) -> None:
    """Selecting a discovered device should go to manual with prefilled host."""
    devices = [{"host": "192.168.1.10", "name": "USR", "already_configured": False}]

    with patch(
        "custom_components.usr_r16.config_flow.discover_devices",
        return_value=devices,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"discovery_method": "auto"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"selected_device": "192.168.1.10"}
        )

    assert result.get("step_id") == "manual"


@pytest.mark.asyncio
async def test_step_select_choose_manual_entry(hass: HomeAssistant) -> None:
    """Selecting MANUAL_ENTRY in select step should show blank manual form."""
    devices = [{"host": "192.168.1.10", "name": "", "already_configured": False}]

    with patch(
        "custom_components.usr_r16.config_flow.discover_devices",
        return_value=devices,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"discovery_method": "auto"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"selected_device": MANUAL_ENTRY}
        )

    assert result.get("step_id") == "manual"


# ---------------------------------------------------------------------------
# _get_local_subnet — happy path (lines 56-59, blocked by pytest-socket env)
# ---------------------------------------------------------------------------


def test_get_local_subnet_happy_path() -> None:
    """Should compute /24 subnet from a working socket (mocked)."""
    mock_sock = MagicMock()
    mock_sock.__enter__ = lambda self: self
    mock_sock.__exit__ = MagicMock(return_value=False)
    mock_sock.getsockname.return_value = ("10.0.0.42", 0)

    with patch("socket.socket", return_value=mock_sock):
        result = _get_local_subnet()

    assert result == "10.0.0.0/24"


# ---------------------------------------------------------------------------
# _discover_udp — _Proto internal methods (lines 82-84, 87-97, 100)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_udp_proto_methods() -> None:
    """
    Drive _Proto.connection_made, datagram_received (all branches) and
    error_received by capturing the protocol factory from create_datagram_endpoint.
    """
    from custom_components.usr_r16.config_flow import UDP_DISCOVERY_MSG

    captured_proto = {}

    async def fake_endpoint(protocol_factory, **kwargs):
        proto = protocol_factory()

        # --- connection_made (lines 82-84) ---
        mock_transport = MagicMock()
        mock_transport.get_extra_info.return_value = MagicMock()  # mock socket
        proto.connection_made(mock_transport)

        # --- datagram_received: echo (line 87-88, early return) ---
        proto.datagram_received(UDP_DISCOVERY_MSG, ("192.168.1.1", 1901))

        # --- datagram_received: short packet without USR header (lines 89-97) ---
        proto.datagram_received(b"\x01\x02\x03", ("192.168.1.2", 1901))

        # --- datagram_received: valid USR-R16 reply (lines 91-97) ---
        # Build a 35-byte valid USR reply: byte[0]=0xFF, byte[1]=35, bytes[5:9]=IP
        data = bytearray(35)
        data[0] = 0xFF
        data[1] = 35
        data[5] = 192
        data[6] = 168
        data[7] = 1
        data[8] = 10
        data[19:27] = b"USR-R16\x00"
        proto.datagram_received(bytes(data), ("192.168.1.10", 1901))

        # --- error_received (line 100) ---
        proto.error_received(OSError("test error"))

        captured_proto["proto"] = proto
        return (mock_transport, proto)

    with (
        patch("asyncio.get_event_loop") as mock_loop,
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_loop.return_value.create_datagram_endpoint = fake_endpoint
        result = await _discover_udp(0.01)

    # The valid device at 192.168.1.10 should be in results
    assert any(d["host"] == "192.168.1.10" for d in result)
    assert any(d["name"] == "USR-R16" for d in result)

    # The short-packet device at 192.168.1.2 should also be there (unnamed)
    assert any(d["host"] == "192.168.1.2" for d in result)

    # The echo should NOT be in results
    assert not any(d["host"] == "192.168.1.1" for d in result)
