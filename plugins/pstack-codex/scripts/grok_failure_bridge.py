#!/usr/bin/env python3
"""Authenticated consumer endpoint for the Grok Bot sender's local failure queue.

``grok_bot.py`` appends each failed webhook event, as the exact JSON it tried to
POST, to a 0600 JSONL file and never retries.  A Bot routine runs on another
computer and cannot read that file.  This bridge lets it consume the file over a
loopback HTTP endpoint that the operator exposes through a TLS reverse proxy or
tailnet front end:

* ``POST /v1/failures/claim`` leases up to N entries and returns their JSON.
* ``POST /v1/failures/ack`` marks a leased entry handled, by entry and claim id.
* ``POST /v1/failures/release`` returns a leased entry early.
* ``GET /v1/failures/status`` reports counts.

The sender's file is never truncated, rewritten or deleted: this bridge opens it
read-only with the sender's own checks and imports only complete lines into its
private SQLite state, so concurrent appends are never lost.  A fetch is a lease,
not a deletion; an unacknowledged entry returns after its lease.  An ack is the
consumer's own statement, not observed Bot completion.

The consumer token is separate from the sender key, which this bridge never
reads.  Standard library only.  Python 3.10+ on a POSIX host.
"""
from __future__ import annotations

import argparse
import contextlib
import hmac
import json
import os
import secrets
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

import bridge_common as bc
import grok_bot

DB_NAME = "grok-failures.sqlite3"
CLAIM_SCHEMA = "pstack-codex/grok-failure-claim/1"
ACK_SCHEMA = "pstack-codex/grok-failure-ack/1"
STATUS_SCHEMA = "pstack-codex/grok-failure-status/1"
CHECK_SCHEMA = "pstack-codex/grok-failure-check/1"
MAX_IMPORT_BYTES = 16 << 20
MAX_REQUEST_BYTES = 64 * 1024
MAX_ITEMS = 100
ACK_NOTE = (
    "An ack records the consumer's own statement that it handled the entry. It is not observed Bot completion. "
    "The sender's log keeps every line."
)
CLAIM_NOTE = "Claimed entries are leased, not deleted. Ack each one after handling it; unacknowledged entries return after the lease."

ConfigError = bc.BridgeConfigError

CONFIG_KEYS = frozenset({"sender_config", "state_dir", "listen_host", "listen_port", "consumer_token_file",
                         "default_lease_seconds", "max_lease_seconds", "max_claim"})

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sources(dev INTEGER NOT NULL, ino INTEGER NOT NULL, offset INTEGER NOT NULL, PRIMARY KEY(dev, ino));
CREATE TABLE IF NOT EXISTS entries(
  entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
  dev INTEGER NOT NULL,
  ino INTEGER NOT NULL,
  offset INTEGER NOT NULL,
  length INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  body BLOB NOT NULL,
  state TEXT NOT NULL,
  claim_id TEXT,
  lease_expires REAL,
  claims INTEGER NOT NULL DEFAULT 0,
  imported_at TEXT NOT NULL,
  claimed_at TEXT,
  acked_at TEXT,
  UNIQUE(dev, ino, offset)
);
"""


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_config(raw: Any, config_path: str | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError("config must be a JSON object")
    bc.reject_unknown_and_secret_keys(raw, CONFIG_KEYS)
    sender_path = bc.require_abs_path(raw.get("sender_config"), "sender_config")
    try:
        sender = grok_bot.load_config(sender_path)
    except grok_bot.ConfigError as exc:
        raise ConfigError(f"sender_config is invalid: {exc}") from None
    config: dict[str, Any] = {"config_path": config_path, "sender_config": sender_path, "queue_path": sender["queue_path"],
                              "sender_key_file": sender["key_file"]}
    config["state_dir"] = bc.require_abs_path(raw.get("state_dir"), "state_dir")
    bc.require_private_dir(config["state_dir"], "state_dir")
    config["listen_host"] = bc.require_loopback(raw.get("listen_host", "127.0.0.1"))
    config["listen_port"] = bc.require_int(raw, "listen_port", None, 1, 65535)
    config["consumer_token_file"] = bc.require_abs_path(raw.get("consumer_token_file"), "consumer_token_file")
    config["max_lease_seconds"] = bc.require_int(raw, "max_lease_seconds", 3600, 1, 86400)
    config["default_lease_seconds"] = bc.require_int(raw, "default_lease_seconds", min(300, config["max_lease_seconds"]), 1,
                                                     config["max_lease_seconds"])
    config["max_claim"] = bc.require_int(raw, "max_claim", 20, 1, MAX_ITEMS)

    token = config["consumer_token_file"]
    others = {"the sender key_file": sender["key_file"], "the failure queue": sender["queue_path"],
              "the sender config": sender_path, "this config": config_path}
    token_ident = bc.file_identity(token)
    for label, path in others.items():
        if not path:
            continue
        if os.path.normpath(path) == token or (token_ident is not None and bc.file_identity(path) == token_ident):
            raise ConfigError(f"consumer_token_file must be a separate credential, not {label}")
    return config


def load_config(path: str) -> dict[str, Any]:
    if not isinstance(path, str) or not path:
        raise ConfigError("config path must be a non-empty string")
    path = os.path.abspath(path)
    return validate_config(bc.load_json_object(path), config_path=path)


def _items(value: Any, name: str) -> list[tuple[int, str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ITEMS:
        raise ValueError(f"{name} must be a list of 1 to {MAX_ITEMS} objects")
    items = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"entry_id", "claim_id"}:
            raise ValueError(f"each {name} item must be exactly {{entry_id, claim_id}}")
        entry_id, claim_id = item["entry_id"], item["claim_id"]
        if isinstance(entry_id, bool) or not isinstance(entry_id, int) or not isinstance(claim_id, str) or not 0 < len(claim_id) <= 128:
            raise ValueError("entry_id must be an integer and claim_id a string")
        items.append((entry_id, claim_id))
    return items


class FailureStore:
    """Private import state and leases over the sender's append-only failure log."""

    def __init__(self, config: dict[str, Any]) -> None:
        bc.require_private_dir(config["state_dir"], "state_dir")
        self.config = config
        self.queue_path = config["queue_path"]
        self.path = os.path.join(config["state_dir"], DB_NAME)
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)

    @contextlib.contextmanager
    def connect(self):
        conn = bc.connect_db(self.path)
        try:
            yield conn
        finally:
            conn.close()

    def _import(self, conn: Any) -> None:
        """Copy complete new lines into the store.  The log itself is only ever read."""
        try:
            fd, st = grok_bot.open_queue(self.queue_path, create=False)
        except FileNotFoundError:
            return
        try:
            ident = (st.st_dev, st.st_ino)
            credentials = (self.config["consumer_token_file"], self.config.get("sender_key_file"), self.config["sender_config"],
                           self.config.get("config_path"))
            if ident in {bc.file_identity(path) for path in credentials if path}:
                raise grok_bot.QueueError("queue_path is the same file as a credential or config; nothing was read")
            row = conn.execute("SELECT offset FROM sources WHERE dev=? AND ino=?", ident).fetchone()
            offset = row["offset"] if row else 0
            if st.st_size < offset:
                raise grok_bot.QueueError("queue file is shorter than the imported offset; it was truncated or rewritten in place")
            data = os.pread(fd, min(st.st_size - offset, MAX_IMPORT_BYTES), offset) if st.st_size > offset else b""
        finally:
            os.close(fd)
        end = data.rfind(b"\n")
        if end < 0:
            return
        now = bc.utc_now()
        position = offset
        for line in data[:end + 1].split(b"\n")[:-1]:
            line_offset = position
            position += len(line) + 1
            if not line.strip():
                continue
            try:
                value = json.loads(line.decode("utf-8"))
                # The sender never queues nesting deeper than bc.MAX_JSON_DEPTH; deeper lines are not served.
                bc.validate_json_value(value)
                state = "available" if isinstance(value, dict) and value else "malformed"
            except (UnicodeDecodeError, ValueError, RecursionError):
                state = "malformed"
            conn.execute(
                "INSERT OR IGNORE INTO entries(dev, ino, offset, length, sha256, body, state, imported_at) VALUES(?,?,?,?,?,?,?,?)",
                (*ident, line_offset, len(line), grok_bot.hashlib.sha256(line).hexdigest(), line, state, now),
            )
        conn.execute("INSERT OR REPLACE INTO sources(dev, ino, offset) VALUES(?,?,?)", (*ident, position))

    def claim(self, limit: Any = None, lease_seconds: Any = None) -> dict[str, Any]:
        limit = self.config["max_claim"] if limit is None else limit
        lease = self.config["default_lease_seconds"] if lease_seconds is None else lease_seconds
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self.config["max_claim"]:
            raise ValueError(f"limit must be an integer from 1 to {self.config['max_claim']}")
        if isinstance(lease, bool) or not isinstance(lease, int) or not 1 <= lease <= self.config["max_lease_seconds"]:
            raise ValueError(f"lease_seconds must be an integer from 1 to {self.config['max_lease_seconds']}")
        entries = []
        with self.connect() as conn, bc.transaction(conn):
            self._import(conn)
            now = time.time()
            expires = now + lease
            rows = conn.execute(
                "SELECT entry_id, body, sha256, claims FROM entries WHERE state='available' OR (state='claimed' AND lease_expires<=?)"
                " ORDER BY entry_id LIMIT ?", (now, limit),
            ).fetchall()
            for row in rows:
                claim_id = secrets.token_urlsafe(24)
                conn.execute(
                    "UPDATE entries SET state='claimed', claim_id=?, lease_expires=?, claims=claims+1, claimed_at=? WHERE entry_id=?",
                    (claim_id, expires, bc.utc_now(), row["entry_id"]),
                )
                entries.append({"entry_id": row["entry_id"], "claim_id": claim_id, "claim_count": row["claims"] + 1,
                                "body_sha256": row["sha256"], "event": json.loads(row["body"])})
            remaining = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE state='available' OR (state='claimed' AND lease_expires<=?)", (now,)
            ).fetchone()[0]
        return {"schema": CLAIM_SCHEMA, "status": "claimed" if entries else "empty", "entries": entries, "lease_seconds": lease,
                "lease_expires_at": _iso(expires), "remaining": remaining, "note": CLAIM_NOTE}

    def _settle(self, value: Any, name: str, target: str) -> dict[str, Any]:
        items = _items(value, name)
        results = []
        with self.connect() as conn, bc.transaction(conn):
            for entry_id, claim_id in items:
                row = conn.execute("SELECT state, claim_id FROM entries WHERE entry_id=?", (entry_id,)).fetchone()
                matches = row is not None and hmac.compare_digest((row["claim_id"] or "").encode(), claim_id.encode())
                if row is None or row["state"] == "malformed":
                    result = "unknown_entry"
                elif row["state"] == "acked":
                    result = "already_acked" if matches else "stale_claim"
                elif row["state"] == "claimed" and matches:
                    if target == "acked":
                        conn.execute("UPDATE entries SET state='acked', acked_at=? WHERE entry_id=?", (bc.utc_now(), entry_id))
                    else:
                        conn.execute("UPDATE entries SET state='available', claim_id=NULL, lease_expires=NULL WHERE entry_id=?", (entry_id,))
                    result = target
                else:
                    result = "stale_claim"
                results.append({"entry_id": entry_id, "result": result})
        return {"schema": ACK_SCHEMA, "results": results, "bot_completion_verified": False, "note": ACK_NOTE}

    def ack(self, value: Any) -> dict[str, Any]:
        return self._settle(value, "acks", "acked")

    def release(self, value: Any) -> dict[str, Any]:
        return self._settle(value, "releases", "released")

    def status(self) -> dict[str, Any]:
        with self.connect() as conn:
            counts = {row["state"]: row["n"] for row in conn.execute("SELECT state, COUNT(*) AS n FROM entries GROUP BY state")}
            sources = {(row["dev"], row["ino"]): row["offset"] for row in conn.execute("SELECT dev, ino, offset FROM sources")}
        try:
            st = os.stat(self.queue_path)
            size, imported = st.st_size, sources.get((st.st_dev, st.st_ino), 0)
        except OSError:
            size, imported = None, None
        return {"schema": STATUS_SCHEMA, "counts": counts, "queue_path": self.queue_path, "queue_bytes": size,
                "imported_bytes": imported, "bot_completion_verified": False}


class _FailureHandler(bc.JsonHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server hook
        if self.path != "/v1/failures/status":
            return self.not_found()
        if not self.authorized():
            return self.reject_unauthorized()
        self.send_json(200, self.server.store.status())

    def do_POST(self) -> None:  # noqa: N802 - http.server hook
        store = self.server.store
        routes = {
            "/v1/failures/claim": lambda body: store.claim(body.get("limit"), body.get("lease_seconds")),
            "/v1/failures/ack": lambda body: store.ack(body.get("acks")),
            "/v1/failures/release": lambda body: store.release(body.get("releases")),
        }
        allowed = {"/v1/failures/claim": {"limit", "lease_seconds"}, "/v1/failures/ack": {"acks"}, "/v1/failures/release": {"releases"}}
        if self.path not in routes:
            self.read_body()
            return self.not_found()
        if not self.authorized():
            self.read_body()
            return self.reject_unauthorized()
        body, error = self.read_json(MAX_REQUEST_BYTES)
        if error:
            return self.send_json(error[0], {"status": error[1]})
        if not isinstance(body, dict) or set(body) - allowed[self.path]:
            return self.send_json(400, {"status": "invalid_request", "error": "unexpected request fields"})
        try:
            result = routes[self.path](body)
        except grok_bot.QueueError as exc:
            return self.send_json(503, {"status": "queue_unavailable", "error": str(exc)})
        except ValueError as exc:
            return self.send_json(400, {"status": "invalid_request", "error": str(exc)})
        self.send_json(200, result)


def make_server(config: dict[str, Any], store: FailureStore, *, port: int | None = None):
    token, _ = bc.read_token(config["consumer_token_file"], "consumer_token_file")
    server = bc.make_loopback_server(config["listen_host"], config["listen_port"] if port is None else port, _FailureHandler)
    server.bridge_token = token
    server.store = store
    return server


def check(path: str) -> dict[str, Any]:
    """Offline readiness.  Reads neither the sender key nor the consumer token value into the report."""
    report: dict[str, Any] = {
        "schema": CHECK_SCHEMA, "config_path": os.path.abspath(path) if isinstance(path, str) and path else None,
        "config_valid": False, "token_usable": False, "queue_usable": False, "queue_entries": None, "ready": False,
        "listen": None, "network_bound": False, "sender_key_read": False, "errors": [],
        "warnings": ["Remote consumers reach this loopback endpoint only through an operator-configured TLS reverse proxy or tailnet; "
                     "this check does not prove that the Bot routine can reach it."],
    }
    try:
        config = load_config(path)
    except ConfigError as exc:
        report["errors"].append(f"invalid_config: {exc}")
        return report
    report.update(config_valid=True, listen=f"{config['listen_host']}:{config['listen_port']}", queue_path=config["queue_path"])
    try:
        bc.read_token(config["consumer_token_file"], "consumer_token_file")
        report["token_usable"] = True
    except ConfigError as exc:
        report["errors"].append(str(exc))
    queue = grok_bot.inspect_queue(config["queue_path"])
    if queue["error"]:
        report["errors"].append(f"invalid_queue: {queue['error']}")
    else:
        report["queue_usable"] = True
        report["queue_entries"] = queue["entries"]
    report["ready"] = report["token_usable"] and report["queue_usable"]
    return report


def serve(config: dict[str, Any]) -> int:
    store = FailureStore(config)
    try:
        server = make_server(config, store)
    except OSError as exc:
        bc.emit({"status": "bind_failed", "error": bc.os_reason(exc)})
        return 1
    stop = threading.Event()
    previous = {}
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        signum = getattr(signal, name)
        previous[signum] = signal.signal(signum, lambda *_: stop.set())
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    sys.stdout.write(json.dumps({"status": "serving", "pid": os.getpid(), "listen": f"{config['listen_host']}:{server.server_address[1]}"}) + "\n")
    sys.stdout.flush()
    try:
        while not stop.wait(1.0):
            pass
    finally:
        server.shutdown()
        server.server_close()
        serving.join(5)
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    sys.stdout.write(json.dumps({"status": "stopped", "pid": os.getpid()}) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grok_failure_bridge.py", description="Lease-based consumer endpoint for the Grok Bot failure queue.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("check", "validate config, token and queue offline"), ("serve", "serve the loopback endpoint in the foreground"),
                       ("status", "print claim and ack counts")):
        sub.add_parser(name, help=text).add_argument("--config", required=True)
    sub.add_parser("new-token", help="create a new 0600 consumer token file without printing it").add_argument("--path", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "new-token":
            try:
                bc.emit(bc.new_token(args.path))
                return 0
            except (OSError, ConfigError) as exc:
                bc.emit({"status": "error", "error": bc.os_reason(exc) if isinstance(exc, OSError) else str(exc)})
                return 2
        if args.command == "check":
            report = check(args.config)
            bc.emit(report)
            return 0 if report["ready"] else 2
        try:
            config = load_config(args.config)
        except ConfigError as exc:
            bc.emit({"status": "invalid_config", "error": str(exc)})
            return 2
        if args.command == "serve":
            return serve(config)
        bc.emit(FailureStore(config).status())
        return 0
    except Exception as exc:
        bc.emit({"status": "internal_error", "error": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
