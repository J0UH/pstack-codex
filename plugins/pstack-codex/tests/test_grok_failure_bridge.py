import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bridge_common  # noqa: E402
import grok_bot  # noqa: E402
import grok_failure_bridge  # noqa: E402

URL = "https://api2.cursor.sh/automations/webhook/synthetic-routine-id"
SENDER_KEY = "sk-SENDER-NEVERPRINT-5a1c9e22-0b7d-4f18-a6c3-000000000000"
CONSUMER_TOKEN = "consumer-token-SYNTHETIC-2b9d4f61-83ce-4a07-9d15-00000000beef"


def write_private(path: Path, text: str, mode: int = 0o600) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chmod(str(path), mode)


class FailingOpener:
    def __init__(self):
        self.calls = 0

    def __call__(self, request, timeout):
        self.calls += 1
        raise urllib.error.URLError(ConnectionRefusedError("synthetic refusal"))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pstack grok failures ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.ui = self.root / "ui"
        self.ui.mkdir()
        self.ui.chmod(0o700)
        self.key_file = self.root / "sender.key"
        write_private(self.key_file, SENDER_KEY + "\n")
        self.sender_config = self.ui / "bot.json"
        write_private(self.sender_config, json.dumps({"url": URL, "key_file": str(self.key_file)}))
        self.queue = self.ui / grok_bot.DEFAULT_QUEUE_NAME
        self.state = self.root / "state"
        self.state.mkdir()
        self.state.chmod(0o700)
        self.token_file = self.root / "consumer.token"
        write_private(self.token_file, CONSUMER_TOKEN + "\n")
        self.config_path = self.root / "failures.json"
        self.write_config()

    def write_config(self, **overrides):
        raw = {
            "sender_config": str(self.sender_config),
            "state_dir": str(self.state),
            "listen_host": "127.0.0.1",
            "listen_port": 8788,
            "consumer_token_file": str(self.token_file),
        }
        raw.update(overrides)
        write_private(self.config_path, json.dumps({k: v for k, v in raw.items() if v is not None}))

    def config(self):
        return grok_failure_bridge.load_config(str(self.config_path))

    def store(self):
        return grok_failure_bridge.FailureStore(self.config())

    def fail_send(self, payload):
        result = grok_bot.send_event(grok_bot.load_config(str(self.sender_config)), payload, opener=FailingOpener())
        self.assertEqual(("network_error", True, 1), (result["status"], result["queued"], result["attempts"]))
        return result


class ClaimAckTests(Fixture):
    def test_failed_sends_are_claimable_as_the_same_json_and_the_log_is_never_rewritten(self):
        events = [{"action": "greet", "n": 1}, {"action": "greet", "who": "æ", "n": 2}]
        for event in events:
            self.fail_send(event)
        before = self.queue.read_bytes()
        store = self.store()
        claim = store.claim(limit=10, lease_seconds=60)
        self.assertEqual("claimed", claim["status"])
        self.assertEqual(events, [entry["event"] for entry in claim["entries"]])
        lines = before.split(b"\n")[:-1]
        self.assertEqual([grok_bot.hashlib.sha256(line).hexdigest() for line in lines], [e["body_sha256"] for e in claim["entries"]])
        self.assertEqual(before, self.queue.read_bytes(), "claiming never deletes or rewrites the sender's log")
        self.assertEqual("empty", store.claim(limit=10, lease_seconds=60)["status"], "leased entries are not handed out twice")
        acked = store.ack([{"entry_id": e["entry_id"], "claim_id": e["claim_id"]} for e in claim["entries"]])
        self.assertEqual(["acked", "acked"], [r["result"] for r in acked["results"]])
        self.assertFalse(acked["bot_completion_verified"])
        self.assertEqual("empty", store.claim(limit=10, lease_seconds=60)["status"])
        self.assertEqual(before, self.queue.read_bytes())
        self.assertEqual({"acked": 2}, store.status()["counts"])

    def test_unacked_claims_return_after_the_lease_and_stale_acks_are_refused(self):
        self.fail_send({"action": "one"})
        store = self.store()
        first = store.claim(limit=1, lease_seconds=1)["entries"][0]
        self.assertEqual(1, first["claim_count"])
        time.sleep(1.2)
        second = store.claim(limit=1, lease_seconds=60)["entries"][0]
        self.assertEqual((first["entry_id"], 2), (second["entry_id"], second["claim_count"]))
        self.assertNotEqual(first["claim_id"], second["claim_id"])
        results = store.ack([{"entry_id": first["entry_id"], "claim_id": first["claim_id"]}, {"entry_id": 999, "claim_id": "x"}])["results"]
        self.assertEqual(["stale_claim", "unknown_entry"], [r["result"] for r in results])
        self.assertEqual("acked", store.ack([{"entry_id": second["entry_id"], "claim_id": second["claim_id"]}])["results"][0]["result"])
        self.assertEqual("already_acked", store.ack([{"entry_id": second["entry_id"], "claim_id": second["claim_id"]}])["results"][0]["result"])

    def test_release_returns_an_entry_without_consuming_it(self):
        self.fail_send({"action": "later"})
        store = self.store()
        entry = store.claim(limit=1, lease_seconds=600)["entries"][0]
        self.assertEqual("released", store.release([{"entry_id": entry["entry_id"], "claim_id": entry["claim_id"]}])["results"][0]["result"])
        again = store.claim(limit=1, lease_seconds=600)["entries"][0]
        self.assertEqual(entry["entry_id"], again["entry_id"])

    def test_partial_trailing_line_waits_for_its_newline(self):
        self.fail_send({"action": "complete"})
        with open(self.queue, "ab") as handle:
            handle.write(b'{"action":"part')
        store = self.store()
        claim = store.claim(limit=10, lease_seconds=60)
        self.assertEqual([{"action": "complete"}], [e["event"] for e in claim["entries"]])
        with open(self.queue, "ab") as handle:
            handle.write(b'ial"}\n')
        self.assertEqual([{"action": "partial"}], [e["event"] for e in store.claim(limit=10, lease_seconds=60)["entries"]])

    def test_malformed_lines_are_reported_not_served_or_removed(self):
        write_private(self.queue, 'not json\n[1]\n{}\n{"action":"ok"}\n')
        before = self.queue.read_bytes()
        store = self.store()
        claim = store.claim(limit=10, lease_seconds=60)
        self.assertEqual([{"action": "ok"}], [e["event"] for e in claim["entries"]])
        self.assertEqual({"claimed": 1, "malformed": 3}, store.status()["counts"])
        self.assertEqual(before, self.queue.read_bytes())

    def test_overly_nested_lines_are_malformed_and_do_not_poison_claims(self):
        depth = 500_000
        too_deep_for_sender = '{"a":' * 20 + "1" + "}" * 20
        write_private(self.queue, "[" * depth + "]" * depth + "\n" + too_deep_for_sender + '\n{"action":"ok"}\n')
        store = self.store()
        self.assertEqual([{"action": "ok"}], [e["event"] for e in store.claim(limit=10, lease_seconds=60)["entries"]])
        self.assertEqual({"claimed": 1, "malformed": 2}, store.status()["counts"])

    def test_concurrent_failed_sends_and_consumers_lose_nothing(self):
        total = 40
        store = self.store()
        seen = []
        lock = threading.Lock()
        done = threading.Event()
        sender_config = grok_bot.load_config(str(self.sender_config))

        def sender(offset):
            for index in range(offset, total, 4):
                result = grok_bot.send_event(sender_config, {"action": "count", "n": index}, opener=FailingOpener())
                assert result["queued"], result

        def consumer():
            while True:
                claim = store.claim(limit=3, lease_seconds=60)
                if claim["entries"]:
                    acks = [{"entry_id": e["entry_id"], "claim_id": e["claim_id"]} for e in claim["entries"]]
                    results = store.ack(acks)["results"]
                    assert all(r["result"] == "acked" for r in results), results
                    with lock:
                        seen.extend(e["event"]["n"] for e in claim["entries"])
                elif done.is_set():
                    return
                else:
                    time.sleep(0.01)

        senders = [threading.Thread(target=sender, args=(offset,)) for offset in range(4)]
        consumers = [threading.Thread(target=consumer) for _ in range(3)]
        for thread in senders + consumers:
            thread.start()
        for thread in senders:
            thread.join(60)
        done.set()
        for thread in consumers:
            thread.join(60)
        self.assertEqual(list(range(total)), sorted(seen), "every failure consumed exactly once as a lease, none lost")
        self.assertEqual(total, len(self.queue.read_bytes().splitlines()))

    def test_bridge_never_reads_the_sender_key(self):
        self.fail_send({"action": "x"})
        with unittest.mock.patch.object(grok_bot, "resolve_secret", side_effect=AssertionError("sender key read")), \
                unittest.mock.patch.object(grok_bot, "read_secret_file", side_effect=AssertionError("sender key read")):
            store = self.store()
            self.assertEqual(1, len(store.claim(limit=5, lease_seconds=60)["entries"]))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(0, grok_failure_bridge.main(["check", "--config", str(self.config_path)]))
        self.assertNotIn(SENDER_KEY, out.getvalue())
        self.assertNotIn(CONSUMER_TOKEN, out.getvalue())


class ConfigTests(Fixture):
    def test_consumer_credentials_are_separate_and_private(self):
        alias = self.root / "alias.token"
        os.link(self.key_file, alias)
        cases = {
            "sender_key_path": {"consumer_token_file": str(self.key_file)},
            "sender_key_hardlink": {"consumer_token_file": str(alias)},
            "queue_path": {"consumer_token_file": str(self.queue)},
            "remote_bind": {"listen_host": "0.0.0.0"},
            "relative_state": {"state_dir": "state"},
            "unknown": {"extra": True},
            "inline_token": {"token": CONSUMER_TOKEN},
            "lease": {"max_lease_seconds": 0},
        }
        for name, overrides in cases.items():
            with self.subTest(name=name):
                self.write_config(**overrides)
                with self.assertRaises(grok_failure_bridge.ConfigError) as raised:
                    grok_failure_bridge.load_config(str(self.config_path))
                self.assertNotIn(SENDER_KEY, str(raised.exception))
                self.assertNotIn(CONSUMER_TOKEN, str(raised.exception))
        self.write_config()
        self.token_file.chmod(0o644)
        report = grok_failure_bridge.check(str(self.config_path))
        self.assertFalse(report["ready"])
        self.assertNotIn(CONSUMER_TOKEN, json.dumps(report))


class HttpTests(Fixture):
    def setUp(self):
        super().setUp()
        self.server = grok_failure_bridge.make_server(self.config(), self.store(), port=0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path, body=None, token=CONSUMER_TOKEN):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=data, method="GET" if body is None else "POST")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with self.opener.open(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read()

    def test_claim_and_ack_over_http_with_scoped_auth(self):
        self.fail_send({"action": "http", "n": 1})
        for token in (None, SENDER_KEY, CONSUMER_TOKEN[:-1] + "Z"):
            with self.subTest(token=bool(token)):
                status, raw = self.call("/v1/failures/claim", {"limit": 5}, token=token)
                self.assertEqual(401, status)
                self.assertNotIn(SENDER_KEY.encode(), raw)
        self.assertEqual({}, self.store().status()["counts"], "unauthorized requests do not even import")
        status, raw = self.call("/v1/failures/claim", {"limit": 5, "lease_seconds": 60})
        self.assertEqual(200, status)
        claim = json.loads(raw)
        self.assertEqual([{"action": "http", "n": 1}], [e["event"] for e in claim["entries"]])
        self.assertNotIn(CONSUMER_TOKEN.encode(), raw)
        self.assertNotIn(SENDER_KEY.encode(), raw)
        entry = claim["entries"][0]
        status, raw = self.call("/v1/failures/ack", {"acks": [{"entry_id": entry["entry_id"], "claim_id": entry["claim_id"]}]})
        self.assertEqual(200, status)
        ack = json.loads(raw)
        self.assertEqual("acked", ack["results"][0]["result"])
        self.assertFalse(ack["bot_completion_verified"])
        status, raw = self.call("/v1/failures/status")
        self.assertEqual((200, {"acked": 1}), (status, json.loads(raw)["counts"]))
        self.assertEqual(400, self.call("/v1/failures/claim", {"limit": "all"})[0])
        self.assertEqual(400, self.call("/v1/failures/ack", {"acks": "x"})[0])
        self.assertEqual(404, self.call("/v1/failures/delete", {"entry_id": 1})[0])

    def test_invalid_queue_fails_closed_without_touching_it(self):
        write_private(self.queue, '{"a":1}\n', 0o644)
        status, raw = self.call("/v1/failures/claim", {"limit": 5})
        self.assertEqual(503, status)
        self.assertEqual("queue_unavailable", json.loads(raw)["status"])
        self.assertEqual(0o644, self.queue.stat().st_mode & 0o777)


if __name__ == "__main__":
    unittest.main()
