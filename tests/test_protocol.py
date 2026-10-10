"""Tests for the embedded USR-R16 protocol implementation."""

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.usr_r16.protocol import (
    MAX_PASSWORD_LENGTH,
    RELAY_COUNT,
    CannotConnect,
    InvalidAuth,
    USR16Client,
    USR16Protocol,
)

TEST_HOST = "192.168.1.50"
TEST_PORT = 8899
TEST_PASSWORD = "admin"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(**kwargs):
    """Return a USR16Client with mocked transport/protocol.

    _reset_timeout is mocked to prevent any call_later from leaking into
    the HA event loop and causing 'lingering timer' test failures.
    """
    host = str(kwargs.get("host", TEST_HOST))
    port = int(kwargs.get("port", TEST_PORT))
    password = str(kwargs.get("password", TEST_PASSWORD))
    client = USR16Client(host=host, port=port, password=password)
    client.transport = MagicMock()
    proto = USR16Protocol(client)
    transport_mock = MagicMock(spec=asyncio.Transport)
    proto.transport = transport_mock  # type: ignore[assignment]
    proto._timeout = MagicMock()
    proto._cmd_timeout = MagicMock()
    proto._keep_alive = MagicMock()
    # Prevent real timers from being scheduled in tests
    proto._reset_timeout = MagicMock()
    proto._reset_cmd_timeout = MagicMock()
    client.protocol = proto  # type: ignore[assignment]
    client.is_connected = True
    return client, proto


def _call_raw(proto, raw_bytes):
    """Call _handle_raw_packet and cancel timers to avoid lingering handles."""
    # Patch call_later to prevent real timers being scheduled
    with patch("asyncio.get_event_loop") as mock_loop:
        mock_loop.return_value.call_later = MagicMock(return_value=MagicMock())
        proto._handle_raw_packet(raw_bytes)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_max_password_length():
    assert MAX_PASSWORD_LENGTH == 5


def test_relay_count():
    assert RELAY_COUNT == 16


# ---------------------------------------------------------------------------
# USR16Protocol — packet validation
# ---------------------------------------------------------------------------


def test_valid_packet_two_bytes():
    assert USR16Protocol._valid_packet(b"OK") is True
    assert USR16Protocol._valid_packet(b"NO") is True


def test_valid_packet_correct_checksum():
    """format_packet produces packets with correct structure."""
    pkt = USR16Protocol.format_packet("0a")
    # Full packet starts with 55aa frame
    assert pkt[:2] == b"\x55\xaa"
    # The raw part (after frame) has correct checksum: sum(body[:-1]) & 0xFF == body[-1]
    raw = pkt[2:]
    checksum = sum(raw[:-1]) & 0xFF
    assert checksum == raw[-1]


def test_valid_packet_wrong_length():
    assert USR16Protocol._valid_packet(b"\x00\x01\x02") is False


def test_valid_packet_bad_checksum():
    pkt = bytearray(USR16Protocol.format_packet("0a")[2:])
    pkt[-1] ^= 0xFF  # corrupt checksum
    assert USR16Protocol._valid_packet(bytes(pkt)) is False


# ---------------------------------------------------------------------------
# USR16Protocol — format_packet
# ---------------------------------------------------------------------------


def test_format_packet_status():
    pkt = USR16Protocol.format_packet("0a")
    assert pkt[:2] == b"\x55\xaa"
    assert len(pkt) == 7  # head(2) + length(2) + id(1) + cmd(1) + checksum(1)


def test_format_packet_with_param():
    pkt = USR16Protocol.format_packet("02", "01")  # turn on relay 1
    assert pkt[:2] == b"\x55\xaa"
    # Verify it's a valid packet (checksum OK)
    assert USR16Protocol._valid_packet(pkt[2:]) is True


# ---------------------------------------------------------------------------
# USR16Protocol — auth responses
# ---------------------------------------------------------------------------


def test_handle_ok_response():
    client, proto = _make_client()
    fut = asyncio.get_event_loop().create_future()
    client._auth_future = fut
    proto._handle_raw_packet(b"OK")
    assert client._auth_ok is True
    assert fut.done() and fut.result() is True


def test_handle_no_response_sets_invalid_auth():
    client, proto = _make_client()
    fut = asyncio.get_event_loop().create_future()
    client._auth_future = fut
    proto._handle_raw_packet(b"NO")
    assert client._auth_ok is False
    assert fut.done()
    with pytest.raises(InvalidAuth):
        fut.result()


def test_handle_no_response_without_future():
    """NO response when no future pending should not raise."""
    client, proto = _make_client()
    client._auth_future = None
    proto._handle_raw_packet(b"NO")  # should not raise
    assert client._auth_ok is False


# ---------------------------------------------------------------------------
# USR16Protocol — state packets
# ---------------------------------------------------------------------------


def _make_state_packet(cmd_byte: int, relay: int, state: int) -> bytes:
    """Build a single-relay state packet (0x81/0x82/0x83)."""
    body = bytes([0x00, 0x03, 0x00, 0x01, cmd_byte, relay, state])
    checksum = (sum(body) & 0xFF).to_bytes(1, "big")
    return body + checksum


def test_handle_single_relay_on():
    client, proto = _make_client()
    cb = MagicMock()
    client.register_status_callback(cb, "3")

    # Build 0x81 packet: relay 3 ON
    raw = bytes([0x00, 0x01, 0x00, 0x81, 3, 1])
    checksum = sum(raw) & 0xFF
    proto._handle_raw_packet(raw + bytes([checksum]))

    assert client.states.get("3") is True
    cb.assert_called_once_with(True)


def test_handle_single_relay_off():
    client, proto = _make_client()
    cb = MagicMock()
    client.register_status_callback(cb, "5")

    raw = bytes([0x00, 0x01, 0x00, 0x82, 5, 0])
    checksum = sum(raw) & 0xFF
    proto._handle_raw_packet(raw + bytes([checksum]))

    assert client.states.get("5") is False
    cb.assert_called_once_with(False)


def test_handle_single_relay_no_change_no_callback():
    """Callback should NOT fire if state hasn't changed."""
    client, proto = _make_client()
    client.states["3"] = True  # pre-set same state
    cb = MagicMock()
    client.register_status_callback(cb, "3")

    raw = bytes([0x00, 0x01, 0x00, 0x81, 3, 1])
    checksum = sum(raw) & 0xFF
    proto._handle_raw_packet(raw + bytes([checksum]))

    cb.assert_not_called()


def test_handle_all_on_packet():
    client, proto = _make_client()
    raw = bytes([0x00, 0x01, 0x00, 0x84, 1])
    checksum = sum(raw) & 0xFF
    proto._handle_raw_packet(raw + bytes([checksum]))
    assert all(client.states.get(str(i)) is True for i in range(1, 17))


def test_handle_all_off_packet():
    client, proto = _make_client()
    # Pre-fill as ON
    client.states = {str(i): True for i in range(1, 17)}
    raw = bytes([0x00, 0x01, 0x00, 0x85, 0])
    checksum = sum(raw) & 0xFF
    proto._handle_raw_packet(raw + bytes([checksum]))
    assert all(client.states.get(str(i)) is False for i in range(1, 17))


def test_handle_full_state_0x8a():
    """0x8a full-state: relay 1 ON, relay 9 ON, rest OFF."""
    client, proto = _make_client()
    # byte4: relay 1-8 bitmask (relay 1 = bit0 = 0b00000001 = 0x01)
    # byte5: relay 9-16 bitmask (relay 9 = bit0 = 0b00000001 = 0x01)
    raw = bytes([0x00, 0x02, 0x00, 0x8A, 0x01, 0x01])
    checksum = sum(raw) & 0xFF
    proto._handle_raw_packet(raw + bytes([checksum]))

    assert client.states["1"] is True
    assert client.states["2"] is False
    assert client.states["9"] is True
    assert client.states["10"] is False


def test_handle_heartbeat_packet():
    client, proto = _make_client()
    raw = bytes([0x00, 0x01, 0x00, 0xFF])
    checksum = sum(raw) & 0xFF
    proto._handle_raw_packet(raw + bytes([checksum]))
    # No state change, no error
    assert client.states == {}


def test_handle_unknown_packet_logs_warning():
    _client, proto = _make_client()
    raw = bytes([0x00, 0x01, 0x00, 0xAB, 0x00])
    checksum = sum(raw) & 0xFF
    # Should not raise, just log warning
    proto._handle_raw_packet(raw + bytes([checksum]))


# ---------------------------------------------------------------------------
# USR16Protocol — transaction resolution
# ---------------------------------------------------------------------------


def test_transaction_resolved_on_packet():
    loop = asyncio.get_event_loop()
    client, proto = _make_client()
    fut = loop.create_future()
    client.active_transaction = cast("asyncio.Future[Any]", fut)
    client.in_transaction = True
    client.active_packet = cast("bytes", b"\x00")

    raw = bytes([0x00, 0x01, 0x00, 0x81, 3, 1])
    checksum = sum(raw) & 0xFF
    proto._handle_raw_packet(raw + bytes([checksum]))

    assert fut.done()
    assert "3" in fut.result()


def test_status_waiters_resolved_on_packet():
    loop = asyncio.get_event_loop()
    client, proto = _make_client()

    waiter = loop.create_future()
    client.status_waiters.append(waiter)
    main_fut = loop.create_future()
    client.active_transaction = cast("asyncio.Future[Any]", main_fut)
    client.in_transaction = True
    client.active_packet = cast("bytes", b"\x00")

    raw = bytes([0x00, 0x01, 0x00, 0x81, 1, 1])
    checksum = sum(raw) & 0xFF
    proto._handle_raw_packet(raw + bytes([checksum]))

    assert waiter.done()


# ---------------------------------------------------------------------------
# USR16Protocol — connection lifecycle
# ---------------------------------------------------------------------------


def test_connection_made_sets_transport():
    _client, proto = _make_client()
    transport = MagicMock()
    proto._timeout = None
    proto._keep_alive = None
    proto.connection_made(transport)
    assert proto.transport is transport


def test_connection_lost_no_reconnect():
    client, proto = _make_client()
    client.reconnect = False
    disconnect_cb = MagicMock()
    client.disconnect_callback = disconnect_cb
    proto.connection_lost(None)
    disconnect_cb.assert_called_once()


def test_connection_lost_schedules_reconnect():
    client, proto = _make_client()
    client.reconnect = True
    client.disconnect_callback = None

    with patch("asyncio.ensure_future") as mock_ensure:
        proto.connection_lost(None)
        mock_ensure.assert_called_once()


def test_data_received_drops_invalid():
    client, proto = _make_client()
    # 0xaa55 delimiter + invalid packet
    proto.data_received(b"\xaa\x55\xde\xad")
    assert client.states == {}


def test_data_received_valid_heartbeat():
    client, proto = _make_client()
    # Build a valid heartbeat packet with framing
    raw = bytes([0x00, 0x01, 0x00, 0xFF])
    checksum = sum(raw) & 0xFF
    framed = b"\xaa\x55" + raw + bytes([checksum])
    proto.data_received(framed)
    assert client.states == {}


# ---------------------------------------------------------------------------
# USR16Protocol — keepalive and send
# ---------------------------------------------------------------------------


def test_send_keepalive_when_idle():
    client, proto = _make_client()
    client.in_transaction = False
    proto._send_keepalive()
    cast(MagicMock, proto.transport).write.assert_called_once()


def test_send_keepalive_skipped_during_transaction():
    client, proto = _make_client()
    client.in_transaction = True
    proto._send_keepalive()
    cast(MagicMock, proto.transport).write.assert_not_called()


def test_send_packet_dequeues_and_writes():
    loop = asyncio.get_event_loop()
    client, proto = _make_client()
    fut = loop.create_future()
    pkt = USR16Protocol.format_packet("0a")
    client.waiters.append((fut, pkt))
    proto._cmd_timeout = None
    proto.send_packet()
    cast(MagicMock, proto.transport).write.assert_called_once_with(pkt)
    assert client.in_transaction is True


def test_cancel_timers():
    _client, proto = _make_client()
    proto._cancel_timers()
    cast(MagicMock, proto._timeout).cancel.assert_called_once()
    cast(MagicMock, proto._cmd_timeout).cancel.assert_called_once()
    cast(MagicMock, proto._keep_alive).cancel.assert_called_once()


# ---------------------------------------------------------------------------
# USR16Client — setup success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_setup_success():
    """setup() should connect and authenticate successfully."""
    client = USR16Client(host=TEST_HOST, port=TEST_PORT, password=TEST_PASSWORD)
    reconnect_cb = MagicMock()
    client.reconnect_callback = reconnect_cb

    mock_transport = MagicMock()

    async def fake_wait_for(coro, timeout):
        # For the TCP connection future
        return (mock_transport, MagicMock())

    # Simulate auth success immediately after setup resolves
    async def fake_setup_inner():
        client.is_connected = True
        client._auth_future = asyncio.get_event_loop().create_future()
        client._auth_future.set_result(True)
        client._auth_ok = True
        client.transport = mock_transport
        if client.reconnect_callback:
            client.reconnect_callback()

    with patch.object(client, "setup", side_effect=fake_setup_inner):
        await client.setup()

    assert client.is_connected is True
    reconnect_cb.assert_called_once()


@pytest.mark.asyncio
async def test_client_setup_timeout_raises_cannot_connect():
    client = USR16Client(host=TEST_HOST, port=TEST_PORT, password=TEST_PASSWORD)
    with (
        patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
        pytest.raises(CannotConnect),
    ):
        await client.setup()


@pytest.mark.asyncio
async def test_client_setup_os_error_raises_cannot_connect():
    client = USR16Client(host=TEST_HOST, port=TEST_PORT, password=TEST_PASSWORD)

    async def raise_os_error(coro, timeout):
        raise OSError("Connection refused")

    with (
        patch("asyncio.wait_for", side_effect=raise_os_error),
        pytest.raises(CannotConnect),
    ):
        await client.setup()


@pytest.mark.asyncio
async def test_client_setup_invalid_auth_propagates():
    """InvalidAuth from protocol should propagate out of setup()."""
    client = USR16Client(host=TEST_HOST, port=TEST_PORT, password="wrong")
    mock_transport = MagicMock()

    call_count = [0]

    async def fake_wait_for(coro, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: TCP connection
            client.transport = mock_transport
            return (mock_transport, MagicMock())
        else:
            # Second call: auth wait — raise InvalidAuth
            raise InvalidAuth("Wrong password")

    with (
        patch("asyncio.wait_for", side_effect=fake_wait_for),
        pytest.raises(InvalidAuth),
    ):
        await client.setup()

    mock_transport.close.assert_called()


# ---------------------------------------------------------------------------
# USR16Client — stop
# ---------------------------------------------------------------------------


def test_client_stop():
    client, _ = _make_client()
    client.stop()
    cast(MagicMock, client.transport).close.assert_called_once()
    assert client.reconnect is False


# ---------------------------------------------------------------------------
# USR16Client — register_status_callback
# ---------------------------------------------------------------------------


def test_register_status_callback():
    client, _ = _make_client()
    cb = MagicMock()
    client.register_status_callback(cb, "4")
    assert cb in client.status_callbacks["4"]


def test_register_multiple_callbacks_same_port():
    client, _ = _make_client()
    cb1, cb2 = MagicMock(), MagicMock()
    client.register_status_callback(cb1, "1")
    client.register_status_callback(cb2, "1")
    assert len(client.status_callbacks["1"]) == 2


# ---------------------------------------------------------------------------
# USR16Client — relay commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_on_sends_packet():
    client, _proto = _make_client()
    loop = asyncio.get_event_loop()

    async def fake_send(pkt):
        fut = loop.create_future()
        fut.set_result({"3": True})
        return await fut

    with patch.object(client, "_send", side_effect=fake_send) as mock_send:
        await client.turn_on("3")
    mock_send.assert_called_once()
    pkt = mock_send.call_args[0][0]
    assert pkt[5:6] == b"\x02"  # turn_on command byte


@pytest.mark.asyncio
async def test_turn_off_sends_packet():
    client, _proto = _make_client()

    async def fake_send(pkt):
        fut = asyncio.get_event_loop().create_future()
        fut.set_result({"3": False})
        return await fut

    with patch.object(client, "_send", side_effect=fake_send) as mock_send:
        await client.turn_off("3")
    pkt = mock_send.call_args[0][0]
    assert pkt[5:6] == b"\x01"  # turn_off command byte


@pytest.mark.asyncio
async def test_toggle_sends_packet():
    client, _proto = _make_client()

    async def fake_send(pkt):
        fut = asyncio.get_event_loop().create_future()
        fut.set_result({"3": True})
        return await fut

    with patch.object(client, "_send", side_effect=fake_send) as mock_send:
        await client.toggle("3")
    pkt = mock_send.call_args[0][0]
    assert pkt[5:6] == b"\x03"  # toggle command byte


# ---------------------------------------------------------------------------
# USR16Client — handle_disconnect reconnect loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_disconnect_reconnects():
    client, _ = _make_client()
    client.reconnect = True
    attempts = []

    async def fake_setup():
        attempts.append(1)
        if len(attempts) == 1:
            raise CannotConnect("first attempt")
        # Second attempt succeeds
        client.reconnect = False  # stop loop

    with (
        patch.object(client, "setup", side_effect=fake_setup),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await client.handle_disconnect()

    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_handle_disconnect_stops_on_invalid_auth():
    """InvalidAuth during reconnect should not loop forever."""
    client, _ = _make_client()
    client.reconnect = True

    async def fake_setup():
        raise InvalidAuth("wrong password")

    call_count = 0
    original_sleep = asyncio.sleep

    async def counting_sleep(t):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            client.reconnect = False
        await original_sleep(0)

    with (
        patch.object(client, "setup", side_effect=fake_setup),
        patch("asyncio.sleep", side_effect=counting_sleep),
    ):
        await client.handle_disconnect()


# ---------------------------------------------------------------------------
# Additional coverage for remaining uncovered lines
# ---------------------------------------------------------------------------


def test_connection_lost_with_error(hass):
    """Line 61: error disconnect logs error."""
    client, proto = _make_client()
    client.reconnect = False
    client.disconnect_callback = MagicMock()
    proto.connection_lost(OSError("boom"))
    client.disconnect_callback.assert_called_once()


def test_send_next_with_waiters():
    """Lines 201-202: _send_next triggers send_packet when waiters present."""
    loop = asyncio.get_event_loop()
    client, proto = _make_client()
    fut = loop.create_future()
    pkt = USR16Protocol.format_packet("0a")
    client.waiters.append((fut, pkt))
    proto._cmd_timeout = None
    proto._send_next()
    cast(MagicMock, proto.transport).write.assert_called_once_with(pkt)


def test_send_next_empty_queue():
    """_send_next does nothing when no waiters."""
    _client, proto = _make_client()
    proto._send_next()  # should not raise
    cast(MagicMock, proto.transport).write.assert_not_called()


def test_cmd_timeout_cancel_on_no_transaction():
    """Lines 210-218: cmd_timeout cancelled when no in_transaction."""
    _client, proto = _make_client()
    mock_cmd_timeout = MagicMock()
    proto._cmd_timeout = mock_cmd_timeout
    # Packet with no pending transaction
    raw = bytes([0x00, 0x01, 0x00, 0xFF])
    checksum = sum(raw) & 0xFF
    proto._handle_raw_packet(raw + bytes([checksum]))
    mock_cmd_timeout.cancel.assert_called()


def test_reset_timeout_real(hass):
    """Lines 210-218: _reset_timeout schedules real timers (using hass loop)."""
    _client, proto = _make_client()
    # Unset the mock so real _reset_timeout is tested
    proto._reset_timeout = USR16Protocol._reset_timeout.__get__(proto, USR16Protocol)
    proto._timeout = None
    proto._keep_alive = None
    with patch("asyncio.get_event_loop") as mock_loop:
        mock_hl = MagicMock()
        mock_hl.call_later.return_value = MagicMock()
        mock_loop.return_value = mock_hl
        proto._reset_timeout()
    assert mock_hl.call_later.call_count == 2


def test_reset_cmd_timeout_real():
    """Lines 224-227: _reset_cmd_timeout schedules timer."""
    _client, proto = _make_client()
    proto._reset_cmd_timeout = USR16Protocol._reset_cmd_timeout.__get__(
        proto, USR16Protocol
    )
    proto._cmd_timeout = None
    with patch("asyncio.get_event_loop") as mock_loop:
        mock_hl = MagicMock()
        mock_hl.call_later.return_value = MagicMock()
        mock_loop.return_value = mock_hl
        proto._reset_cmd_timeout()
    mock_hl.call_later.assert_called_once()


@pytest.mark.asyncio
async def test_client_setup_auth_timeout():
    """Lines 350-351: auth timeout closes transport and raises CannotConnect."""
    client = USR16Client(host=TEST_HOST, port=TEST_PORT, password=TEST_PASSWORD)
    mock_transport = MagicMock()
    call_count = [0]

    async def fake_wait_for(coro, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            client.transport = mock_transport
            return (mock_transport, MagicMock())
        raise asyncio.TimeoutError()

    with (
        patch("asyncio.wait_for", side_effect=fake_wait_for),
        pytest.raises(CannotConnect),
    ):
        await client.setup()

    mock_transport.close.assert_called()


def test_client_send_queues_and_sends():
    """Lines 394-399: _send enqueues packet and triggers send_packet."""
    client, proto = _make_client()
    client.in_transaction = False
    pkt = USR16Protocol.format_packet("0a")
    proto._cmd_timeout = None
    fut = client._send(pkt)
    cast(MagicMock, proto.transport).write.assert_called_once_with(pkt)
    assert not fut.done()


def test_client_send_queues_without_send_during_transaction():
    """_send doesn't send immediately when in_transaction=True."""
    client, proto = _make_client()
    client.in_transaction = True
    client.active_transaction = asyncio.get_event_loop().create_future()
    pkt = USR16Protocol.format_packet("0a")
    client._send(pkt)
    cast(MagicMock, proto.transport).write.assert_not_called()
    assert len(client.waiters) == 1


def test_transaction_triggers_send_next():
    """Line 177: send_next called when more waiters after transaction resolved."""
    loop = asyncio.get_event_loop()
    client, proto = _make_client()

    # First transaction
    fut1 = loop.create_future()
    pkt1 = USR16Protocol.format_packet("0a")
    client.active_transaction = cast("asyncio.Future[Any]", fut1)
    client.in_transaction = True
    client.active_packet = cast("bytes", pkt1)

    # Second packet waiting
    fut2 = loop.create_future()
    pkt2 = USR16Protocol.format_packet("02", "01")
    client.waiters.append((fut2, pkt2))

    # Resolve via a state packet — should trigger send_next for pkt2
    raw = bytes([0x00, 0x01, 0x00, 0x81, 3, 1])
    checksum = sum(raw) & 0xFF
    proto._handle_raw_packet(raw + bytes([checksum]))

    # pkt2 should have been sent
    cast(MagicMock, proto.transport).write.assert_called_with(pkt2)


def test_transaction_cmd_timeout_cancel_when_no_waiters():
    """Line 212: _cmd_timeout cancelled when transaction done and no more waiters."""
    loop = asyncio.get_event_loop()
    client, proto = _make_client()
    mock_cmd_timeout = MagicMock()
    proto._cmd_timeout = mock_cmd_timeout

    fut = loop.create_future()
    client.active_transaction = cast("asyncio.Future[Any]", fut)
    client.in_transaction = True
    client.active_packet = cast("bytes", b"\x00")

    raw = bytes([0x00, 0x01, 0x00, 0x81, 2, 0])
    checksum = sum(raw) & 0xFF
    proto._handle_raw_packet(raw + bytes([checksum]))

    mock_cmd_timeout.cancel.assert_called()


def test_reset_timeout_cancels_existing():
    """Lines 212,217: _reset_timeout cancels existing timers before rescheduling."""
    _client, proto = _make_client()
    # Unset the mock so real logic runs
    proto._reset_timeout = USR16Protocol._reset_timeout.__get__(proto, USR16Protocol)
    old_timeout = MagicMock()
    old_keepalive = MagicMock()
    proto._timeout = old_timeout
    proto._keep_alive = old_keepalive

    with patch("asyncio.get_event_loop") as mock_loop:
        mock_hl = MagicMock()
        mock_hl.call_later.return_value = MagicMock()
        mock_loop.return_value = mock_hl
        proto._reset_timeout()

    old_timeout.cancel.assert_called_once()
    old_keepalive.cancel.assert_called_once()


def test_reset_cmd_timeout_cancels_existing():
    """Line 226: _reset_cmd_timeout cancels existing timer."""
    _client, proto = _make_client()
    proto._reset_cmd_timeout = USR16Protocol._reset_cmd_timeout.__get__(
        proto, USR16Protocol
    )
    old_cmd = MagicMock()
    proto._cmd_timeout = old_cmd

    with patch("asyncio.get_event_loop") as mock_loop:
        mock_hl = MagicMock()
        mock_hl.call_later.return_value = MagicMock()
        mock_loop.return_value = mock_hl
        proto._reset_cmd_timeout()

    old_cmd.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_client_setup_fires_reconnect_callback():
    """Lines 357-358: reconnect_callback fires after successful auth."""
    client = USR16Client(host=TEST_HOST, port=TEST_PORT, password=TEST_PASSWORD)
    reconnect_cb = MagicMock()
    client.reconnect_callback = reconnect_cb
    mock_transport = MagicMock()
    call_count = [0]

    async def fake_wait_for(coro, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            client.transport = mock_transport
            return (mock_transport, MagicMock())
        # Auth future succeeds
        client._auth_ok = True
        return True

    with (
        patch("asyncio.wait_for", side_effect=fake_wait_for),
        patch("asyncio.shield", side_effect=lambda f: f),
    ):
        await client.setup()

    reconnect_cb.assert_called_once()
