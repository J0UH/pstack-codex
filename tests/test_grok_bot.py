"""Grok Bot sender contract and regression tests.

Every network interaction here is either an injected opener or a loopback
``http.server`` on 127.0.0.1.  Nothing contacts an external service, no real
sender key exists, and no test is evidence that a live routine accepted a POST.

The regression classes reproduce the review findings against the send boundary:
host override, queue file handling, key-bearing payloads, error-message leaks
and non-200 acceptance.
"""

import contextlib
import email.message
import http.server
import io
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import grok_bot  # noqa: E402

URL = "https://api2.cursor.sh/automations/webhook/synthetic-routine-id"
# Synthetic keys share no 8-character window with any other fixture text (URL, paths, messages),
# so a fragment check can tell a leak from a coincidence.
KEY = "sk-NEVERPRINT-7f3a9c2e-b1d4-4e8a-9f6c"
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
LONG_KEY = "LONG_SYNTHETIC_" + "".join(_ALPHABET[(i * 7) % 62] for i in range(2000))
TRICKY_KEY = 'tk"quoted\\backslash/slash-NEVERPRINT-0042'
RESPONSE_SECRET = "synthetic-response-token-must-not-be-recorded"
EVENT = {"action": "greet", "name": "pstack"}
EVENT_BYTES = b'{"action":"greet","name":"pstack"}'


class NonregularFileTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO test")
    def test_fifo_key_and_queue_are_rejected_without_waiting_for_another_process(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            fifo = base / "pipe"
            os.mkfifo(fifo, 0o600)
            config = base / "config.json"
            config.write_text(json.dumps({"url": URL, "key_file": str(fifo), "queue_path": str(base / "queue")}))
            program = """
import sys
sys.path.insert(0, sys.argv[1])
import grok_bot
try:
    if sys.argv[2] == 'key': grok_bot.read_secret_file(sys.argv[3])
    elif sys.argv[2] == 'check':
        result = grok_bot.check_config(sys.argv[4])
        assert result['secret_available'] is False and result['errors']
        print('rejected'); raise SystemExit(0)
    else: grok_bot.open_queue(sys.argv[3], create=sys.argv[2] == 'queue-create')
except (grok_bot.SecretError, grok_bot.QueueError):
    print('rejected'); raise SystemExit(0)
raise SystemExit('nonregular file accepted')
"""
            for case in ("key", "queue-create", "queue-read", "check"):
                with self.subTest(case=case):
                    result = subprocess.run(
                        [sys.executable, "-c", program, str(Path(grok_bot.__file__).parent), case, str(fifo), str(config)],
                        capture_output=True, text=True, timeout=2,
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertIn("rejected", result.stdout)


class NeverReadBody:
    """A response body that fails the test if anyone reads it."""

    def read(self, *_args):
        raise AssertionError("the response body must never be read")

    readline = readinto = read

    def close(self):
        return None


class FakeResponse:
    def __init__(self, status=200, headers=None):
        self.status = status
        self.headers = headers or {"Set-Cookie": RESPONSE_SECRET}
        self.closed = False

    def read(self, *_args):
        raise AssertionError("the response body must never be read")

    def getcode(self):
        return self.status

    def close(self):
        self.closed = True


class FakeOpener:
    """Records every call; returns or raises the scripted outcome."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if callable(self.outcome):
            return self.outcome()
        return self.outcome


class Tripwire(dict):
    """An environ mapping that fails the test if the sender key is ever looked up."""

    def get(self, *_args, **_kwargs):
        raise AssertionError("the sender key must not be resolved before the boundary checks")

    __getitem__ = get


def http_error(code, location=None):
    headers = email.message.Message()
    if location:
        headers["Location"] = location
    headers["Set-Cookie"] = RESPONSE_SECRET
    return urllib.error.HTTPError(URL, code, f"synthetic {code} {RESPONSE_SECRET}", headers, NeverReadBody())


def write_secret_file(path: Path, text: str, mode: int = 0o600) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chmod(str(path), mode)


def assert_no_fragment(test, text, secret, window=8):
    """No window of ``secret`` (not just the whole value) may appear in ``text``."""
    for start in range(0, max(1, len(secret) - window + 1)):
        piece = secret[start:start + window]
        test.assertNotIn(piece, text, f"key fragment at offset {start} leaked")


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pstack grok bot ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.key_file = self.root / "sender.key"
        write_secret_file(self.key_file, KEY + "\n")
        self.config_path = self.root / "ui" / "bot.json"
        self.config_path.parent.mkdir()
        self.write_config({"url": URL, "key_file": str(self.key_file), "probe_payload": {"action": "probe"}})

    def write_config(self, raw):
        self.config_path.write_text(json.dumps(raw))

    def load(self):
        return grok_bot.load_config(str(self.config_path))

    @property
    def queue_path(self):
        return self.config_path.parent / grok_bot.DEFAULT_QUEUE_NAME

    def send(self, outcome, payload=None, config=None, **kwargs):
        opener = FakeOpener(outcome)
        result = grok_bot.send_event(config or self.load(), EVENT if payload is None else payload, opener=opener, **kwargs)
        return result, opener

    def assert_no_secret(self, value, secret=KEY):
        text = json.dumps(value, ensure_ascii=False, default=str)
        self.assertNotIn(secret, text)
        self.assertNotIn(RESPONSE_SECRET, text)
        assert_no_fragment(self, text, secret)


# --------------------------------------------------------------------------- URL and config


class UrlValidationTests(unittest.TestCase):
    def test_documented_shape_is_accepted(self):
        target = grok_bot.validate_url(URL)
        self.assertEqual(target["host"], "api2.cursor.sh")
        self.assertEqual(target["routine_id"], "synthetic-routine-id")
        self.assertEqual(grok_bot.validate_url("https://API2.cursor.sh/automations/webhook/r1")["host"], "api2.cursor.sh")

    def test_rejects_every_non_documented_shape(self):
        bad = {
            "http": "http://api2.cursor.sh/automations/webhook/abc",
            "query": URL + "?x=1",
            "empty_query": URL + "?",
            "fragment": URL + "#frag",
            "userinfo": "https://user:pw@api2.cursor.sh/automations/webhook/abc",
            "host_as_userinfo": "https://api2.cursor.sh@evil.example/automations/webhook/abc",
            "port": "https://api2.cursor.sh:443/automations/webhook/abc",
            "other_host": "https://evil.example/automations/webhook/abc",
            "example_org": "https://example.org/automations/webhook/abc",
            "subdomain": "https://api2.cursor.sh.evil.example/automations/webhook/abc",
            "prefix_host": "https://xapi2.cursor.sh/automations/webhook/abc",
            "trailing_dot": "https://api2.cursor.sh./automations/webhook/abc",
            "backslash": "https://api2.cursor.sh\\evil.example/automations/webhook/abc",
            "non_ascii": "https://api2.cursor.sh/automations/webhook/abcé",
            "wrong_path": "https://api2.cursor.sh/other/webhook/abc",
            "extra_segment": URL + "/more",
            "trailing_slash": URL + "/",
            "dot_segment": "https://api2.cursor.sh/automations/webhook/../x",
            "empty_id": "https://api2.cursor.sh/automations/webhook/",
            "whitespace": URL + " ",
            "newline": URL + "\n",
            "not_string": 12,
            "too_long": "https://api2.cursor.sh/automations/webhook/" + "a" * 300,
        }
        for name, url in bad.items():
            with self.subTest(name=name), self.assertRaises(grok_bot.ConfigError):
                grok_bot.validate_url(url)

    def test_no_host_override_parameter_exists(self):
        with self.assertRaises(TypeError):
            grok_bot.validate_url("https://example.org/automations/webhook/abc", "example.org")


class ConfigTests(Base):
    def test_nonfinite_payload_is_invalid_before_transport(self):
        for number in (float("inf"), float("-inf")):
            result, opener = self.send(FakeResponse(200), payload={"number": number})
            self.assertEqual(("invalid_payload", 2), (result["status"], result["exit_code"]))
            self.assertEqual([], opener.calls)
            self.assertFalse(self.queue_path.exists())

    def test_refused_key_symlink_cannot_alias_the_failure_queue(self):
        alias = self.root / "alias.key"
        alias.symlink_to(self.key_file)
        before = self.key_file.read_bytes()
        self.write_config({"url": URL, "key_file": str(alias), "queue_path": str(self.key_file)})
        result, opener = self.send(FakeResponse(500))
        self.assertEqual("invalid_queue", result["status"])
        self.assertEqual([], opener.calls)
        self.assertEqual(before, self.key_file.read_bytes())
        self.assertFalse(grok_bot.check_config(str(self.config_path))["queue_usable"])

    def test_queue_requires_an_owned_directory_without_shared_write_access(self):
        self.config_path.parent.chmod(0o777)
        result, opener = self.send(FakeResponse(200))
        self.assertEqual("invalid_queue", result["status"])
        self.assertEqual([], opener.calls)
        self.assertFalse(self.queue_path.exists())

    def test_unknown_positional_argument_does_not_echo_a_key(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            grok_bot.main([KEY])
        self.assertNotIn(KEY, err.getvalue())

    def test_probe_requires_an_explicit_payload_with_known_routine_semantics(self):
        self.write_config({"url": URL, "key_file": str(self.key_file)})
        opener = FakeOpener(FakeResponse(200))
        result = grok_bot.probe_event(self.load(), opener=opener, environ=Tripwire())
        self.assertEqual("invalid_config", result["status"])
        self.assertEqual([], opener.calls)
        self.assertFalse(self.queue_path.exists())

    def test_valid_file_config_defaults_queue_next_to_config(self):
        config = self.load()
        self.assertEqual(config["url"], URL)
        self.assertEqual(config["routine_id"], "synthetic-routine-id")
        self.assertEqual(config["queue_path"], str(self.queue_path))
        self.assertEqual(config["probe_payload"], {"action": "probe"})
        self.assertEqual(config["config_path"], str(self.config_path))
        self.assertEqual(set(config), grok_bot.NORMALIZED_KEYS)

    def test_malformed_and_incomplete_configs_are_rejected(self):
        cases = {
            "not_json": "{not json",
            "not_object": json.dumps([URL]),
            "missing_url": json.dumps({"key_file": str(self.key_file)}),
            "no_key_source": json.dumps({"url": URL}),
            "both_key_sources": json.dumps({"url": URL, "key_file": str(self.key_file), "key_env": "GROK_BOT_SENDER_KEY"}),
            "inline_key": json.dumps({"url": URL, "key_file": str(self.key_file), "key": KEY}),
            "inline_token": json.dumps({"url": URL, "key_file": str(self.key_file), "token": KEY}),
            "unknown_key": json.dumps({"url": URL, "key_file": str(self.key_file), "extra": 1}),
            "expected_host": json.dumps({"url": URL, "key_file": str(self.key_file), "expected_host": "api2.cursor.sh"}),
            "relative_key_file": json.dumps({"url": URL, "key_file": "sender.key"}),
            "bad_env_name": json.dumps({"url": URL, "key_env": "lowercase-name"}),
            "relative_queue": json.dumps({"url": URL, "key_file": str(self.key_file), "queue_path": "queue.jsonl"}),
            "queue_is_key_file": json.dumps({"url": URL, "key_file": str(self.key_file), "queue_path": str(self.key_file)}),
            "queue_is_key_file_dotted": json.dumps({"url": URL, "key_file": str(self.key_file), "queue_path": str(self.root / "ui" / ".." / "sender.key")}),
            "queue_is_config": json.dumps({"url": URL, "key_file": str(self.key_file), "queue_path": str(self.config_path)}),
            "key_file_is_config": json.dumps({"url": URL, "key_file": str(self.config_path)}),
            "bad_probe": json.dumps({"url": URL, "key_file": str(self.key_file), "probe_payload": ["x"]}),
            "bad_url": json.dumps({"url": "http://api2.cursor.sh/automations/webhook/x", "key_file": str(self.key_file)}),
            "other_host_url": json.dumps({"url": "https://example.org/automations/webhook/x", "key_file": str(self.key_file)}),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                self.config_path.write_text(text)
                with self.assertRaises(grok_bot.ConfigError) as raised:
                    grok_bot.load_config(str(self.config_path))
                self.assertNotIn(KEY, str(raised.exception))

    def test_dict_config_requires_explicit_queue_path(self):
        with self.assertRaises(grok_bot.ConfigError):
            grok_bot.validate_config({"url": URL, "key_file": str(self.key_file)})
        config = grok_bot.validate_config({"url": URL, "key_file": str(self.key_file), "queue_path": str(self.root / "q.jsonl")})
        self.assertEqual(config["queue_path"], str(self.root / "q.jsonl"))
        self.assertIsNone(config["config_path"])


class HostOverrideRegressionTests(Base):
    """Finding 1: no host other than api2.cursor.sh may ever receive the credential headers."""

    def test_expected_host_in_file_config_is_rejected_before_any_send(self):
        self.write_config({"url": "https://example.org/automations/webhook/r1", "key_file": str(self.key_file), "expected_host": "example.org"})
        with self.assertRaises(grok_bot.ConfigError) as raised:
            self.load()
        self.assertIn("expected_host", str(raised.exception))
        opener = FakeOpener(FakeResponse(200))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = grok_bot.main(["probe", "--config", str(self.config_path)], opener=opener, environ=Tripwire())
        self.assertEqual(code, 2)
        self.assertEqual(opener.calls, [])
        self.assertEqual(json.loads(out.getvalue())["status"], "invalid_config")

    def test_caller_supplied_dict_config_with_other_host_never_sends_or_reads_key(self):
        queue = str(self.root / "q.jsonl")
        configs = {
            "normalized_looking": {"url": "https://example.org/automations/webhook/r1", "host": "example.org", "routine_id": "r1", "key_env": "GROK_BOT_SENDER_KEY", "queue_path": queue, "probe_payload": {"action": "probe"}, "config_path": None},
            "expected_host_key": {"url": URL, "key_env": "GROK_BOT_SENDER_KEY", "queue_path": queue, "expected_host": "example.org"},
            "spoofed_host_field": {"url": "https://evil.example/automations/webhook/r1", "host": "api2.cursor.sh", "routine_id": "r1", "key_env": "GROK_BOT_SENDER_KEY", "queue_path": queue},
            "raw_only_url": {"url": "https://example.org/automations/webhook/r1"},
            "not_a_dict": ["url"],
        }
        for name, config in configs.items():
            with self.subTest(name=name):
                opener = FakeOpener(FakeResponse(200))
                result = grok_bot.send_event(config, EVENT, opener=opener, environ=Tripwire())
                self.assertEqual(result["status"], "invalid_config")
                self.assertEqual(result["exit_code"], 2)
                self.assertEqual(opener.calls, [])
                self.assertIsNone(result["secret_source"])
                self.assertFalse(result["queued"])
                self.assertFalse(os.path.exists(queue))

    def test_loaded_config_mutated_to_other_host_is_revalidated_at_send(self):
        self.write_config({"url": URL, "key_env": "GROK_BOT_SENDER_KEY"})
        config = self.load()
        config["url"] = "https://example.org/automations/webhook/synthetic-routine-id"
        opener = FakeOpener(FakeResponse(200))
        result = grok_bot.send_event(config, EVENT, opener=opener, environ=Tripwire())
        self.assertEqual(result["status"], "invalid_config")
        self.assertEqual(opener.calls, [])
        self.assertFalse(self.queue_path.exists())

    def test_documented_host_sends_exactly_once_with_documented_headers(self):
        result, opener = self.send(FakeResponse(200))
        self.assertEqual(len(opener.calls), 1)
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, URL)
        self.assertEqual(timeout, 8.0)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["host_policy"], "documented_default")


# --------------------------------------------------------------------------- secret


class SecretTests(Base):
    def test_permission_checked_file_is_read_and_newline_stripped(self):
        key, source, ident = grok_bot.resolve_secret(self.load())
        self.assertEqual(key, KEY)
        self.assertEqual(source, f"file:{self.key_file}")
        st = os.stat(self.key_file)
        self.assertEqual(ident, (st.st_dev, st.st_ino))

    def test_unsafe_or_malformed_key_files_are_rejected_without_disclosure(self):
        cases = {
            "group_readable": (KEY + "\n", 0o640),
            "world_readable": (KEY + "\n", 0o644),
            "group_writable": (KEY + "\n", 0o620),
            "empty": ("", 0o600),
            "only_newline": ("\n", 0o600),
            "two_lines": (KEY + "\nsecond\n", 0o600),
            "control_char": (KEY + "\x01\n", 0o600),
            "non_ascii": (KEY + "é\n", 0o600),
            "padded": ("  " + KEY + "\n", 0o600),
            "too_long": ("k" * 5000 + "\n", 0o600),
        }
        for name, (text, mode) in cases.items():
            with self.subTest(name=name):
                write_secret_file(self.key_file, text, mode)
                with self.assertRaises(grok_bot.SecretError) as raised:
                    grok_bot.resolve_secret(self.load())
                self.assertNotIn(KEY, str(raised.exception))
        self.key_file.unlink()
        with self.assertRaises(grok_bot.SecretError):
            grok_bot.resolve_secret(self.load())
        self.key_file.mkdir()
        with self.assertRaises(grok_bot.SecretError):
            grok_bot.resolve_secret(self.load())

    def test_key_file_symlink_is_not_followed(self):
        real = self.root / "real.key"
        write_secret_file(real, KEY + "\n")
        self.key_file.unlink()
        self.key_file.symlink_to(real)
        with self.assertRaises(grok_bot.SecretError) as raised:
            grok_bot.resolve_secret(self.load())
        self.assertIn("symbolic link", str(raised.exception))
        self.assertNotIn(KEY, str(raised.exception))

    def test_secret_error_carries_file_identity_for_content_failures(self):
        write_secret_file(self.key_file, "", 0o600)
        with self.assertRaises(grok_bot.SecretError) as raised:
            grok_bot.resolve_secret(self.load())
        st = os.stat(self.key_file)
        self.assertEqual(raised.exception.ident, (st.st_dev, st.st_ino))
        self.key_file.chmod(0o644)
        with self.assertRaises(grok_bot.SecretError) as raised:
            grok_bot.resolve_secret(self.load())
        self.assertEqual(raised.exception.ident, (st.st_dev, st.st_ino))

    def test_environment_reference_never_exposes_value(self):
        self.write_config({"url": URL, "key_env": "GROK_BOT_SENDER_KEY"})
        config = self.load()
        key, source, ident = grok_bot.resolve_secret(config, {"GROK_BOT_SENDER_KEY": KEY})
        self.assertEqual((key, source, ident), (KEY, "env:GROK_BOT_SENDER_KEY", None))
        with self.assertRaises(grok_bot.SecretError) as raised:
            grok_bot.resolve_secret(config, {})
        self.assertIn("GROK_BOT_SENDER_KEY", str(raised.exception))
        for bad in (KEY + "\nextra", " " + KEY, KEY + "é", 12):
            with self.assertRaises(grok_bot.SecretError) as raised:
                grok_bot.resolve_secret(config, {"GROK_BOT_SENDER_KEY": bad})
            self.assertNotIn(KEY, str(raised.exception))

    def test_scrub_redacts_secret_in_arbitrary_text(self):
        self.assertEqual(grok_bot.scrub(f"Bearer {KEY} failed", KEY), "Bearer <redacted> failed")
        self.assertEqual(grok_bot.scrub("clean", KEY), "clean")
        self.assertEqual(grok_bot.scrub("clean", None), "clean")


# --------------------------------------------------------------------------- payload


class PayloadTests(unittest.TestCase):
    def test_rejects_non_object_media_and_unbounded_payloads(self):
        cases = {
            "list": ["a"],
            "string": "text",
            "empty": {},
            "bytes": {"file": b"\x89PNG"},
            "nan": {"n": float("nan")},
            "non_json": {"when": object()},
            "int_key": {1: "x"},
            "oversize": {"blob": "x" * (grok_bot.MAX_BODY_BYTES + 1)},
        }
        for name, payload in cases.items():
            with self.subTest(name=name), self.assertRaises(grok_bot.PayloadError):
                grok_bot.encode_payload(grok_bot.validate_payload(payload))
        deep = {"a": 1}
        for _ in range(20):
            deep = {"n": deep}
        with self.assertRaises(grok_bot.PayloadError):
            grok_bot.validate_payload(deep)

    def test_encoding_is_compact_utf8_and_stable(self):
        body = grok_bot.encode_payload({"action": "greet", "who": "æ", "n": [1, 2]})
        self.assertEqual(body, '{"action":"greet","who":"æ","n":[1,2]}'.encode("utf-8"))

    def test_payload_contains_finds_key_in_values_keys_nesting_and_despite_escaping(self):
        for secret in (KEY, TRICKY_KEY, LONG_KEY):
            with self.subTest(secret=secret[:12]):
                hits = {
                    "value": {"note": secret},
                    "embedded": {"note": "prefix " + secret + " suffix"},
                    "nested": {"a": [{"b": {"c": [1, secret]}}]},
                    "dict_key": {secret: 1},
                    "nested_key": {"a": {secret: {"x": 1}}},
                }
                for name, payload in hits.items():
                    body = grok_bot.encode_payload(grok_bot.validate_payload(payload))
                    self.assertTrue(grok_bot.payload_contains(payload, body, secret), name)
        tricky_body = grok_bot.encode_payload({"v": TRICKY_KEY})
        self.assertNotIn(TRICKY_KEY.encode(), tricky_body, "escaping changes the bytes; the object walk must still match")
        self.assertTrue(grok_bot.payload_contains({"v": TRICKY_KEY}, tricky_body, TRICKY_KEY))
        clean = {"action": "greet", "note": KEY[:10], "n": 1, "flag": True, "none": None, "list": ["x"]}
        self.assertFalse(grok_bot.payload_contains(clean, grok_bot.encode_payload(clean), KEY))


# --------------------------------------------------------------------------- queue (finding 2)


class QueueRegressionTests(Base):
    """Finding 2: the queue is opened O_NOFOLLOW, checked on the descriptor, never chmodded, never
    truncated, never the key or config file, and validated before anything is sent."""

    def test_existing_world_readable_queue_is_refused_untouched_and_nothing_is_sent(self):
        self.queue_path.write_bytes(b'{"earlier":1}\n')
        self.queue_path.chmod(0o644)
        self.write_config({"url": URL, "key_env": "GROK_BOT_SENDER_KEY"})
        result, opener = self.send(FakeResponse(200), environ=Tripwire())
        self.assertEqual(result["status"], "invalid_queue")
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(opener.calls, [], "nothing is sent when the queue is invalid")
        self.assertFalse(result["queued"])
        self.assertEqual(stat.S_IMODE(os.stat(self.queue_path).st_mode), 0o644, "someone else's mode is not changed")
        self.assertEqual(self.queue_path.read_bytes(), b'{"earlier":1}\n', "not truncated, not appended")
        self.assertIn("mode 0600", " ".join(result["errors"]))

    def test_queue_symlink_to_key_or_config_is_not_followed(self):
        for name, target in (("key_file", self.key_file), ("config", self.config_path), ("private_other", self.root / "other.txt")):
            with self.subTest(name=name):
                if name == "private_other":
                    write_secret_file(target, "other\n")
                before = target.read_bytes()
                if self.queue_path.exists() or self.queue_path.is_symlink():
                    self.queue_path.unlink()
                self.queue_path.symlink_to(target)
                result, opener = self.send(FakeResponse(200))
                self.assertEqual(result["status"], "invalid_queue")
                self.assertEqual(opener.calls, [])
                self.assertIn("symbolic link", " ".join(result["errors"]))
                self.assertEqual(target.read_bytes(), before, "symlink target untouched")
                self.assertTrue(self.queue_path.is_symlink())
                self.assert_no_secret(result)

    def test_queue_hardlink_to_key_file_is_detected_by_inode_before_any_send(self):
        os.link(self.key_file, self.queue_path)
        result, opener = self.send(FakeResponse(200))
        self.assertEqual(result["status"], "invalid_queue")
        self.assertEqual(opener.calls, [])
        self.assertEqual(self.key_file.read_text(), KEY + "\n", "key file not appended to")
        self.assertIn("same file as key_file", " ".join(result["errors"]))
        self.assert_no_secret(result)

    def test_queue_hardlink_to_empty_key_file_is_not_appended_even_when_secret_fails(self):
        write_secret_file(self.key_file, "", 0o600)
        os.link(self.key_file, self.queue_path)
        result, opener = self.send(FakeResponse(200))
        self.assertEqual(result["status"], "invalid_queue")
        self.assertEqual(opener.calls, [])
        self.assertEqual(self.key_file.read_bytes(), b"")
        self.assertFalse(result["queued"])

    def test_queue_hardlink_to_config_file_is_detected_by_inode(self):
        os.chmod(self.config_path, 0o600)
        before = self.config_path.read_bytes()
        os.link(self.config_path, self.queue_path)
        result, opener = self.send(FakeResponse(200))
        self.assertEqual(result["status"], "invalid_queue")
        self.assertEqual(opener.calls, [])
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_key_file_hardlinked_to_config_is_rejected(self):
        self.key_file.unlink()
        os.chmod(self.config_path, 0o600)
        os.link(self.config_path, self.key_file)
        result, opener = self.send(FakeResponse(200))
        self.assertEqual(result["status"], "invalid_config")
        self.assertEqual(opener.calls, [])
        self.assertFalse(result["queued"])

    def test_queue_directory_or_missing_parent_fails_closed_without_creating_directories(self):
        self.queue_path.mkdir()
        result, opener = self.send(FakeResponse(200))
        self.assertEqual(result["status"], "invalid_queue")
        self.assertEqual(opener.calls, [])
        self.queue_path.rmdir()
        self.write_config({"url": URL, "key_file": str(self.key_file), "queue_path": str(self.root / "missing" / "dir" / "q.jsonl")})
        result, opener = self.send(FakeResponse(200))
        self.assertEqual(result["status"], "invalid_queue")
        self.assertEqual(opener.calls, [])
        self.assertFalse((self.root / "missing").exists())

    def test_new_queue_is_created_private_and_receives_the_exact_encoded_event_lines(self):
        self.assertFalse(self.queue_path.exists())
        result, _ = self.send(http_error(500))
        self.assertTrue(result["queued"])
        self.assertEqual(stat.S_IMODE(os.stat(self.queue_path).st_mode), 0o600)
        self.assertEqual(self.queue_path.read_bytes(), EVENT_BYTES + b"\n")
        second = {"action": "count", "n": 2, "who": "æ"}
        result, _ = self.send(urllib.error.URLError(OSError("refused")), payload=second)
        self.assertTrue(result["queued"])
        lines = self.queue_path.read_bytes().split(b"\n")
        self.assertEqual(lines, [EVENT_BYTES, grok_bot.encode_payload(second), b""])
        self.assertEqual([json.loads(line) for line in lines if line], [EVENT, second])
        self.assertEqual(stat.S_IMODE(os.stat(self.queue_path).st_mode), 0o600)

    def test_existing_private_queue_is_appended_not_truncated(self):
        write_secret_file(self.queue_path, '{"earlier":1}\n')
        result, _ = self.send(http_error(503))
        self.assertTrue(result["queued"])
        self.assertEqual(self.queue_path.read_bytes(), b'{"earlier":1}\n' + EVENT_BYTES + b"\n")

    def test_success_leaves_queue_empty_and_probe_failures_are_never_queued(self):
        result, _ = self.send(FakeResponse(200))
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(self.queue_path.exists(), "queue validated before sending")
        self.assertEqual(self.queue_path.read_bytes(), b"")
        result = grok_bot.probe_event(self.load(), opener=FakeOpener(http_error(500)))
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(result["probe"])
        self.assertFalse(result["queued"])
        self.assertEqual(self.queue_path.read_bytes(), b"")
        self.assertIn("probe payloads are not queued", result["warnings"])

    def test_probe_still_fails_closed_on_an_invalid_queue(self):
        self.queue_path.write_bytes(b"")
        self.queue_path.chmod(0o644)
        opener = FakeOpener(FakeResponse(200))
        result = grok_bot.probe_event(self.load(), opener=opener)
        self.assertEqual(result["status"], "invalid_queue")
        self.assertEqual(opener.calls, [])

    def test_write_all_loops_over_short_writes(self):
        written = []

        def short_write(_fd, view):
            chunk = bytes(view[:3])
            written.append(chunk)
            return len(chunk)

        grok_bot._write_all(99, b"0123456789\n", write=short_write)
        self.assertEqual(b"".join(written), b"0123456789\n")
        self.assertEqual(len(written), 4)
        with self.assertRaises(OSError):
            grok_bot._write_all(99, b"abc", write=lambda _fd, _view: 0)

    def test_queue_append_failure_is_reported_not_hidden(self):
        with unittest.mock.patch.object(grok_bot, "append_queue_line", side_effect=OSError(28, "No space left on device")):
            result, _ = self.send(http_error(503))
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["queued"])
        self.assertIn("queue_append_failed: No space left on device; the event was not preserved", result["errors"])

    def test_inspect_queue_counts_events_and_malformed_lines_without_creating(self):
        report = grok_bot.inspect_queue(str(self.queue_path))
        self.assertEqual((report["exists"], report["usable"], report["entries"]), (False, True, 0))
        self.assertFalse(self.queue_path.exists())
        write_secret_file(self.queue_path, EVENT_BYTES.decode() + "\nnot json\n[1]\n\n{}\n" + EVENT_BYTES.decode() + "\n")
        report = grok_bot.inspect_queue(str(self.queue_path))
        self.assertEqual((report["exists"], report["usable"], report["entries"], report["malformed_lines"]), (True, True, 2, 3))
        self.queue_path.chmod(0o644)
        report = grok_bot.inspect_queue(str(self.queue_path))
        self.assertFalse(report["usable"])
        self.assertIn("mode 0600", report["error"])


# --------------------------------------------------------------------------- key in payload (finding 3)


class KeyInPayloadRegressionTests(Base):
    """Finding 3: an event containing the resolved sender key is never sent and never queued."""

    def secrets(self):
        return {"short": KEY, "tricky": TRICKY_KEY, "long": LONG_KEY}

    def test_key_bearing_payloads_are_refused_before_post_and_never_queued(self):
        self.write_config({"url": URL, "key_env": "GROK_BOT_SENDER_KEY"})
        config = self.load()
        for label, secret in self.secrets().items():
            payloads = {
                "value": {"action": "greet", "note": secret},
                "nested": {"action": "greet", "items": [{"deep": {"x": "a" + secret + "b"}}]},
                "dict_key": {secret: "x"},
            }
            for name, payload in payloads.items():
                with self.subTest(key=label, payload=name):
                    opener = FakeOpener(FakeResponse(200))
                    result = grok_bot.send_event(config, payload, opener=opener, environ={"GROK_BOT_SENDER_KEY": secret})
                    self.assertEqual(result["status"], "invalid_payload")
                    self.assertEqual(result["exit_code"], 2)
                    self.assertEqual(opener.calls, [], "never POSTed")
                    self.assertFalse(result["queued"])
                    self.assertEqual(self.queue_path.read_bytes(), b"", "never queued")
                    self.assert_no_secret(result, secret)
                    self.assertIn("contains the sender key", " ".join(result["errors"]))

    def test_key_bearing_payload_is_refused_with_file_sourced_key_too(self):
        result, opener = self.send(FakeResponse(200), payload={"action": "greet", "note": KEY})
        self.assertEqual(result["status"], "invalid_payload")
        self.assertEqual(opener.calls, [])
        self.assertEqual(self.queue_path.read_bytes(), b"")

    def test_valid_non_secret_payload_failure_is_queued_as_the_same_json(self):
        payload = {"action": "greet", "note": KEY[:10], "n": 1}
        result, opener = self.send(http_error(500), payload=payload)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(len(opener.calls), 1)
        self.assertTrue(result["queued"])
        line = self.queue_path.read_bytes()
        self.assertEqual(line, grok_bot.encode_payload(payload) + b"\n")
        self.assertEqual(json.loads(line), payload)
        self.assertNotIn(KEY.encode(), line)


# --------------------------------------------------------------------------- error leakage (finding 4)


class ErrorLeakRegressionTests(Base):
    """Finding 4: no transport exception message, response body or header reaches the receipt."""

    class Weird(Exception):
        pass

    def configure_env_key(self):
        self.write_config({"url": URL, "key_env": "GROK_BOT_SENDER_KEY"})
        return self.load()

    def test_transport_exceptions_yield_fixed_class_labels_for_short_and_long_keys(self):
        config = self.configure_env_key()
        for label, secret in (("short", KEY), ("long", LONG_KEY), ("tricky", TRICKY_KEY)):
            cases = {
                "oserror": (OSError(f"boom {secret} " + "x" * 500), "network_error", "network_error: OSError; not retried"),
                "urlerror_reason": (urllib.error.URLError(ConnectionRefusedError(f"refused {secret}")), "network_error", "network_error: ConnectionRefusedError; not retried"),
                "urlerror_str_reason": (urllib.error.URLError(f"unknown {secret}"), "network_error", "network_error: URLError; not retried"),
                "gaierror": (urllib.error.URLError(socket.gaierror(8, f"nodename {secret}")), "network_error", "network_error: gaierror; not retried"),
                "http_exception": (grok_bot.http.client.BadStatusLine(f"HTTP/1.1 {secret}"), "network_error", "network_error: BadStatusLine; not retried"),
                "value_error": (ValueError(f"bad {secret}"), "network_error", "network_error: ValueError; not retried"),
                "weird": (self.Weird(f"weird {secret}"), "internal_error", "internal_error: Weird"),
                "timeout": (socket.timeout(f"timed out {secret}"), "timeout", "timeout: no response within 8s; not retried"),
                "wrapped_timeout": (urllib.error.URLError(TimeoutError(secret)), "timeout", "timeout: no response within 8s; not retried"),
            }
            for name, (outcome, status, message) in cases.items():
                with self.subTest(key=label, case=name):
                    opener = FakeOpener(outcome)
                    result = grok_bot.send_event(config, EVENT, opener=opener, environ={"GROK_BOT_SENDER_KEY": secret})
                    self.assertEqual(result["status"], status)
                    self.assertEqual(len(opener.calls), 1)
                    self.assertEqual(result["errors"], [message])
                    self.assert_no_secret(result, secret)
                    text = json.dumps(result, ensure_ascii=False)
                    self.assertNotIn("boom", text)
                    self.assertNotIn("x" * 20, text)

    def test_http_error_message_headers_and_body_are_never_recorded_or_read(self):
        result, opener = self.send(http_error(500))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["http_status"], 500)
        self.assert_no_secret(result)
        self.assertNotIn("synthetic 500", json.dumps(result))
        self.assertNotIn("response_body", json.dumps(result))
        result, _ = self.send(FakeResponse(200, {"Set-Cookie": RESPONSE_SECRET}))
        self.assertEqual(result["status"], "accepted")
        self.assert_no_secret(result)

    def test_slow_response_body_is_not_awaited(self):
        class SlowBody(FakeResponse):
            def read(self, *_args):
                time.sleep(10)
                raise AssertionError("body read")

        started = time.monotonic()
        result, _ = self.send(SlowBody(200))
        self.assertEqual(result["status"], "accepted")
        self.assertLess(time.monotonic() - started, 1.0)

    def test_cli_output_has_no_key_fragments_and_no_traceback(self):
        self.write_config({"url": URL, "key_env": "GROK_BOT_SENDER_KEY"})
        payload = self.root / "event.json"
        payload.write_text(json.dumps(EVENT))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = grok_bot.main(
                ["send", "--config", str(self.config_path), "--payload-file", str(payload)],
                opener=FakeOpener(OSError("boom " + LONG_KEY)),
                environ={"GROK_BOT_SENDER_KEY": LONG_KEY},
            )
        self.assertEqual(code, 1)
        self.assertEqual(err.getvalue(), "")
        assert_no_fragment(self, out.getvalue(), LONG_KEY)
        self.assertEqual(json.loads(out.getvalue())["status"], "network_error")

    def test_finish_scrubs_if_a_field_ever_carried_the_key(self):
        for secret in (KEY, TRICKY_KEY, LONG_KEY):
            with self.subTest(secret_kind=len(secret)):
                result = grok_bot._base_result(None, False, grok_bot.utc_now())
                result["errors"].append(f"unexpected {secret}")
                cleaned = grok_bot._finish(result, "internal_error", time.monotonic(), secret)
                self.assert_no_secret(cleaned, secret)
                self.assertTrue(any("redacted" in e for e in cleaned["errors"]))


# --------------------------------------------------------------------------- acceptance (finding 5)


class AcceptanceRegressionTests(Base):
    """Finding 5: exactly HTTP 200 is acceptance; the response body is never drained."""

    def test_only_http_200_is_accepted(self):
        result, opener = self.send(FakeResponse(200))
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["http_accepted"])
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["queued"])
        self.assertFalse(result["bot_completion_verified"])
        for code in (201, 202, 204, 226, 100):
            with self.subTest(code=code):
                result, opener = self.send(FakeResponse(code))
                self.assertEqual(len(opener.calls), 1)
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(result["exit_code"], 1)
                self.assertFalse(result["http_accepted"])
                self.assertEqual(result["http_status"], code)
                self.assertTrue(result["queued"])
                self.assertIn("unconfirmed", " ".join(result["errors"]))
        for code in (400, 401, 403, 404, 429, 500, 503):
            with self.subTest(code=code):
                result, opener = self.send(http_error(code))
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(result["http_status"], code)
                self.assertFalse(result["http_accepted"])
                self.assertTrue(result["queued"])

    def test_redirects_are_refused_and_queued(self):
        for name, outcome in (("http_error_302", http_error(302, "https://evil.example/collect")), ("object_307", FakeResponse(307)), ("object_308", FakeResponse(308))):
            with self.subTest(name=name):
                result, opener = self.send(outcome)
                self.assertEqual(len(opener.calls), 1)
                self.assertEqual(result["status"], "redirect_refused")
                self.assertFalse(result["http_accepted"])
                self.assertFalse(result["redirects_followed"])
                self.assertTrue(result["queued"])
                self.assertNotIn("evil.example", json.dumps(result))

    def test_accepted_post_uses_documented_headers_timeout_and_one_attempt(self):
        result, opener = self.send(FakeResponse(200))
        self.assertEqual(len(opener.calls), 1)
        request, timeout = opener.calls[0]
        self.assertEqual(timeout, 8.0)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, URL)
        self.assertEqual(request.get_header("Authorization"), f"Bearer {KEY}")
        self.assertEqual(request.get_header("X-automation-key"), KEY)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("User-agent"), grok_bot.USER_AGENT)
        self.assertEqual(request.data, EVENT_BYTES)
        self.assertEqual(result["attempts"], 1)
        self.assertFalse(result["retry"])
        self.assertEqual(result["timeout_seconds"], 8.0)
        self.assertIn("not a hard whole-attempt deadline", result["timeout_note"])
        self.assertEqual(result["body_sha256"], grok_bot.hashlib.sha256(request.data).hexdigest())
        self.assertEqual(result["headers_sent"], ["Authorization", "X-Automation-Key", "Content-Type", "User-Agent"])
        self.assertNotIn("response_body_bytes", result)
        self.assertNotIn("response_body_sha256", result)
        self.assert_no_secret(result)

    def test_missing_secret_queues_event_without_any_attempt(self):
        self.key_file.chmod(0o644)
        result, opener = self.send(FakeResponse(200))
        self.assertEqual(result["status"], "secret_unavailable")
        self.assertEqual(opener.calls, [])
        self.assertTrue(result["queued"])
        self.assertEqual(self.queue_path.read_bytes(), EVENT_BYTES + b"\n")
        self.assertIsNone(result["secret_source"])
        self.assert_no_secret(result)

    def test_invalid_payload_does_not_attempt_or_queue(self):
        result, opener = self.send(FakeResponse(200), payload={"file": b"bytes"})
        self.assertEqual(result["status"], "invalid_payload")
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(opener.calls, [])
        self.assertFalse(result["queued"])
        self.assertFalse(self.queue_path.exists())

    def test_probe_uses_configured_harmless_payload(self):
        self.write_config({"url": URL, "key_file": str(self.key_file), "probe_payload": {"action": "ignore-me"}})
        opener = FakeOpener(FakeResponse(200))
        result = grok_bot.probe_event(self.load(), opener=opener)
        self.assertTrue(result["probe"])
        self.assertEqual(opener.calls[0][0].data, b'{"action":"ignore-me"}')
        self.assertEqual(result["status"], "accepted")
        self.assertFalse(result["bot_completion_verified"])
        self.assertEqual(grok_bot.probe_event({"url": URL}, opener=opener)["status"], "invalid_config")

    def test_check_config_reports_readiness_without_network_or_secret(self):
        report = grok_bot.check_config(str(self.config_path))
        self.assertTrue(report["config_valid"])
        self.assertTrue(report["secret_available"])
        self.assertTrue(report["queue_usable"])
        self.assertFalse(report["network_called"])
        self.assertEqual(report["queue_entries"], 0)
        self.assertEqual(report["host_policy"], "documented_default")
        self.assertEqual(report["warnings"], [])
        self.assertFalse(self.queue_path.exists(), "check never creates the queue")
        self.assert_no_secret(report)
        for key in ("schema", "config_path", "config_valid", "url", "routine_id", "host_policy", "secret_source", "secret_available", "queue_path", "queue_usable", "queue_entries", "network_called", "errors", "warnings"):
            self.assertIn(key, report, "documented check report key")
        self.key_file.chmod(0o644)
        report = grok_bot.check_config(str(self.config_path))
        self.assertFalse(report["secret_available"])
        self.assertTrue(report["config_valid"])
        self.assert_no_secret(report)

    def test_check_config_reports_unusable_queues(self):
        self.queue_path.write_bytes(b"")
        self.queue_path.chmod(0o644)
        report = grok_bot.check_config(str(self.config_path))
        self.assertFalse(report["queue_usable"])
        self.assertIsNone(report["queue_entries"])
        self.assertTrue(any(e.startswith("invalid_queue") for e in report["errors"]))
        self.queue_path.unlink()
        os.link(self.key_file, self.queue_path)
        report = grok_bot.check_config(str(self.config_path))
        self.assertFalse(report["queue_usable"])
        self.assertIn("invalid_queue: queue_path is the same file as key_file", report["errors"])
        self.assert_no_secret(report)
        self.queue_path.unlink()
        self.queue_path.symlink_to(self.key_file)
        report = grok_bot.check_config(str(self.config_path))
        self.assertFalse(report["queue_usable"])
        self.assertIn("symbolic link", " ".join(report["errors"]))


# --------------------------------------------------------------------------- loopback transport


class _LoopbackHandler(http.server.BaseHTTPRequestHandler):
    seen = []
    mode = "ok"
    lock = threading.Lock()

    def log_message(self, *_args):  # silence
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        with self.lock:
            self.seen.append({"path": self.path, "headers": dict(self.headers.items()), "body": body})
        if self.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/collect")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.mode == "slow_headers":
            time.sleep(0.8)
        payload = RESPONSE_SECRET.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Set-Cookie", RESPONSE_SECRET)
        if self.mode == "slow_body":
            self.send_header("Content-Length", str(len(payload) * 1000))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
            time.sleep(1.5)
            return
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class LoopbackBoundaryTests(unittest.TestCase):
    """Real urllib transport against a loopback server: header delivery, redirect refusal, timeout,
    and no body read.  Transport-level only; send_event never accepts a loopback URL."""

    def setUp(self):
        _LoopbackHandler.seen = []
        _LoopbackHandler.mode = "ok"
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _LoopbackHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/automations/webhook/loopback"

    def post(self, timeout=5.0):
        request = grok_bot.build_request(self.url, KEY, b'{"action":"probe"}')
        return grok_bot.post_once(request, timeout)

    def test_headers_and_body_reach_the_server_and_response_content_is_not_kept(self):
        outcome = self.post()
        self.assertEqual(outcome, {"kind": "response", "http_status": 200, "error_class": None})
        self.assertEqual(len(_LoopbackHandler.seen), 1)
        seen = _LoopbackHandler.seen[0]
        self.assertEqual(seen["headers"]["Authorization"], f"Bearer {KEY}")
        self.assertEqual(seen["headers"]["X-Automation-Key"], KEY)
        self.assertEqual(seen["headers"]["Content-Type"], "application/json")
        self.assertEqual(seen["headers"]["User-Agent"], grok_bot.USER_AGENT)
        self.assertEqual(seen["body"], b'{"action":"probe"}')
        self.assertNotIn(RESPONSE_SECRET, json.dumps(outcome))

    def test_redirect_is_refused_and_credentials_are_not_re_sent(self):
        _LoopbackHandler.mode = "redirect"
        outcome = self.post()
        self.assertEqual(outcome["kind"], "redirect")
        self.assertEqual(outcome["http_status"], 302)
        time.sleep(0.1)
        self.assertEqual([s["path"] for s in _LoopbackHandler.seen], ["/automations/webhook/loopback"])

    def test_timeout_is_classified_without_retry(self):
        _LoopbackHandler.mode = "slow_headers"
        outcome = self.post(timeout=0.2)
        self.assertEqual(outcome["kind"], "timeout")
        time.sleep(1.0)
        self.assertEqual(len(_LoopbackHandler.seen), 1)

    def test_unfinished_body_is_not_awaited(self):
        _LoopbackHandler.mode = "slow_body"
        started = time.monotonic()
        outcome = self.post(timeout=5.0)
        self.assertEqual(outcome["kind"], "response")
        self.assertEqual(outcome["http_status"], 200)
        self.assertLess(time.monotonic() - started, 1.0, "status only; the body is never drained")

    def test_send_event_never_accepts_a_loopback_url(self):
        result = grok_bot.send_event({"url": self.url, "key_env": "K", "queue_path": "/tmp/never.jsonl"}, EVENT, environ=Tripwire())
        self.assertEqual(result["status"], "invalid_config")
        self.assertEqual(_LoopbackHandler.seen, [])


# --------------------------------------------------------------------------- CLI


class CliTests(Base):
    def run_cli(self, argv, opener=None, environ=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = grok_bot.main(argv, opener=opener, environ=environ)
        self.assertEqual(err.getvalue(), "")
        return code, json.loads(out.getvalue())

    def test_send_and_probe_print_scrubbed_results(self):
        payload = self.root / "event.json"
        payload.write_text(json.dumps({"action": "greet"}))
        code, result = self.run_cli(["send", "--config", str(self.config_path), "--payload-file", str(payload)], FakeOpener(FakeResponse(200)))
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "accepted")
        self.assert_no_secret(result)
        code, result = self.run_cli(["probe", "--config", str(self.config_path)], FakeOpener(http_error(500)))
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(result["probe"])
        self.assertFalse(result["queued"])
        self.assert_no_secret(result)

    def test_send_reads_stdin(self):
        with unittest.mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(EVENT_BYTES))):
            code, result = self.run_cli(["send", "--config", str(self.config_path), "--stdin"], FakeOpener(http_error(500)))
        self.assertEqual((code, result["status"]), (1, "rejected"))
        self.assertEqual(self.queue_path.read_bytes(), EVENT_BYTES + b"\n")

    def test_cli_never_accepts_the_key_url_or_removed_commands(self):
        stderr = io.StringIO()
        for argv in (["send", "--config", str(self.config_path), "--stdin", "--key", KEY],
                     ["send", "--config", str(self.config_path), "--stdin", "--url", URL],
                     ["send", "--config", str(self.config_path), "--stdin", "--expected-host", "example.org"],
                     ["probe", "--config", str(self.config_path), "--key-file", str(self.key_file)],
                     ["queue", "--config", str(self.config_path)]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                grok_bot.main(argv, opener=FakeOpener(FakeResponse(200)))
            self.assertEqual(raised.exception.code, 2)
        self.assertNotIn(KEY, stderr.getvalue())

    def test_check_and_error_paths(self):
        code, report = self.run_cli(["check", "--config", str(self.config_path)])
        self.assertEqual(code, 0)
        self.assertTrue(report["secret_available"])
        self.assertTrue(report["queue_usable"])
        self.assert_no_secret(report)
        self.key_file.chmod(0o644)
        code, report = self.run_cli(["check", "--config", str(self.config_path)])
        self.assertEqual(code, 2)
        self.assert_no_secret(report)
        self.key_file.chmod(0o600)
        self.queue_path.write_bytes(b"")
        self.queue_path.chmod(0o644)
        code, report = self.run_cli(["check", "--config", str(self.config_path)])
        self.assertEqual(code, 2)
        self.assertFalse(report["queue_usable"])
        self.queue_path.unlink()
        bad_payload = self.root / "bad.json"
        bad_payload.write_text("{nope")
        code, result = self.run_cli(["send", "--config", str(self.config_path), "--payload-file", str(bad_payload)], FakeOpener(FakeResponse(200)))
        self.assertEqual((code, result["status"]), (2, "invalid_payload"))
        code, result = self.run_cli(["send", "--config", str(self.config_path), "--payload-file", str(self.root / "missing.json")], FakeOpener(FakeResponse(200)))
        self.assertEqual((code, result["status"]), (2, "invalid_payload"))
        self.assertFalse(self.queue_path.exists())
        self.config_path.write_text("{broken")
        code, result = self.run_cli(["send", "--config", str(self.config_path), "--payload-file", str(bad_payload)])
        self.assertEqual((code, result["status"]), (2, "invalid_config"))
        code, report = self.run_cli(["check", "--config", str(self.config_path)])
        self.assertEqual((code, report["config_valid"]), (2, False))


if __name__ == "__main__":
    unittest.main()
