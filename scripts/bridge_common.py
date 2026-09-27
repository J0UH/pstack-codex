#!/usr/bin/env python3
"""Shared plumbing for the opt-in local bridges (``event_bridge.py`` and
``grok_failure_bridge.py``).

* private-file and private-directory checks made on the opened descriptor or
  on ``lstat`` so a symlink never redirects them
* bearer tokens read from 0600 files, created only through :func:`new_token`,
  compared in constant time and never printed
* SQLite state opened with durable commits and explicit ``BEGIN IMMEDIATE``
  transactions
* a JSON-over-HTTP handler that binds only to loopback.  Remote callers must go
  through an operator-configured TLS reverse proxy or tailnet front end.

Standard library only.  Python 3.10+ on a POSIX host.
"""
from __future__ import annotations

import contextlib
import errno
import hmac
import http.server
import json
import math
import os
import secrets
import socket
import sqlite3
import stat
import sys
from datetime import datetime, timezone
from typing import Any, Iterator

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
TOKEN_MIN_CHARS = 32
TOKEN_MAX_CHARS = 512
MAX_JSON_DEPTH = 16
DRAIN_LIMIT = 2 << 20
FORBIDDEN_SECRET_KEYS = ("token", "secret", "key", "password", "sender_key")


class BridgeConfigError(ValueError):
    """A bridge setting or file is unusable.  Messages never contain a token."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    sys.stdout.flush()


def os_reason(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def require_abs_path(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BridgeConfigError(f"{key} must be a non-empty string")
    if any(ch in value for ch in ("\x00", "\n", "\r")):
        raise BridgeConfigError(f"{key} contains control characters")
    if not os.path.isabs(value):
        raise BridgeConfigError(f"{key} must be an absolute path")
    return os.path.normpath(value)


def require_int(raw: dict, key: str, default: int | None, low: int, high: int) -> int:
    value = raw.get(key, default)
    if value is None:
        raise BridgeConfigError(f"{key} is required")
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BridgeConfigError(f"{key} must be an integer from {low} to {high}")
    return value


def require_loopback(value: Any, key: str = "listen_host") -> str:
    if value not in LOOPBACK_HOSTS:
        raise BridgeConfigError(
            f"{key} must be 127.0.0.1 or ::1; expose it remotely only through an explicit TLS reverse proxy or tailnet front end"
        )
    return value


def reject_unknown_and_secret_keys(raw: dict, allowed: frozenset[str]) -> None:
    if any(not isinstance(key, str) for key in raw):
        raise BridgeConfigError("config keys must be strings")
    for forbidden in FORBIDDEN_SECRET_KEYS:
        if forbidden in raw:
            raise BridgeConfigError(f"config must not contain {forbidden!r}; reference a 0600 token file instead")
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise BridgeConfigError("unknown config keys: " + ", ".join(name[:40] for name in unknown[:5]))


def load_json_object(path: str, limit: int = 1 << 20) -> dict[str, Any]:
    try:
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise BridgeConfigError(f"cannot read config file: {os_reason(exc)}") from None
    if len(data) > limit:
        raise BridgeConfigError("config file is too large")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeConfigError(f"config file is not valid JSON: {type(exc).__name__}") from None
    if not isinstance(value, dict):
        raise BridgeConfigError("config must be a JSON object")
    return value


def require_private_dir(path: str, key: str) -> None:
    """The directory holds state and journals, so nobody else may traverse it."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise BridgeConfigError(f"{key} is not accessible: {os_reason(exc)}") from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise BridgeConfigError(f"{key} must be a real directory, not a symlink")
    if st.st_uid != os.getuid():
        raise BridgeConfigError(f"{key} must be owned by the current user")
    if st.st_mode & 0o077:
        raise BridgeConfigError(f"{key} must not be accessible by group or others (mode 0700)")


def read_owned_text(path: str, key: str, limit: int) -> tuple[str, tuple[int, int]]:
    """Read an operator-owned text file that nobody else can modify."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError as exc:
        reason = "is a symbolic link (not followed)" if exc.errno == errno.ELOOP else f"is not accessible: {os_reason(exc)}"
        raise BridgeConfigError(f"{key} {reason}") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise BridgeConfigError(f"{key} must be a regular file")
        if st.st_uid != os.getuid():
            raise BridgeConfigError(f"{key} must be owned by the current user")
        if st.st_mode & 0o022:
            raise BridgeConfigError(f"{key} must not be writable by group or others")
        if st.st_size > limit:
            raise BridgeConfigError(f"{key} is larger than {limit} bytes")
        data = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    try:
        return data.decode("utf-8"), (st.st_dev, st.st_ino)
    except UnicodeDecodeError:
        raise BridgeConfigError(f"{key} is not UTF-8 text") from None


def read_token(path: str, key: str) -> tuple[str, tuple[int, int]]:
    """Return ``(token, (st_dev, st_ino))`` from a 0600 single-line file owned by this user."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError as exc:
        reason = "is a symbolic link (not followed)" if exc.errno == errno.ELOOP else f"is not accessible: {os_reason(exc)}"
        raise BridgeConfigError(f"{key} {reason}") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise BridgeConfigError(f"{key} must be a regular file")
        if st.st_uid != os.getuid():
            raise BridgeConfigError(f"{key} must be owned by the current user")
        if st.st_mode & 0o077:
            raise BridgeConfigError(f"{key} must not be accessible by group or others (mode 0600)")
        if st.st_size > TOKEN_MAX_CHARS + 2:
            raise BridgeConfigError(f"{key} must contain one token line")
        data = os.read(fd, TOKEN_MAX_CHARS + 3)
    finally:
        os.close(fd)
    text = data.decode("ascii", errors="replace")
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if not TOKEN_MIN_CHARS <= len(text) <= TOKEN_MAX_CHARS or any(ord(ch) <= 0x20 or ord(ch) >= 0x7F for ch in text):
        raise BridgeConfigError(f"{key} must contain one line of {TOKEN_MIN_CHARS}+ printable ASCII characters without spaces")
    return text, (st.st_dev, st.st_ino)


def file_identity(path: str | None) -> tuple[int, int] | None:
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def new_token(path: str) -> dict[str, Any]:
    """Create a new 0600 token file.  Never overwrites and never prints the token."""
    path = require_abs_path(path, "path")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, (secrets.token_urlsafe(32) + "\n").encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return {"status": "created", "path": path, "mode": "0600", "note": "the token was written to the file only; it is not printed"}


def validate_json_value(value: Any, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ValueError("nesting is too deep")
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("contains a non-finite number")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("object keys must be strings")
            validate_json_value(item, depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            validate_json_value(item, depth + 1)
        return
    raise ValueError(f"contains a non-JSON value of type {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """One ASCII line: every newline, control and line-separator character is escaped."""
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True, allow_nan=False)


def connect_db(path: str) -> sqlite3.Connection:
    """Open a private SQLite file with durable commits.  Callers manage transactions."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise BridgeConfigError("state database must be a private regular file owned by the current user")
    finally:
        os.close(fd)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=FULL")
    return conn


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    bridge_token: str = ""


class _Server6(_Server):
    address_family = socket.AF_INET6


def make_loopback_server(host: str, port: int, handler: type[http.server.BaseHTTPRequestHandler]) -> _Server:
    require_loopback(host)
    cls = _Server6 if host == "::1" else _Server
    return cls((host, port), handler)


class JsonHandler(http.server.BaseHTTPRequestHandler):
    """Bearer-authenticated JSON requests.  Nothing is logged; bodies are bounded."""

    server_version = "pstack-bridge/1"
    sys_version = ""
    timeout = 15

    def log_message(self, *_args: Any) -> None:
        return

    def authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        expected = self.server.bridge_token
        if not expected or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:].encode("utf-8", "replace"), expected.encode("ascii"))

    def reject_unauthorized(self) -> None:
        self.send_json(401, {"status": "unauthorized"}, {"WWW-Authenticate": "Bearer"})

    def read_body(self) -> tuple[bytes | None, tuple[int, str] | None]:
        """Read a Content-Length body up to DRAIN_LIMIT so every reply reaches the client intact."""
        if self.headers.get("Transfer-Encoding"):
            return None, (411, "length_required")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None, (400, "invalid_length")
        if length < 0:
            return None, (400, "invalid_length")
        if length > DRAIN_LIMIT:
            return None, (413, "payload_too_large")
        body = self.rfile.read(length) if length else b""
        if len(body) != length:
            return None, (400, "truncated_body")
        return body, None

    def read_json(self, max_bytes: int) -> tuple[Any, tuple[int, str] | None]:
        body, error = self.read_body()
        if error:
            return None, error
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return None, (415, "unsupported_media_type")
        if len(body) > max_bytes:
            return None, (413, "payload_too_large")
        try:
            return json.loads(body.decode("utf-8")), None
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            # A small body can still nest deeply enough to exhaust the parser's recursion limit.
            return None, (400, "invalid_json")

    def send_json(self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def not_found(self) -> None:
        self.send_json(404, {"status": "not_found"})

    def do_GET(self) -> None:  # noqa: N802 - http.server hook
        self.not_found()

    def do_POST(self) -> None:  # noqa: N802 - http.server hook
        self.not_found()
