"""Exercise the worker lifecycle with real local Python subprocesses only."""

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import worker_common as worker


def parse_fixture(events, spec):
    results = [event for event in events if event.get("type") == "result"]
    result = results[-1] if results else {}
    return {
        "result_text": result.get("text", ""),
        "observed_models": [result["model"]] if result.get("model") else [],
        "is_error": result.get("error", False),
        "complete": bool(results),
    }


class WorkerCommonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pstack fake worker ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.prompt = self.root / "prompt with spaces.txt"
        self.prompt.write_text("Synthetic prompt: quotes ' \" and unicode æ")
        self.count = 0

    def spec(self, **changes):
        self.count += 1
        return {
            "backend": "claude", "model": "fixture-model", "effort": "xhigh", "profile": "analysis",
            "cwd": str(self.root), "prompt_file": str(self.prompt),
            "run_dir": str(self.root / f"attempt {self.count}"), "timeout_seconds": 3,
            "term_grace_seconds": 0.1, **changes,
        }

    def run_child(self, body, spec=None, stdin=None, parser=parse_fixture):
        spec = spec or self.spec()
        script = self.root / f"child {self.count}.py"
        script.write_text(body)
        return worker.run_process(spec, [sys.executable, str(script)], parser, stdin_text=stdin,
                                  env={"PATH": os.defpath, "RUN_DIR": spec["run_dir"]})

    def test_stdin_spaces_launch_order_and_private_artifacts(self):
        payload = self.prompt.read_text() * 5000
        receipt = self.run_child(
            "import json,os,sys\n"
            "from pathlib import Path\n"
            "launch=json.loads((Path(os.environ['RUN_DIR'])/'launch.json').read_text())\n"
            "assert launch['stage']=='launch_intent'\n"
            "assert os.stat(os.environ['RUN_DIR']).st_mode & 0o777 == 0o700\n"
            "text=sys.stdin.read()\n"
            "print(json.dumps({'type':'result','model':'fixture-model','text':text}))\n",
            stdin=payload,
        )
        self.assertEqual("success", receipt["status"])
        self.assertTrue(receipt["requested_model_verified"])
        self.assertEqual(payload, Path(receipt["result_path"]).read_text())
        self.assertEqual(hashlib.sha256(payload.encode()).hexdigest(), receipt["stdin_sha256"])
        self.assertEqual(str(self.root), receipt["cwd"])
        self.assertNotIn(payload, json.dumps(receipt))
        for path in worker.artifact_paths(receipt["run_dir"]).values():
            self.assertEqual(0o600, Path(path).stat().st_mode & 0o777)
        self.assertEqual("finished", json.loads(Path(receipt["process_path"]).read_text())["stage"])

    def test_success_requires_complete_clean_matching_transport(self):
        cases = [
            ("print('{\"type\":\"result\",\"model\":\"other\",\"text\":\"answer\"}')", "model_mismatch"),
            ("print('{\"type\":\"partial\"}')", "incomplete"),
            ("print('{\"type\":\"result\",\"model\":\"fixture-model\",\"error\":true}')", "provider_error"),
            ("print('{\"type\":\"result\",\"model\":\"fixture-model\"}'); raise SystemExit(7)", "process_failed"),
            ("print('{\"type\":\"result\",\"text\":\"unattributed\"}')", "unverified"),
        ]
        for body, expected in cases:
            with self.subTest(expected=expected):
                receipt = self.run_child(body)
                self.assertEqual(expected, receipt["status"])
                self.assertFalse(receipt["requested_model_verified"])
                self.assertNotEqual(0, receipt["exit_code"])

    def test_truncated_stream_and_parser_exception_keep_receipts(self):
        receipt = self.run_child("import sys; sys.stdout.write('{\"type\":')")
        self.assertEqual("incomplete", receipt["status"])
        self.assertTrue(receipt["stream"]["truncated_final_line"])
        self.assertTrue(Path(receipt["receipt_path"]).is_file())

        def broken_parser(events, spec):
            raise RuntimeError("synthetic parser failure")

        receipt = self.run_child("print('{}')", parser=broken_parser)
        self.assertNotEqual("success", receipt["status"])
        self.assertTrue(any("parser_exception" in error for error in receipt["errors"]))
        self.assertTrue(Path(receipt["receipt_path"]).is_file())

    def test_attempt_reuse_denied_without_replacing_receipt(self):
        spec = self.spec()
        receipt = self.run_child("print('{\"type\":\"result\",\"model\":\"fixture-model\"}')", spec)
        original = Path(receipt["receipt_path"]).read_bytes()
        with self.assertRaisesRegex(worker.SpecError, "already exists"):
            self.run_child("raise RuntimeError('must not run')", spec)
        self.assertEqual(original, Path(receipt["receipt_path"]).read_bytes())

    def test_spawn_failure_has_launch_intent_but_no_spawn_claim(self):
        spec = self.spec()
        receipt = worker.run_process(spec, [str(self.root / "absent executable")], parse_fixture, env={})
        self.assertEqual("spawn_failed", receipt["status"])
        self.assertIsNone(receipt["pid"])
        self.assertTrue(Path(receipt["launch_path"]).is_file())
        self.assertFalse(Path(receipt["process_path"]).exists())

    def test_timeout_kills_and_reaps_child_that_ignores_term(self):
        receipt = self.run_child(
            "import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(20)\n",
            self.spec(timeout_seconds=0.3),
        )
        self.assertEqual("timeout", receipt["status"])
        self.assertTrue(receipt["confirmed_terminated"])
        self.assertTrue(receipt["termination"]["kill_sent"])
        self.assertLess(receipt["elapsed_seconds"], 4)
        with self.assertRaises(ProcessLookupError):
            os.kill(receipt["pid"], 0)

    def test_timeout_does_not_leave_term_ignoring_grandchild_running(self):
        pid_path = self.root / "grandchild.pid"
        heartbeat = self.root / "grandchild.heartbeat"
        child_code = (
            "import os,signal,time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            f"Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
            "for count in range(1000):\n"
            f" Path({str(heartbeat)!r}).write_text(str(count))\n"
            " time.sleep(0.02)\n"
        )
        body = (
            "import subprocess,sys,time\nfrom pathlib import Path\n"
            f"subprocess.Popen([sys.executable,'-c',{child_code!r}])\n"
            f"while not Path({str(pid_path)!r}).exists(): time.sleep(0.01)\n"
            "time.sleep(20)\n"
        )
        receipt = self.run_child(body, self.spec(timeout_seconds=0.5))
        self.assertTrue(pid_path.exists(), "fixture grandchild did not start")
        pid = int(pid_path.read_text())
        try:
            self.assertTrue(heartbeat.exists())
            stopped_value = heartbeat.read_text()
            time.sleep(0.12)
            self.assertEqual(stopped_value, heartbeat.read_text(), "grandchild kept writing after termination")
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), f"grandchild still executing: pid={pid} state={state}")
            self.assertEqual("timeout", receipt["status"])
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_normal_leader_exit_cannot_hide_a_live_descendant(self):
        pid_path = self.root / "normal-exit-child.pid"
        heartbeat = self.root / "normal-exit-child.heartbeat"
        child_code = (
            "import os,signal,time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            f"Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
            "for count in range(1000):\n"
            f" Path({str(heartbeat)!r}).write_text(str(count))\n"
            " time.sleep(0.02)\n"
        )
        body = (
            "import subprocess,sys,time\nfrom pathlib import Path\n"
            f"subprocess.Popen([sys.executable,'-c',{child_code!r}])\n"
            f"while not Path({str(heartbeat)!r}).exists(): time.sleep(0.01)\n"
            "print('{\"type\":\"result\",\"model\":\"fixture-model\",\"text\":\"leader finished\"}',flush=True)\n"
        )
        receipt = self.run_child(body)
        self.assertTrue(pid_path.exists())
        pid = int(pid_path.read_text())
        try:
            self.assertEqual("unverified", receipt["status"])
            self.assertFalse(receipt["requested_model_verified"])
            self.assertTrue(receipt["confirmed_terminated"])
            stopped_value = heartbeat.read_text()
            time.sleep(0.12)
            self.assertEqual(stopped_value, heartbeat.read_text(), "normal exit left a writing descendant")
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), f"descendant still executing: {pid} {state}")
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_invalid_timeouts_paths_and_tools_fail_before_launch(self):
        for changes in ({"timeout_seconds": float("nan")}, {"timeout_seconds": True},
                        {"timeout_seconds": 0}, {"cwd": "."}, {"allowed_tools": "Read"}):
            with self.subTest(changes=changes):
                spec = self.spec(**changes)
                with self.assertRaises(worker.SpecError):
                    self.run_child("raise RuntimeError('must not run')", spec)
                self.assertFalse(Path(spec["run_dir"]).exists())


if __name__ == "__main__":
    unittest.main()
