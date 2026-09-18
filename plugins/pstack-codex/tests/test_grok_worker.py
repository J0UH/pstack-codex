"""Synthetic wire contract tests plus a sanitized real pre-inference failure."""

import copy
import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from grok_worker import UnsupportedProfile, build_command, build_env, main, parse_events, run


# Real 1.0.34 probe emitted zero JSON events and refused sandbox startup.
REAL_SANDBOX_FAILURE = {
    "events": [],
    "exit_code": 1,
    "stderr": "error: could not apply the 'read-only' sandbox profile; see the warning above for the cause. Refusing to start with its protections missing.",
}

# Synthetic shapes based on the selected Messages format. No live success claim.
SYNTHETIC_MESSAGES = [
    {"type": "system", "subtype": "init", "model": "grok-4.6", "tools": []},
    {"type": "message_start", "message": {"model": "grok-4.6", "content": []}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "PSTACK_"}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "GROK_OK"}},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
    {"type": "message_stop"},
]


class GrokParserTests(unittest.TestCase):
    def parse(self, events):
        return parse_events(events, {"model": "grok-4.6", "profile": "analysis"})

    def test_synthetic_complete_messages_preserve_text_and_identity(self):
        result = self.parse(SYNTHETIC_MESSAGES)
        self.assertEqual(result["result_text"], "PSTACK_GROK_OK")
        self.assertEqual(result["observed_models"], ["grok-4.6"])
        self.assertTrue(result["complete"])
        self.assertFalse(result["is_error"])

    def test_real_empty_sandbox_failure_is_not_a_success(self):
        result = self.parse(REAL_SANDBOX_FAILURE["events"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["is_error"])
        self.assertTrue(result["errors"])
        self.assertEqual(result["observed_models"], [])

    def test_missing_terminal_is_incomplete_even_with_answer(self):
        result = self.parse(SYNTHETIC_MESSAGES[:-1])
        self.assertFalse(result["complete"])
        self.assertIn("missing terminal event", result["errors"])

    def test_configuration_model_is_not_inference_evidence(self):
        events = copy.deepcopy(SYNTHETIC_MESSAGES)
        del events[1]["message"]["model"]
        self.assertIn("missing observed inference model", self.parse(events)["errors"])

    def test_mismatch_is_failure_without_alias_fallback(self):
        events = copy.deepcopy(SYNTHETIC_MESSAGES)
        events[1]["message"]["model"] = "grok-4.5"
        result = self.parse(events)
        self.assertFalse(result["is_error"])
        self.assertIn("observed model does not match requested model", result["errors"])
        self.assertEqual(result["observed_models"], ["grok-4.5"])

    def test_error_after_text_and_terminal_overrides_success(self):
        result = self.parse(SYNTHETIC_MESSAGES + [{"type": "error", "error": {"message": "synthetic auth failure"}}])
        self.assertTrue(result["complete"])
        self.assertTrue(result["is_error"])

    def test_unknown_tool_inventory_fails_closed(self):
        self.assertIn("tool-free capability is unverified", self.parse(SYNTHETIC_MESSAGES[1:])["errors"])
        events = copy.deepcopy(SYNTHETIC_MESSAGES)
        events[0]["tools"] = ["Read"]
        self.assertIn("analysis exposed a nonempty tool inventory", self.parse(events)["errors"])

    def test_tool_use_cannot_pass_with_empty_declared_inventory(self):
        tool = {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "name": "read", "id": "synthetic"}}
        result = self.parse(SYNTHETIC_MESSAGES + [tool])
        self.assertIn("analysis attempted tool use", result["errors"])
        self.assertEqual(result["tool_calls"][0]["name"], "read")

    def test_truncation_stop_is_not_completion(self):
        events = copy.deepcopy(SYNTHETIC_MESSAGES)
        events[-2]["delta"]["stop_reason"] = "max_tokens"
        self.assertIn("incomplete stop reason: max_tokens", self.parse(events)["errors"])

    def test_enveloped_events_do_not_double_count_final_answer(self):
        events = [SYNTHETIC_MESSAGES[0]] + [{"type": "stream_event", "event": item} for item in SYNTHETIC_MESSAGES[1:]]
        events += [{"type": "assistant", "message": {"model": "grok-4.6", "content": [{"type": "text", "text": "PSTACK_GROK_OK"}], "stop_reason": "end_turn"}}]
        events += [{"type": "result", "subtype": "success", "result": "PSTACK_GROK_OK", "is_error": False}]
        self.assertEqual(self.parse(events)["result_text"], "PSTACK_GROK_OK")


class GrokCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        prompt = root / "prompt with spaces.txt"
        prompt.write_text("Synthetic task")
        # The worker's cwd and its attempt evidence are siblings, never nested.
        project = root / "project"
        project.mkdir()
        self.spec = {"backend": "grok", "model": "grok-4.6", "effort": "low", "profile": "analysis", "cwd": str(project), "prompt_file": str(prompt), "run_dir": str(root / "run"), "timeout_seconds": 30}

    def run_main(self, spec):
        path = Path(spec["cwd"]) / "spec.json"
        path.write_text(json.dumps(spec))
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(["--spec", str(path)])
        return code, json.loads(output.getvalue()), errors.getvalue()

    def test_command_pins_controls_and_preserves_argv_paths(self):
        command = build_command(self.spec, "/synthetic/grok")
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertEqual(command[command.index("--prompt-file") + 1], self.spec["prompt_file"])
        self.assertIn("--no-subagents", command)
        self.assertIn("--disable-web-search", command)
        self.assertEqual([command[i + 1] for i, value in enumerate(command) if value == "--deny"],
                         ["Bash", "Edit", "Read", "Grep", "MCPTool", "WebFetch", "WebSearch"])
        self.assertNotIn("--always-approve", command)
        self.assertNotIn("bypassPermissions", command)

    def test_unverified_profiles_do_not_launch(self):
        for profile in ("reader", "writer", "unknown"):
            with self.subTest(profile=profile), self.assertRaises(UnsupportedProfile):
                build_command(dict(self.spec, profile=profile), "/synthetic/grok")

    def test_nonempty_tool_allowlist_does_not_broaden_analysis(self):
        with self.assertRaises(UnsupportedProfile):
            build_command(dict(self.spec, allowed_tools=["Read"]), "/synthetic/grok")

    def test_invalid_timeout_and_relative_paths_rejected(self):
        for timeout in (0, -1, True, "30", float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                build_command(dict(self.spec, timeout_seconds=timeout), "/synthetic/grok")
        with self.assertRaises(ValueError):
            build_command(dict(self.spec, cwd="."), "/synthetic/grok")

    def test_runner_uses_shared_launcher_without_changing_the_spec(self):
        calls = []
        def shared_runner(spec, command, parse, **kwargs):
            calls.append((spec, command, parse, kwargs))
            return {"status": "error", **parse(REAL_SANDBOX_FAILURE["events"], spec)}
        common = types.ModuleType("worker_common")
        common.run_process = shared_runner
        before = copy.deepcopy(self.spec)
        with patch.dict(sys.modules, {"worker_common": common}), patch("grok_worker.shutil.which", return_value="/synthetic/grok"):
            receipt = run(self.spec)
        self.assertEqual(self.spec, before)
        self.assertIs(calls[0][0], self.spec)
        self.assertIs(calls[0][2], parse_events)
        self.assertIsInstance(calls[0][3]["env"], dict)
        self.assertEqual(calls[0][3]["adapter_evidence"]["auth_policy"]["auth_route"], "installed-cli-auth")
        self.assertFalse(receipt["complete"])
        self.assertTrue(receipt["errors"])

    def test_unsupported_profile_uses_the_common_error_receipt(self):
        path = Path(self.spec["cwd"]) / "spec.json"
        path.write_text(json.dumps(dict(self.spec, profile="writer")))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--spec", str(path)])
        receipt = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(receipt["schema"], "pstack-codex/worker-receipt/1")
        self.assertEqual(receipt["status"], "unsupported_profile")
        self.assertTrue(receipt["errors"])
        self.assertFalse(Path(self.spec["run_dir"]).exists())

    def test_unexpected_runtime_failure_emits_the_common_internal_error_receipt(self):
        with patch("grok_worker.run", side_effect=RuntimeError("synthetic adapter failure")):
            code, receipt, stderr = self.run_main(self.spec)
        self.assertEqual(code, 1)
        self.assertEqual(receipt["schema"], "pstack-codex/worker-receipt/1")
        self.assertEqual(receipt["status"], "internal_error")
        self.assertEqual(receipt["exit_code"], 1)
        self.assertEqual(receipt["backend"], "grok")
        self.assertEqual(receipt["requested_model"], "grok-4.6")
        self.assertEqual(receipt["errors"], ["RuntimeError: synthetic adapter failure"])
        self.assertFalse(receipt["requested_model_verified"])
        self.assertIn("RuntimeError: synthetic adapter failure", stderr)
        self.assertFalse(Path(self.spec["run_dir"]).exists())

    def test_attempt_directory_inside_cwd_is_rejected_by_the_shared_launcher(self):
        spec = dict(self.spec, run_dir=str(Path(self.spec["cwd"]) / "run"))
        with patch("grok_worker.shutil.which", return_value="/synthetic/grok"), \
                patch("grok_worker.build_env", return_value=({"PATH": "/usr/bin"}, {"auth_route": "installed-cli-auth"})):
            code, receipt, _ = self.run_main(spec)
        self.assertEqual(code, 2)
        self.assertEqual(receipt["status"], "invalid_spec")
        self.assertTrue(any("inside cwd" in error for error in receipt["errors"]), receipt["errors"])
        self.assertFalse(Path(spec["run_dir"]).exists())

    def test_auth_override_values_are_not_exposed(self):
        for name in ["XAI_API_KEY", "GROK_CLI_CHAT_PROXY_BASE_URL"]:
            with self.assertRaises(ValueError) as error:
                build_env({name: "synthetic-secret-never-print"})
            self.assertIn(name, str(error.exception))
            self.assertNotIn("synthetic-secret-never-print", str(error.exception))

    def test_shared_runner_persists_actual_startup_failure_shape(self):
        import worker_common
        command = [sys.executable, "-c", "import sys;sys.stderr.write('synthetic sandbox startup failure\\n');sys.exit(1)"]
        receipt = worker_common.run_process(self.spec, command, parse_events)
        self.assertEqual(receipt["status"], "process_failed")
        self.assertEqual(receipt["returncode"], 1)
        self.assertFalse(receipt["provider_is_error"])
        self.assertFalse(receipt["requested_model_verified"])
        self.assertFalse(receipt["complete"])
        self.assertEqual(receipt["observed_models"], [])
        self.assertTrue(Path(receipt["receipt_path"]).is_file())
        self.assertTrue(any("tool-free capability is unverified" in error for error in receipt["errors"]))

    def test_shared_runner_rejects_complete_but_mismatched_response(self):
        import json
        import worker_common
        events = copy.deepcopy(SYNTHETIC_MESSAGES)
        events[1]["message"]["model"] = "grok-4.5"
        payload = "".join(json.dumps(event) + "\n" for event in events)
        command = [sys.executable, "-c", "import sys;sys.stdout.write(sys.argv[1])", payload]
        receipt = worker_common.run_process(self.spec, command, parse_events)
        self.assertEqual(receipt["status"], "model_mismatch")
        self.assertTrue(receipt["complete"])
        self.assertFalse(receipt["provider_is_error"])
        self.assertFalse(receipt["requested_model_verified"])


if __name__ == "__main__":
    unittest.main()
