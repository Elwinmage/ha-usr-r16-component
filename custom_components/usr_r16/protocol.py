"""USR-R16 relay board — embedded TCP protocol implementation.

This replaces the external usr-r16 library, adding:
- Bad-password detection (device responds with b'NO')
- Proper async setup/teardown for Home Assistant
- No dependency on the usr_r16 PyPI package
"""

import asyncio
import logging
from collections import deque

_LOGGER = logging.getLogger(__name__)

# Maximum password length enforced by the USR-R16 firmware
MAX_PASSWORD_LENGTH = 5

# Relay count
RELAY_COUNT = 16


class InvalidAuth(Exception):
    """Raised when the device rejects the password."""


class CannotConnect(Exception):
    """Raised when TCP connection cannot be established."""


class USR16Protocol(asyncio.Protocol):
    """asyncio.Protocol implementation for the USR-R16 binary protocol."""

    def __init__(self, client):
        """Initialize protocol."""
        self.client = client
        self._buffer = b""
        self.transport = None
        self._timeout = None
        self._cmd_timeout = None
        self._keep_alive = None

    # ------------------------------------------------------------------
    # asyncio.Protocol interface
    # ------------------------------------------------------------------

    def connection_made(self, transport: asyncio.BaseTransport) -> None:  # type: ignore[override]
        """Store transport and start keepalive timer."""
        import typing

        self.transport = typing.cast(asyncio.Transport, transport)
        _LOGGER.debug("TCP connection established to %s", self.client.address)
        self._reset_timeout()

    def data_received(self, data: bytes) -> None:
        """Append incoming bytes to buffer and process."""
        _LOGGER.debug("Received raw bytes: %s", data.hex())
        self._buffer = data
        self._handle_buffer()

    def connection_lost(self, exc: Exception | None) -> None:
        """Handle connection drop — schedule reconnect if needed."""
        if exc:
            _LOGGER.error(
                "USR-R16 %s disconnected with error: %s", self.client.address, exc
            )
        else:
            _LOGGER.info("USR-R16 %s connection closed", self.client.address)

        self._cancel_timers()

        if self.client.disconnect_callback:
            self.client.disconnect_callback()

        if self.client.reconnect:
            _LOGGER.debug("Scheduling reconnect for %s", self.client.address)
            asyncio.ensure_future(self.client.handle_disconnect())

    # ------------------------------------------------------------------
    # Packet handling
    # ------------------------------------------------------------------

    def _handle_buffer(self) -> None:
        """Split buffer on 0xAA55 frame delimiter and process each packet."""
        for chunk in self._buffer.split(b"\xaa\x55"):
            if chunk:
                if self._valid_packet(chunk):
                    self._handle_raw_packet(chunk)
                else:
                    _LOGGER.warning("Dropping invalid packet: %s", chunk.hex())

    @staticmethod
    def _valid_packet(raw: bytes) -> bool:
        """Validate packet checksum."""
        if len(raw) == 2:
            return True  # Auth response: 'OK' or 'NO'
        if len(raw) not in (6, 7):
            return False
        checksum = sum(raw[:-1]) & 0xFF
        return checksum == raw[-1]

    def _handle_raw_packet(self, raw: bytes) -> None:
        """Parse a validated packet and update client state."""
        self._reset_timeout()
        states: dict[str, bool] = {}
        changes: list[str] = []

        if len(raw) == 2:
            text = raw.decode(errors="ignore")
            if text == "OK":
                _LOGGER.info(
                    "USR-R16 %s: authentication successful", self.client.address
                )
                self.client._auth_ok = True
                if self.client._auth_future and not self.client._auth_future.done():
                    self.client._auth_future.set_result(True)
            elif text == "NO":
                _LOGGER.error(
                    "USR-R16 %s: authentication FAILED — wrong password",
                    self.client.address,
                )
                self.client._auth_ok = False
                if self.client._auth_future and not self.client._auth_future.done():
                    self.client._auth_future.set_exception(
                        InvalidAuth("Wrong password")
                    )
            return

        cmd = raw[3:4]

        if cmd in (b"\x81", b"\x82", b"\x83"):
            # Single relay state change
            port = format(raw[4], "d")
            state = raw[5] == 1
            states[port] = state
            if self.client.states.get(port) is not state:
                changes.append(port)
                self.client.states[port] = state
                _LOGGER.debug("Relay %s → %s (push)", port, state)

        elif cmd in (b"\x84", b"\x85"):
            # All relays ON or all OFF
            state = raw[4] == 1
            for i in range(1, RELAY_COUNT + 1):
                port = format(i, "d")
                states[port] = state
                if self.client.states.get(port) is not state:
                    changes.append(port)
                    self.client.states[port] = state

        elif cmd in (b"\x86", b"\x8a"):
            # Full 16-relay state bitmap
            b1 = format(raw[4], "08b")[::-1]  # relay 1-8 LSB first
            b2 = format(raw[5], "08b")[::-1]  # relay 9-16 LSB first
            for i, bit in enumerate(b1 + b2):
                port = format(i + 1, "d")
                state = bit == "1"
                states[port] = state
                if self.client.states.get(port) is not state:
                    changes.append(port)
                    self.client.states[port] = state
            _LOGGER.debug("Full state update: %s", states)

        elif cmd == b"\xff":
            _LOGGER.debug("Heartbeat packet received from %s", self.client.address)

        else:
            _LOGGER.warning(
                "Unknown packet from %s: %s", self.client.address, raw.hex()
            )

        # Fire push callbacks for changed relays
        for port in changes:
            for cb in self.client.status_callbacks.get(port, []):
                cb(states[port])

        # Resolve pending transaction future
        if self.client.in_transaction:
            self.client.in_transaction = False
            self.client.active_packet = None
            if not self.client.active_transaction.done():
                self.client.active_transaction.set_result(states)
            while self.client.status_waiters:
                w = self.client.status_waiters.popleft()
                if not w.done():
                    w.set_result(states)
            if self.client.waiters:
                self._send_next()
            elif self._cmd_timeout:
                self._cmd_timeout.cancel()
        elif self._cmd_timeout:
            self._cmd_timeout.cancel()

        self._reset_timeout()

    # ------------------------------------------------------------------
    # Packet sending
    # ------------------------------------------------------------------

    def send_packet(self) -> None:
        """Send next queued packet."""
        waiter, packet = self.client.waiters.popleft()
        _LOGGER.debug("Sending packet: %s", packet.hex())
        self.client.active_transaction = waiter
        self.client.in_transaction = True
        self.client.active_packet = packet
        self._reset_cmd_timeout()
        assert self.transport is not None
        self.transport.write(packet)

    def _send_next(self) -> None:
        """Send next packet if queue not empty."""
        if self.client.waiters:
            self.send_packet()

    # ------------------------------------------------------------------
    # Timer management
    # ------------------------------------------------------------------

    def _reset_timeout(self) -> None:
        """Reschedule connection timeout and keepalive."""
        loop = asyncio.get_event_loop()
        if self._timeout:
            self._timeout.cancel()
        assert self.transport is not None
        self._timeout = loop.call_later(self.client.timeout, self.transport.close)
        if self._keep_alive:
            self._keep_alive.cancel()
        self._keep_alive = loop.call_later(
            self.client.keep_alive_interval, self._send_keepalive
        )

    def _reset_cmd_timeout(self) -> None:
        """Reschedule command-response timeout."""
        loop = asyncio.get_event_loop()
        if self._cmd_timeout:
            self._cmd_timeout.cancel()
        assert self.transport is not None
        self._cmd_timeout = loop.call_later(self.client.timeout, self.transport.close)

    def _send_keepalive(self) -> None:
        """Send keepalive (status request) if idle."""
        if not self.client.in_transaction and self.transport:
            packet = self.format_packet("0a")
            _LOGGER.debug("Sending keepalive to %s", self.client.address)
            self.transport.write(packet)

    def _cancel_timers(self) -> None:
        """Cancel all pending timers."""
        for timer in (self._timeout, self._cmd_timeout, self._keep_alive):
            if timer:
                timer.cancel()

    # ------------------------------------------------------------------
    # Packet formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_packet(cmd_str: str, param_str: str = "") -> bytes:
        """Build a USR-R16 binary command packet."""
        head = bytes.fromhex("55aa")
        id_byte = bytes.fromhex("00")
        cmd = bytes.fromhex(cmd_str)
        param = bytes.fromhex(param_str) if param_str else b""
        payload = id_byte + cmd + param
        length = len(payload).to_bytes(2, "big")
        body = length + payload
        checksum = (sum(body) & 0xFF).to_bytes(1, "big")
        return head + body + checksum


class USR16Client:
    """High-level client for the USR-R16 relay board.

    Manages the TCP connection, authentication, keepalives, push callbacks
    and queued commands. Designed for use with Home Assistant's event loop.
    """

    def __init__(
        self,
        host: str,
        port: int = 8899,
        password: str = "admin",
        disconnect_callback=None,
        reconnect_callback=None,
        timeout: int = 10,
        reconnect_interval: int = 10,
        keep_alive_interval: int = 3,
    ) -> None:
        """Initialize client."""
        self.host = host
        self.port = port
        self.address = f"{host}:{port}"
        # Password is sent as ASCII + CR LF
        self._raw_password = password
        self._password_bytes = password.encode() + b"\r\n"
        self.timeout = timeout
        self.reconnect_interval = reconnect_interval
        self.keep_alive_interval = keep_alive_interval
        self.reconnect = True

        self.disconnect_callback = disconnect_callback
        self.reconnect_callback = reconnect_callback

        self.transport: asyncio.Transport | None = None
        self.protocol: "USR16Protocol | None" = None
        self.is_connected = False

        # Command queue
        self.waiters: deque = deque()
        self.status_waiters: deque = deque()
        self.active_transaction: asyncio.Future | None = None
        self.in_transaction = False
        self.active_packet: bytes | None = None

        # State
        self.states: dict[str, bool] = {}
        self.status_callbacks: dict[str, list] = {}

        # Auth
        self._auth_ok = False
        self._auth_future: asyncio.Future | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """Connect, authenticate and fetch initial relay states."""
        loop = asyncio.get_event_loop()
        _LOGGER.debug("Connecting to USR-R16 at %s", self.address)

        try:
            self.transport, self.protocol = await asyncio.wait_for(
                loop.create_connection(
                    lambda: USR16Protocol(self),
                    host=self.host,
                    port=self.port,
                ),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError as err:
            raise CannotConnect(f"Timeout connecting to {self.address}") from err
        except OSError as err:
            raise CannotConnect(f"Cannot connect to {self.address}: {err}") from err

        self.is_connected = True
        _LOGGER.info("Connected to USR-R16 at %s", self.address)

        # Authenticate — wait for OK/NO response
        self._auth_future = loop.create_future()
        self.transport.write(self._password_bytes)
        _LOGGER.debug("Sent password to %s", self.address)

        try:
            await asyncio.wait_for(
                asyncio.shield(self._auth_future), timeout=self.timeout
            )
        except asyncio.TimeoutError as err:
            self.transport.close()
            raise CannotConnect(f"Auth timeout for {self.address}") from err
        except InvalidAuth:
            # Stop reconnect loop before closing so connection_lost doesn't retry
            self.reconnect = False
            self.transport.close()
            raise

        # Fire reconnect callback (signals coordinator the connection is ready)
        if self.reconnect_callback:
            self.reconnect_callback()

    def stop(self) -> None:
        """Shut down the connection."""
        self.reconnect = False
        _LOGGER.debug("Stopping USR-R16 client for %s", self.address)
        if self.transport:
            self.transport.close()

    async def handle_disconnect(self) -> None:
        """Reconnect loop after an unexpected disconnect."""
        self.is_connected = False
        while self.reconnect:
            _LOGGER.info("Reconnecting to USR-R16 at %s", self.address)
            try:
                await self.setup()
                _LOGGER.info("Reconnected to USR-R16 at %s", self.address)
                return
            except InvalidAuth:
                # Wrong password — stop reconnect loop immediately
                _LOGGER.error(
                    "Reconnect aborted for %s: invalid password", self.address
                )
                self.reconnect = False
                return
            except CannotConnect as err:
                _LOGGER.warning("Reconnect failed for %s: %s", self.address, err)
                await asyncio.sleep(self.reconnect_interval)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def register_status_callback(self, callback, switch: str) -> None:
        """Register a callback fired when a relay changes state."""
        self.status_callbacks.setdefault(switch, []).append(callback)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _send(self, packet: bytes) -> asyncio.Future:
        """Enqueue a packet and return a future resolved when acknowledged."""
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self.waiters.append((fut, packet))
        if self.waiters and not self.in_transaction and self.protocol is not None:
            self.protocol.send_packet()
        return fut

    async def turn_on(self, switch: str) -> dict:
        """Close relay (ON)."""
        param = int(switch).to_bytes(1, "big").hex()
        return await self._send(USR16Protocol.format_packet("02", param))

    async def turn_off(self, switch: str) -> dict:
        """Open relay (OFF)."""
        param = int(switch).to_bytes(1, "big").hex()
        return await self._send(USR16Protocol.format_packet("01", param))

    async def toggle(self, switch: str) -> dict:
        """Toggle relay."""
        param = int(switch).to_bytes(1, "big").hex()
        return await self._send(USR16Protocol.format_packet("03", param))
