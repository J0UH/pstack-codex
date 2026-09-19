#!/usr/bin/env python3
"""Server-side JSON sender for a Grok Bot webhook routine (pstack ``make-bot-ui``).

This is the optional Codex host bridge for the upstream skill's "Host the page
on this computer" step.  A page or local server on this machine imports
:func:`send_event` (or runs the CLI) to wake a webhook routine.  Nothing in the
core Codex/Fable workflow depends on it.

The sender key leaves this machine only in the two documented request headers.
It is read at send time from an environment variable named in the config or
from a permission-checked file.  It is never accepted on the command line,
printed, logged, queued, or included in an exception or result.

Preserved upstream contract (``upstream/pstack/skills/make-bot-ui/SKILL.md``):

* ``POST`` to the routine URL copied from the routine panel, exactly
  ``https://api2.cursor.sh/automations/webhook/<id>`` with no query string.
  There is no host override.
* ``Content-Type: application/json``, ``Authorization: Bearer <key>`` and
  ``X-Automation-Key: <key>``.
* body: one JSON object with the fields named in the routine prompt.  No media bytes.
* timeout 8 seconds, one try, no retry, redirects refused, no proxy.
* HTTP 200 means the routine woke.  Every other status is unconfirmed.  The
  response body and headers are never read or recorded.  HTTP 200 is not proof
  that the bot finished anything; that is observed separately in the Bot UI.
* a harmless probe before declaring the UI live, using an action the prompt ignores.
* if a POST fails, the same JSON object is appended as one line to a local
  0600 log (the failure queue).  Draining that log "from the routine" needs a
  separate bridge the routine can reach; this module gives nothing any cloud
  access to local files.

Standard library only.  Python 3.10+ on a POSIX host.  No private API is
invented: the module performs the documented POST and nothing else.  Routine
creation, the sender key and the webhook wake envelope stay in the Grok Bot app
(see ``docs/grok-bot.md``).
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import http.client
import json
import math
import os
import re
import socket
import ssl
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

RESULT_SCHEMA = "pstack-codex/grok-bot-send/1"
CHECK_SCHEMA = "pstack-codex/grok-bot-check/1"

DOCUMENTED_HOST = "api2.cursor.sh"
HOST_POLICY = "documented_default"
WEBHOOK_PATH_RE = re.compile(r"^/automations/webhook/(?P<routine_id>[A-Za-z0-9][A-Za-z0-9._-]{0,255})$")
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

TIMEOUT_SECONDS = 8.0
TIMEOUT_NOTE = (
    "8 s socket timeout applied to connect and to each read; DNS resolution is not "
    "covered and this is not a hard whole-attempt deadline"
)
MAX_URL_CHARS = 2048
MAX_KEY_CHARS = 4096
MAX_BODY_BYTES = 64 * 1024
MAX_CONFIG_BYTES = 1 << 20
USER_AGENT = "pstack-codex-grok-bot/1"
DEFAULT_QUEUE_NAME = "failed-webhook-events.jsonl"
HEADERS_SENT = ["Authorization", "X-Automation-Key", "Content-Type", "User-Agent"]

CONFIG_KEYS = frozenset({"url", "key_env", "key_file", "queue_path", "probe_payload"})
NORMALIZED_KEYS = CONFIG_KEYS | {"host", "routine_id", "config_path"}
FORBIDDEN_CONFIG_KEYS = ("key", "sender_key", "secret", "token")

STATUS_EXIT_CODES = {
    "accepted": 0,
    "rejected": 1,
    "redirect_refused": 1,
    "network_error": 1,
    "timeout": 124,
    "secret_unavailable": 2,
    "invalid_payload": 2,
    "invalid_config": 2,
    "invalid_queue": 2,
    "internal_error": 1,
}
QUEUED_STATUSES = frozenset({"rejected", "redirect_refused", "network_error", "timeout", "secret_unavailable"})
COMPLETION_NOTE = "HTTP 200 means the routine was woken; bot completion is observed in the Grok Bot UI, not here."
REDACTED = "<redacted>"


class ConfigError(ValueError):
    """The config or a config value was rejected.  Messages never contain a key."""


class PayloadError(ValueError):
    """The event payload is not one bounded JSON object, or it contains the key."""


class QueueError(ValueError):
    """The failure queue cannot be used safely.  Nothing is sent when this is raised."""


class UnsafeFileError(ValueError):
    """A key or queue descriptor failed the regular/owner/private checks.  Fixed phrases only.

    ``ident`` is the ``(st_dev, st_ino)`` of the opened file when it was reached.
    """

    def __init__(self, message: str, ident: tuple[int, int] | None = None) -> None:
        super().__init__(message)
        self.ident = ident


class SecretError(ValueError):
    """The sender key could not be obtained safely.  Messages never contain a key.

    ``ident`` is the ``(st_dev, st_ino)`` of the key file when it was opened
    before the failure, so callers can still refuse to write into that file.
    """

    def __init__(self, message: str, ident: tuple[int, int] | None = None) -> None:
        super().__init__(message)
        self.ident = ident


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def scrub(text: Any, secret: str | None) -> str:
    text = str(text)
    if secret and secret in text:
        text = text.replace(secret, REDACTED)
    return text


def exit_code_for(status: str) -> int:
    return STATUS_EXIT_CODES.get(status, 1)


def _os_reason(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def _ident_of(path: str | None) -> tuple[int, int] | None:
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def validate_url(url: Any) -> dict[str, str]:
    """Accept only the documented routine URL shape.  Returns host, path and routine id.

    Strict HTTPS, exactly ``api2.cursor.sh``, the documented path, no query, no
    fragment, no userinfo, no explicit port.  There is no host override.
    """
    if not isinstance(url, str):
        raise ConfigError("url must be a string")
    if not url or len(url) > MAX_URL_CHARS:
        raise ConfigError("url must be a non-empty string of at most %d characters" % MAX_URL_CHARS)
    if not url.isascii() or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        raise ConfigError("url must be printable ASCII without whitespace")
    if "?" in url or "#" in url:
        raise ConfigError("url must not contain a query string or fragment")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise ConfigError("url must use https")
    if parts.netloc.lower() != DOCUMENTED_HOST:
        raise ConfigError(f"url host must be exactly {DOCUMENTED_HOST} with no port or userinfo")
    match = WEBHOOK_PATH_RE.match(parts.path)
    if not match:
        raise ConfigError("url path must be /automations/webhook/<id> copied from the routine panel")
    return {"url": url, "host": DOCUMENTED_HOST, "path": parts.path, "routine_id": match.group("routine_id")}


def _abs_path(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string")
    if any(ch in value for ch in ("\x00", "\n", "\r")):
        raise ConfigError(f"{key} contains control characters")
    if not os.path.isabs(value):
        raise ConfigError(f"{key} must be an absolute path")
    return value


def _unknown_keys(present: Any, allowed: frozenset[str]) -> None:
    unknown = sorted(set(present) - allowed)
    if unknown:
        shown = ", ".join(name[:40] for name in unknown[:5])
        raise ConfigError(f"unknown config keys: {shown}")


def validate_config(raw: Any, config_path: str | None = None) -> dict[str, Any]:
    """Normalize a config object or raise ConfigError.  Unknown keys are rejected.

    ``config_path`` (the file the object came from) supplies the default queue
    path and takes part in the distinct-file checks.
    """
    if not isinstance(raw, dict):
        raise ConfigError("config must be a JSON object")
    if any(not isinstance(key, str) for key in raw):
        raise ConfigError("config keys must be strings")
    for forbidden in FORBIDDEN_CONFIG_KEYS:
        if forbidden in raw:
            raise ConfigError(f"config must not contain {forbidden!r}: reference the sender key through key_env or key_file")
    if "expected_host" in raw:
        raise ConfigError(f"expected_host is not supported: the only host is {DOCUMENTED_HOST} from the routine panel")
    _unknown_keys(raw, CONFIG_KEYS)
    if "url" not in raw:
        raise ConfigError("config missing required key: url")
    target = validate_url(raw["url"])

    has_env = "key_env" in raw
    has_file = "key_file" in raw
    if has_env == has_file:
        raise ConfigError("config must name exactly one of key_env or key_file")
    key_env = None
    key_file = None
    if has_env:
        key_env = raw["key_env"]
        if not isinstance(key_env, str) or not ENV_NAME_RE.match(key_env):
            raise ConfigError("key_env must be an environment variable name such as GROK_BOT_SENDER_KEY")
    else:
        key_file = _abs_path(raw["key_file"], "key_file")

    if config_path is not None:
        config_path = _abs_path(config_path, "config_path")
    queue_path = raw.get("queue_path")
    if queue_path is None:
        if config_path is None:
            raise ConfigError("queue_path is required when the config is not loaded from a file")
        queue_path = os.path.join(os.path.dirname(config_path), DEFAULT_QUEUE_NAME)
    queue_path = _abs_path(queue_path, "queue_path")

    probe_payload = None
    if "probe_payload" in raw:
        try:
            probe_payload = validate_payload(raw["probe_payload"])
        except PayloadError as exc:
            raise ConfigError(f"probe_payload invalid: {exc}") from None

    named = [(name, os.path.normpath(path)) for name, path in (("queue_path", queue_path), ("key_file", key_file), ("config_path", config_path)) if path]
    for index, (name, path) in enumerate(named):
        for other_name, other_path in named[index + 1:]:
            if path == other_path:
                raise ConfigError(f"{name} and {other_name} must be different files")

    return {
        "url": target["url"],
        "host": target["host"],
        "routine_id": target["routine_id"],
        "key_env": key_env,
        "key_file": key_file,
        "queue_path": queue_path,
        "probe_payload": probe_payload,
        "config_path": config_path,
    }


def load_config(path: str) -> dict[str, Any]:
    """Read and validate a JSON config file.  The file must not contain the key itself."""
    if not isinstance(path, str) or not path:
        raise ConfigError("config path must be a non-empty string")
    path = os.path.abspath(path)
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_CONFIG_BYTES + 1)
    except OSError as exc:
        raise ConfigError(f"cannot read config file: {_os_reason(exc)}") from None
    if len(data) > MAX_CONFIG_BYTES:
        raise ConfigError("config file is larger than 1 MiB")
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"config file is not valid JSON: {type(exc).__name__}") from None
    return validate_config(raw, config_path=path)


def _trusted_config(config: Any) -> dict[str, Any]:
    """Derive the permitted host from the URL again because caller dictionaries are untrusted."""
    if not isinstance(config, dict):
        raise ConfigError("config must be the object returned by load_config or validate_config")
    if any(not isinstance(key, str) for key in config):
        raise ConfigError("config keys must be strings")
    _unknown_keys(config, NORMALIZED_KEYS)
    raw = {key: config[key] for key in CONFIG_KEYS if config.get(key) is not None}
    return validate_config(raw, config_path=config.get("config_path"))


def _open_private(path: str, flags: int, *, dir_fd: int | None = None) -> tuple[int, os.stat_result]:
    """Check the opened descriptor to prevent path replacement from defeating file restrictions."""
    try:
        fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise UnsafeFileError("is a symbolic link (not followed)") from None
        raise
    try:
        st = os.fstat(fd)
        ident = (st.st_dev, st.st_ino)
        if not stat.S_ISREG(st.st_mode):
            raise UnsafeFileError("must be a regular file", ident)
        if st.st_uid != os.getuid():
            raise UnsafeFileError("must be owned by the current user", ident)
        if st.st_mode & 0o077:
            raise UnsafeFileError("must not be accessible by group or others (mode 0600)", ident)
    except BaseException:
        os.close(fd)
        raise
    return fd, st


def _read_fd(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write_all(fd: int, data: bytes, write: Callable[[int, Any], int] = os.write) -> None:
    view = memoryview(data)
    while len(view):
        written = write(fd, view)
        if not isinstance(written, int) or written <= 0:
            raise OSError(errno.EIO, "write made no progress")
        view = view[written:]


def _validate_key_text(text: Any, source: str, ident: tuple[int, int] | None = None) -> str:
    if not isinstance(text, str):
        raise SecretError(f"sender key from {source} is not text", ident)
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if not text:
        raise SecretError(f"sender key from {source} is empty", ident)
    if len(text) > MAX_KEY_CHARS:
        raise SecretError(f"sender key from {source} is longer than {MAX_KEY_CHARS} characters", ident)
    if text != text.strip():
        raise SecretError(f"sender key from {source} has leading or trailing whitespace", ident)
    if any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in text):
        raise SecretError(f"sender key from {source} must be one line of printable ASCII", ident)
    return text


def read_secret_file(path: str) -> tuple[str, tuple[int, int]]:
    """Read a sender key from a 0600 regular file owned by this user.

    The file is opened with ``O_NOFOLLOW`` and every check runs on the opened
    descriptor, so there is no window between checking and reading.  Returns the
    key and the file identity ``(st_dev, st_ino)``.
    """
    try:
        fd, st = _open_private(path, os.O_RDONLY)
    except UnsafeFileError as exc:
        raise SecretError(f"key_file {exc}", exc.ident) from None
    except OSError as exc:
        raise SecretError(f"key_file is not accessible: {_os_reason(exc)}") from None
    ident = (st.st_dev, st.st_ino)
    try:
        if st.st_size == 0 or st.st_size > MAX_KEY_CHARS + 2:
            raise SecretError("key_file must contain one key line", ident)
        try:
            data = _read_fd(fd, MAX_KEY_CHARS + 3)
        except OSError as exc:
            raise SecretError(f"key_file could not be read: {_os_reason(exc)}", ident) from None
    finally:
        os.close(fd)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SecretError("key_file is not UTF-8 text", ident) from None
    if "\n" in text.rstrip("\r\n"):
        raise SecretError("key_file must contain exactly one line", ident)
    return _validate_key_text(text, "key_file", ident), ident


def resolve_secret(config: dict[str, Any], environ: dict[str, str] | None = None) -> tuple[str, str, tuple[int, int] | None]:
    """Return ``(key, source_label, key_file_ident)``.  The key must never be stored anywhere."""
    environ = os.environ if environ is None else environ
    if config.get("key_env"):
        name = config["key_env"]
        value = environ.get(name)
        if value is None:
            raise SecretError(f"environment variable {name} is not set in the sender process")
        return _validate_key_text(value, f"environment variable {name}"), f"env:{name}", None
    if config.get("key_file"):
        key, ident = read_secret_file(config["key_file"])
        return key, f"file:{config['key_file']}", ident
    raise SecretError("config names neither key_env nor key_file")


def validate_payload(payload: Any) -> dict[str, Any]:
    """Accept one JSON object of bounded size with JSON-native values.  No bytes, no media."""
    if not isinstance(payload, dict):
        raise PayloadError("payload must be one JSON object")
    if not payload:
        raise PayloadError("payload must name at least one field from the routine prompt")

    def check(value: Any, depth: int) -> None:
        if depth > 16:
            raise PayloadError("payload nesting is too deep")
        if value is None or isinstance(value, (bool, int, float, str)):
            if isinstance(value, float) and not math.isfinite(value):
                raise PayloadError("payload contains a non-finite number")
            return
        if isinstance(value, (bytes, bytearray, memoryview)):
            raise PayloadError("payload must not contain bytes; do not send media on the webhook")
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise PayloadError("payload keys must be strings")
                check(item, depth + 1)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                check(item, depth + 1)
            return
        raise PayloadError(f"payload contains a non-JSON value of type {type(value).__name__}")

    check(payload, 0)
    return payload


def encode_payload(payload: dict[str, Any]) -> bytes:
    """Serialize the validated object exactly once; the same bytes are POSTed, hashed and queued."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise PayloadError(f"payload is {len(body)} bytes; the webhook limit here is {MAX_BODY_BYTES} bytes (no media)")
    return body


def payload_contains(payload: Any, body: bytes, secret: str) -> bool:
    """Check decoded strings too so JSON escaping cannot hide a sender key."""

    def walk(value: Any) -> bool:
        if isinstance(value, str):
            return secret in value
        if isinstance(value, dict):
            return any(walk(key) or walk(item) for key, item in value.items())
        if isinstance(value, (list, tuple)):
            return any(walk(item) for item in value)
        return False

    return walk(payload) or secret.encode("utf-8") in body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so credential headers cannot be forwarded to another destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        return None


def build_request(url: str, key: str, body: bytes) -> urllib.request.Request:
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_unredirected_header("Authorization", f"Bearer {key}")
    request.add_unredirected_header("X-Automation-Key", key)
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    return request


def default_opener(request: urllib.request.Request, timeout: float):
    """Open the request once over verified TLS with redirects refused and no environment proxy."""
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _NoRedirect(),
    )
    return opener.open(request, timeout=timeout)


def _close_quietly(response: Any) -> None:
    try:
        response.close()
    except Exception:
        pass


def post_once(request: urllib.request.Request, timeout: float, opener: Callable | None = None) -> dict[str, Any]:
    """Read status only; an untrusted response body must not delay or leak into the result."""
    opener = default_opener if opener is None else opener
    outcome: dict[str, Any] = {"kind": "network", "http_status": None, "error_class": None}
    try:
        response = opener(request, timeout)
    except urllib.error.HTTPError as exc:
        _close_quietly(exc)
        outcome["http_status"] = int(exc.code)
        outcome["kind"] = "redirect" if 300 <= exc.code < 400 else "response"
    except (socket.timeout, TimeoutError):
        outcome["kind"] = "timeout"
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            outcome["kind"] = "timeout"
        else:
            outcome["error_class"] = type(reason).__name__ if isinstance(reason, BaseException) else "URLError"
    except (OSError, http.client.HTTPException, ValueError) as exc:
        outcome["error_class"] = type(exc).__name__
    else:
        status = getattr(response, "status", None)
        if not isinstance(status, int) and hasattr(response, "getcode"):
            status = response.getcode()
        _close_quietly(response)
        if isinstance(status, int) and not isinstance(status, bool):
            outcome["http_status"] = status
            outcome["kind"] = "redirect" if 300 <= status < 400 else "response"
        else:
            outcome["error_class"] = "NoStatus"
    return outcome


def open_queue(queue_path: str, *, create: bool) -> tuple[int, os.stat_result]:
    """Open the failure queue with ``O_NOFOLLOW`` and descriptor checks.

    ``create=True`` opens for append and creates a 0600 file if missing.  The
    directory must already exist; nothing is created, chmodded or truncated.
    ``create=False`` opens read-only and lets FileNotFoundError through.
    """
    if not os.path.isdir(os.path.dirname(queue_path)):
        raise QueueError("queue_path directory does not exist; this tool does not create directories")
    flags = (os.O_WRONLY | os.O_APPEND | os.O_CREAT) if create else os.O_RDONLY
    directory_fd = None
    try:
        directory_fd = os.open(os.path.dirname(queue_path), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        directory = os.fstat(directory_fd)
        if directory.st_uid != os.getuid() or directory.st_mode & 0o022:
            raise QueueError("queue directory must be owned by the current user and not writable by group or others")
        return _open_private(os.path.basename(queue_path), flags, dir_fd=directory_fd)
    except UnsafeFileError as exc:
        raise QueueError(f"queue_path {exc}; it was left untouched") from None
    except FileNotFoundError:
        if create:
            raise QueueError("queue_path could not be created") from None
        raise
    except OSError as exc:
        raise QueueError(f"queue_path is not accessible: {_os_reason(exc)}") from None
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def append_queue_line(fd: int, body: bytes) -> None:
    """Append the exact encoded event as one line to an already-checked queue descriptor."""
    _write_all(fd, body + b"\n")
    os.fsync(fd)


def inspect_queue(queue_path: str) -> dict[str, Any]:
    """Count events in the failure queue without creating or modifying anything."""
    report: dict[str, Any] = {"queue_path": queue_path, "exists": False, "usable": False, "ident": None, "entries": 0, "malformed_lines": 0, "error": None}
    try:
        fd, st = open_queue(queue_path, create=False)
    except FileNotFoundError:
        report["usable"] = True
        return report
    except QueueError as exc:
        report["error"] = str(exc)
        return report
    report["exists"] = True
    report["ident"] = (st.st_dev, st.st_ino)
    try:
        with os.fdopen(fd, "rb") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    report["malformed_lines"] += 1
                    continue
                if isinstance(value, dict) and value:
                    report["entries"] += 1
                else:
                    report["malformed_lines"] += 1
    except OSError as exc:
        report["error"] = f"queue_path could not be read: {_os_reason(exc)}"
        return report
    report["usable"] = True
    return report


def _base_result(config: dict[str, Any] | None, probe: bool, started_at: str) -> dict[str, Any]:
    config = config or {}
    return {
        "schema": RESULT_SCHEMA,
        "status": "internal_error",
        "exit_code": 1,
        "probe": probe,
        "url": config.get("url"),
        "routine_id": config.get("routine_id"),
        "host_policy": HOST_POLICY,
        "method": "POST",
        "headers_sent": [],
        "timeout_seconds": TIMEOUT_SECONDS,
        "timeout_note": TIMEOUT_NOTE,
        "attempts": 0,
        "retry": False,
        "redirects_followed": False,
        "body_bytes": None,
        "body_sha256": None,
        "http_status": None,
        "http_accepted": False,
        "bot_completion_verified": False,
        "completion_note": COMPLETION_NOTE,
        "secret_source": None,
        "queued": False,
        "queue_path": config.get("queue_path"),
        "errors": [],
        "warnings": [],
        "started_at": started_at,
        "ended_at": None,
        "elapsed_seconds": None,
    }


def _finish(result: dict[str, Any], status: str, clock_start: float, secret: str | None) -> dict[str, Any]:
    result["status"] = status
    result["exit_code"] = exit_code_for(status)
    result["ended_at"] = utc_now()
    result["elapsed_seconds"] = round(time.monotonic() - clock_start, 3)
    if secret:
        def redact(value):
            if isinstance(value, str):
                return scrub(value, secret)
            if isinstance(value, list):
                return [redact(item) for item in value]
            if isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            return value
        cleaned = redact(result)
        if cleaned != result:
            cleaned["errors"].append("internal: a result field contained the sender key and was redacted")
            return cleaned
    return result


def send_event(
    config: dict[str, Any],
    payload: dict[str, Any],
    *,
    probe: bool = False,
    opener: Callable | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """POST one JSON object to the configured routine exactly once and return a result.

    Order: config re-validated against the documented host, payload encoded,
    failure queue opened and checked, key resolved, payload checked for the key,
    one POST, then on failure the same encoded event is appended to the queue.
    Any check that fails stops the sequence before the key is sent anywhere.
    """
    started_at = utc_now()
    clock_start = time.monotonic()
    secret: str | None = None
    queue_fd: int | None = None
    result = _base_result(None, probe, started_at)
    try:
        try:
            trusted = _trusted_config(config)
        except ConfigError as exc:
            result["errors"].append(f"invalid_config: {exc}")
            return _finish(result, "invalid_config", clock_start, None)
        result = _base_result(trusted, probe, started_at)

        try:
            body = encode_payload(validate_payload(payload))
        except PayloadError as exc:
            result["errors"].append(f"invalid_payload: {exc}")
            return _finish(result, "invalid_payload", clock_start, None)
        result["body_bytes"] = len(body)
        result["body_sha256"] = hashlib.sha256(body).hexdigest()

        config_ident = _ident_of(trusted["config_path"])
        try:
            queue_fd, queue_st = open_queue(trusted["queue_path"], create=True)
            queue_ident = (queue_st.st_dev, queue_st.st_ino)
            if config_ident is not None and queue_ident == config_ident:
                raise QueueError("queue_path is the same file as the config file")
        except QueueError as exc:
            result["errors"].append(f"invalid_queue: {exc}; nothing was sent or queued")
            return _finish(result, "invalid_queue", clock_start, None)

        status: str | None = None
        key_ident = None
        try:
            secret, source, key_ident = resolve_secret(trusted, environ)
        except SecretError as exc:
            key_ident = exc.ident or _ident_of(trusted["key_file"])
            status = "secret_unavailable"
            result["errors"].append(f"secret_unavailable: {exc}")
        else:
            result["secret_source"] = source
        if key_ident is not None:
            if key_ident == queue_ident:
                result["errors"].append("invalid_queue: queue_path is the same file as key_file; nothing was sent or queued")
                return _finish(result, "invalid_queue", clock_start, secret)
            if config_ident is not None and key_ident == config_ident:
                result["errors"].append("invalid_config: key_file is the same file as the config file; nothing was sent or queued")
                return _finish(result, "invalid_config", clock_start, secret)

        if status is None:
            if payload_contains(payload, body, secret):
                result["errors"].append("invalid_payload: the payload contains the sender key; it was not sent and not queued")
                return _finish(result, "invalid_payload", clock_start, secret)
            request = build_request(trusted["url"], secret, body)
            result["headers_sent"] = list(HEADERS_SENT)
            outcome = post_once(request, TIMEOUT_SECONDS, opener)
            result["attempts"] = 1
            result["http_status"] = outcome["http_status"]
            kind = outcome["kind"]
            if kind == "response":
                if outcome["http_status"] == 200:
                    status = "accepted"
                    result["http_accepted"] = True
                else:
                    status = "rejected"
                    result["errors"].append(
                        f"rejected: HTTP {outcome['http_status']} is not the documented HTTP 200; the wake is unconfirmed and the response was not read"
                    )
            elif kind == "redirect":
                status = "redirect_refused"
                result["errors"].append(f"redirect_refused: HTTP {outcome['http_status']} redirect not followed; credentials were not re-sent")
            elif kind == "timeout":
                status = "timeout"
                result["errors"].append(f"timeout: no response within {TIMEOUT_SECONDS:g}s; not retried")
            else:
                status = "network_error"
                result["errors"].append(f"network_error: {outcome['error_class']}; not retried")

        if status in QUEUED_STATUSES:
            if probe:
                result["warnings"].append("probe payloads are not queued")
            else:
                try:
                    append_queue_line(queue_fd, body)
                    result["queued"] = True
                except OSError as exc:
                    result["errors"].append(f"queue_append_failed: {_os_reason(exc)}; the event was not preserved")
        return _finish(result, status, clock_start, secret)
    except Exception as exc:
        result["errors"].append(f"internal_error: {type(exc).__name__}")
        return _finish(result, "internal_error", clock_start, secret)
    finally:
        if queue_fd is not None:
            try:
                os.close(queue_fd)
            except OSError:
                pass


def probe_event(config: dict[str, Any], *, opener: Callable | None = None, environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Send the configured harmless probe (an action the routine prompt ignores).  Never queued."""
    if not isinstance(config, dict) or not isinstance(config.get("probe_payload"), dict):
        result = send_event({}, {}, probe=True, opener=opener, environ=environ)
        result["errors"] = ["invalid_config: provide an explicit probe_payload that the routine is known to ignore; no universal probe action is assumed"]
        return result
    return send_event(config, config["probe_payload"], probe=True, opener=opener, environ=environ)


def check_config(path: str, environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Validate the config, URL, secret availability and queue without any network call.  Never prints the key."""
    report: dict[str, Any] = {
        "schema": CHECK_SCHEMA,
        "config_path": os.path.abspath(path) if isinstance(path, str) and path else None,
        "config_valid": False,
        "url": None,
        "routine_id": None,
        "host_policy": None,
        "secret_source": None,
        "secret_available": False,
        "queue_path": None,
        "queue_usable": False,
        "queue_entries": None,
        "network_called": False,
        "errors": [],
        "warnings": [],
    }
    try:
        config = load_config(path)
    except ConfigError as exc:
        report["errors"].append(f"invalid_config: {exc}")
        return report
    report.update(config_valid=True, url=config["url"], routine_id=config["routine_id"], host_policy=HOST_POLICY, queue_path=config["queue_path"])

    config_ident = _ident_of(config["config_path"])
    queue = inspect_queue(config["queue_path"])
    if queue["error"]:
        report["errors"].append(f"invalid_queue: {queue['error']}")
    elif queue["ident"] is not None and queue["ident"] == config_ident:
        report["errors"].append("invalid_queue: queue_path is the same file as the config file")
    else:
        report["queue_usable"] = True
        report["queue_entries"] = queue["entries"]
        if queue["malformed_lines"]:
            report["warnings"].append(f"failure queue has {queue['malformed_lines']} malformed line(s)")

    key_ident = None
    try:
        _, source, key_ident = resolve_secret(config, environ)
    except SecretError as exc:
        key_ident = exc.ident or _ident_of(config["key_file"])
        report["errors"].append(f"secret_unavailable: {exc}")
    else:
        report["secret_source"] = source
        report["secret_available"] = True
    if key_ident is not None:
        if queue["ident"] is not None and key_ident == queue["ident"]:
            report["errors"].append("invalid_queue: queue_path is the same file as key_file")
            report["queue_usable"] = False
            report["queue_entries"] = None
        if config_ident is not None and key_ident == config_ident:
            report["errors"].append("invalid_config: key_file is the same file as the config file")
            report["config_valid"] = False
            report["secret_available"] = False
            report["secret_source"] = None
    return report


def _read_payload(args: argparse.Namespace) -> Any:
    if args.payload_file:
        with open(args.payload_file, "rb") as handle:
            data = handle.read(MAX_BODY_BYTES + 1)
    else:
        data = sys.stdin.buffer.read(MAX_BODY_BYTES + 1)
    if len(data) > MAX_BODY_BYTES:
        raise PayloadError(f"payload is larger than {MAX_BODY_BYTES} bytes (no media)")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PayloadError(f"payload is not valid JSON: {type(exc).__name__}") from None


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class _Parser(argparse.ArgumentParser):

    def error(self, message: str) -> None:  # noqa: D401 - argparse hook
        if message.startswith("unrecognized arguments:"):
            message = "unrecognized arguments (values are never echoed)"
        elif "invalid choice:" in message:
            message = "invalid command or value (values are never echoed); use send, probe, or check"
        super().error(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="grok_bot.py",
        description="POST one JSON event to a Grok Bot webhook routine (server-held key, 8 s, one try).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("send", "send one JSON object from --payload-file or stdin"),
        ("probe", "send the configured harmless probe payload once"),
        ("check", "validate config, URL, secret availability and queue without network"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--config", required=True, help="absolute path to the JSON config (url plus key_env or key_file)")
        if name == "send":
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument("--payload-file", help="JSON object file to send")
            source.add_argument("--stdin", action="store_true", help="read the JSON object from stdin")
    return parser


def main(argv: list[str] | None = None, *, opener: Callable | None = None, environ: dict[str, str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            report = check_config(args.config, environ)
            _emit(report)
            return 0 if report["config_valid"] and report["secret_available"] and report["queue_usable"] else 2
        try:
            config = load_config(args.config)
        except ConfigError as exc:
            result = _base_result(None, args.command == "probe", utc_now())
            result["errors"].append(f"invalid_config: {exc}")
            _emit(_finish(result, "invalid_config", time.monotonic(), None))
            return exit_code_for("invalid_config")
        if args.command == "probe":
            result = probe_event(config, opener=opener, environ=environ)
        else:
            try:
                payload = _read_payload(args)
            except PayloadError as exc:
                result = _base_result(config, False, utc_now())
                result["errors"].append(f"invalid_payload: {exc}")
                _emit(_finish(result, "invalid_payload", time.monotonic(), None))
                return exit_code_for("invalid_payload")
            except OSError as exc:
                result = _base_result(config, False, utc_now())
                result["errors"].append(f"invalid_payload: payload file could not be read: {_os_reason(exc)}")
                _emit(_finish(result, "invalid_payload", time.monotonic(), None))
                return exit_code_for("invalid_payload")
            result = send_event(config, payload, opener=opener, environ=environ)
        _emit(result)
        return result["exit_code"]
    except Exception as exc:
        _emit({"schema": RESULT_SCHEMA, "status": "internal_error", "exit_code": 1, "errors": [f"internal_error: {type(exc).__name__}"]})
        return 1


if __name__ == "__main__":
    sys.exit(main())
