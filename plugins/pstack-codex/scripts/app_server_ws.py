#!/usr/bin/env python3
"""Client-side WebSocket framing for the Codex app-server's Unix-socket transport.

``codex app-server --listen unix://PATH`` speaks WebSocket (RFC 6455) on that
socket: a standard HTTP/1.1 Upgrade handshake, then one JSON-RPC message per
text message.  ``codex app-server proxy`` only relays raw bytes between its
stdio and that socket, so the event bridge performs the handshake and the
framing itself over the proxy's pipes.

The scope is one local connection: no extensions, no subprotocols, no
compression and text messages only.  Anything else fails closed with
:class:`ProtocolError`.

Standard library only.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import struct
from typing import Any

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_HANDSHAKE_BYTES = 16 * 1024
OP_CONTINUATION, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
CONTROL_OPCODES = frozenset({OP_CLOSE, OP_PING, OP_PONG})
MAX_CONTROL_PAYLOAD = 125
CLOSE_NORMAL, CLOSE_PROTOCOL_ERROR, CLOSE_TOO_BIG = 1000, 1002, 1009
STATUS_LINE_RE = re.compile(rb"HTTP/1\.1 101(?: [\t\x20-\x7e]*)?")
HEADER_NAME_RE = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
HEADER_VALUE_RE = re.compile(rb"[\t\x20-\x7e]*")
_INCOMPLETE = object()


class ProtocolError(ValueError):
    """The peer broke the handshake or framing rules; the connection must be dropped."""


class MessageTooBig(ProtocolError):
    """A message is larger than the configured bound."""


def new_key() -> str:
    return base64.b64encode(os.urandom(16)).decode("ascii")


def accept_for(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


def handshake_request(key: str) -> bytes:
    """The client's opening handshake.  The socket is local, so the host and path are nominal."""
    return (
        "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    ).encode("ascii")


def split_response_head(buffer: bytes) -> tuple[bytes, bytes] | None:
    """Return ``(head, rest)`` once the header block is complete, None while it is not."""
    end = buffer.find(b"\r\n\r\n", 0, MAX_HANDSHAKE_BYTES)
    if end < 0:
        if len(buffer) >= MAX_HANDSHAKE_BYTES:
            raise ProtocolError(f"handshake response headers exceed {MAX_HANDSHAKE_BYTES} bytes")
        return None
    return buffer[:end], buffer[end + 4:]


def check_response(head: bytes, key: str) -> None:
    """Validate the server's handshake answer for ``key`` (RFC 6455 section 4.2.2)."""
    status, *lines = head.split(b"\r\n")
    if not STATUS_LINE_RE.fullmatch(status):
        raise ProtocolError(f"expected HTTP/1.1 101 Switching Protocols, got {status[:80]!r}")
    headers: dict[str, list[str]] = {}
    for line in lines:
        name, sep, value = line.partition(b":")
        if not sep or not HEADER_NAME_RE.fullmatch(name) or not HEADER_VALUE_RE.fullmatch(value):
            raise ProtocolError("malformed handshake response header")
        headers.setdefault(name.decode("ascii").lower(), []).append(value.strip(b" \t").decode("ascii"))

    def single(name: str) -> str:
        values = headers.get(name, [])
        if len(values) != 1:
            raise ProtocolError(f"handshake response needs exactly one {name} header")
        return values[0]

    if single("upgrade").lower() != "websocket":
        raise ProtocolError("handshake response does not upgrade to websocket")
    tokens = {token.strip().lower() for value in headers.get("connection", []) for token in value.split(",")}
    if "upgrade" not in tokens:
        raise ProtocolError("handshake response Connection header lacks upgrade")
    if single("sec-websocket-accept") != accept_for(key):
        raise ProtocolError("handshake response has the wrong Sec-WebSocket-Accept")
    for name in ("sec-websocket-extensions", "sec-websocket-protocol"):
        if name in headers:
            raise ProtocolError(f"server selected {name} although none was requested")


def mask(payload: bytes, key: bytes) -> bytes:
    """XOR ``payload`` with the repeating 4-byte ``key``; the same call unmasks."""
    if not payload:
        return b""
    size = len(payload)
    stream = (key * (size // 4 + 1))[:size]
    return (int.from_bytes(payload, "big") ^ int.from_bytes(stream, "big")).to_bytes(size, "big")


def client_frame(opcode: int, payload: bytes, mask_key: bytes | None = None) -> bytes:
    """One final, masked client frame (RFC 6455 section 5.2).  Client messages are never fragmented."""
    key = os.urandom(4) if mask_key is None else mask_key
    size = len(payload)
    if size < 126:
        header = struct.pack("!BB", 0x80 | opcode, 0x80 | size)
    elif size < 1 << 16:
        header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, size)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, size)
    return header + key + mask(payload, key)


def close_payload(code: int | None) -> bytes:
    return b"" if code is None else struct.pack("!H", code)


def _valid_close_code(code: int) -> bool:
    return (1000 <= code <= 1014 and code not in (1004, 1005, 1006)) or 3000 <= code <= 4999


class FrameDecoder:
    """Incremental decoder for server frames.

    :meth:`feed` returns complete events in arrival order: ``("text", bytes)`` with
    fragments joined and UTF-8 checked, ``("ping", bytes)``, ``("pong", bytes)`` and
    ``("close", code_or_None)``.  A frame header announcing more than the message
    bound is refused before its payload is buffered.
    """

    def __init__(self, max_message_bytes: int) -> None:
        self.max_message_bytes = max_message_bytes
        self.closed = False
        self._buffer = bytearray()
        self._parts: list[bytes] | None = None
        self._size = 0

    def feed(self, data: bytes) -> list[tuple[str, Any]]:
        if self.closed:
            if data:
                raise ProtocolError("data after the close frame")
            return []
        self._buffer += data
        events = []
        while not self.closed:
            event = self._step()
            if event is _INCOMPLETE:
                break
            if event is not None:
                events.append(event)
        return events

    def _step(self) -> Any:
        buf = self._buffer
        if len(buf) < 2:
            return _INCOMPLETE
        fin, opcode, length, offset = buf[0] & 0x80, buf[0] & 0x0F, buf[1] & 0x7F, 2
        if buf[0] & 0x70:
            raise ProtocolError("reserved frame bits are set but no extension was negotiated")
        if buf[1] & 0x80:
            raise ProtocolError("server frames must not be masked")
        if opcode in CONTROL_OPCODES:
            if not fin:
                raise ProtocolError("fragmented control frame")
            if length > MAX_CONTROL_PAYLOAD:
                raise ProtocolError("control frame payload exceeds 125 bytes")
        elif opcode == OP_BINARY:
            raise ProtocolError("binary messages are not part of the app-server protocol")
        elif opcode == OP_TEXT and self._parts is not None:
            raise ProtocolError("new text message before the previous one finished")
        elif opcode == OP_CONTINUATION and self._parts is None:
            raise ProtocolError("continuation frame without a message")
        elif opcode not in (OP_TEXT, OP_CONTINUATION):
            raise ProtocolError(f"unknown opcode {opcode:#x}")
        if length == 126:
            if len(buf) < 4:
                return _INCOMPLETE
            length, offset = struct.unpack_from("!H", buf, 2)[0], 4
            if length < 126:
                raise ProtocolError("frame length is not minimally encoded")
        elif length == 127:
            if len(buf) < 10:
                return _INCOMPLETE
            length, offset = struct.unpack_from("!Q", buf, 2)[0], 10
            if length >> 63 or length < 1 << 16:
                raise ProtocolError("frame length is invalid or not minimally encoded")
        if opcode not in CONTROL_OPCODES and self._size + length > self.max_message_bytes:
            raise MessageTooBig(f"message exceeds {self.max_message_bytes} bytes")
        if len(buf) < offset + length:
            return _INCOMPLETE
        payload = bytes(buf[offset:offset + length])
        del buf[:offset + length]

        if opcode == OP_PING:
            return ("ping", payload)
        if opcode == OP_PONG:
            return ("pong", payload)
        if opcode == OP_CLOSE:
            self.closed = True
            if not payload:
                return ("close", None)
            if len(payload) == 1:
                raise ProtocolError("close frame with a one-byte payload")
            code = struct.unpack_from("!H", payload)[0]
            if not _valid_close_code(code):
                raise ProtocolError(f"invalid close code {code}")
            try:
                payload[2:].decode("utf-8")
            except UnicodeDecodeError:
                raise ProtocolError("close reason is not valid UTF-8") from None
            return ("close", code)
        if opcode == OP_TEXT:
            self._parts, self._size = [], 0
        self._parts.append(payload)
        self._size += length
        if not fin:
            return None
        message, self._parts, self._size = b"".join(self._parts), None, 0
        try:
            message.decode("utf-8")
        except UnicodeDecodeError:
            raise ProtocolError("text message is not valid UTF-8") from None
        return ("text", message)
