import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from grok_worker import UnsupportedProfile, build_command, build_env, main, parse_events
import worker_common


FIXTURES = Path(__file__).parent / "fixtures" / "grok"


def fixture(name):
    return [json.loads(line) for line in (FIXTURES / f"{name}.jsonl").read_text().splitlines()]


def fixture_spec(name):
    return {"model": "grok-4.6", "profile": "writer" if name.startswith("writer") else name,
            "cwd": fixture(name)[0]["cwd"]}


class GrokParserTests(unittest.TestCase):
    def parse(self, events, profile="analysis", cwd="/fixture/project"):
        return parse_events(events, {"model": "grok-4.6", "profile": profile, "cwd": cwd})

    def test_actual_analysis_requires_exact_identity_and_empty_inventory(self):
        result = self.parse(fixture("analysis"))
        self.assertEqual(result["result_text"], "PSTACK_GROK_PROBE_OK")
        self.assertEqual(result["observed_models"], ["grok-4.6"])
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["complete"])
        self.assertFalse(result["is_error"])
        self.assertTrue(result["evidence"]["tool_inventory_verified_empty"])
        self.assertEqual(result["evidence"]["usage_models"], ["grok-4.6-build"])

    def test_actual_reader_multiturn_keeps_all_attempts_and_final_text(self):
        result = parse_events(fixture("reader"), fixture_spec("reader"))
        self.assertEqual([call["name"] for call in result["tool_calls"]], ["read_file", "list_dir", "grep"])
        self.assertEqual(result["result_text"], "**Nonce:** `FIXTURE_NONCE`\n\n**Directory listing** (this package):\n- `inside.txt`\n- `linked.txt`\n- `readme.txt`\n\n**Write/edit:** unavailable. There is no edit or write tool in this session, so `inside.txt` was not changed.")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["evidence"]["permission_denial_count"], 0)
        self.assertEqual([turn["stop_reason"] for turn in result["evidence"]["turns"]], ["tool_use", "end_turn"])

    def test_actual_writer_stream_can_be_inspected_without_enabling_dispatch(self):
        result = parse_events(fixture("writer-positive"), fixture_spec("writer-positive"))
        self.assertEqual([call["name"] for call in result["tool_calls"]], ["read_file", "read_file", "search_replace"])
        self.assertEqual(result["result_text"], "Nonce from `readme.txt`: `FIXTURE_NONCE`\n\n`inside.txt` is now exactly `INSIDE_ALLOWED` plus a newline.\n\nWRITER_POSITIVE_COMPLETE")
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["complete"])

    def test_actual_cancellation_is_a_terminal_provider_error_with_attempt_evidence(self):
        result = parse_events(fixture("writer-sibling"), fixture_spec("writer-sibling"))
        self.assertTrue(result["complete"])
        self.assertTrue(result["is_error"])
        self.assertEqual(result["tool_calls"][0]["input"]["content"], "OUTSIDE_ATTEMPTED\n")
        self.assertEqual(result["evidence"]["terminal_subtype"], "error_during_execution")
        self.assertEqual(result["evidence"]["terminal_errors"], ["cancelled"])
        self.assertEqual(result["evidence"]["tool_cancellation_count"], 1)
        self.assertEqual(result["evidence"]["permission_denial_count"], 0)
        self.assertEqual(result["evidence"]["tool_errors"][0]["content_sha256"],
                         "f1697f82d715d0fe6007db8b8e389b76f3a5c7a81e1b7bc0ed9b0a9825d2e428")
        self.assertNotIn("content", result["evidence"]["tool_errors"][0])

    def test_actual_os_denial_does_not_relabel_successful_delivery(self):
        result = parse_events(fixture("writer-symlink"), fixture_spec("writer-symlink"))
        self.assertEqual(result["result_text"], "The write to `linked.txt` was denied (`Permission denied`). Stopped after that single attempt.")
        self.assertEqual(result["errors"], [])
        self.assertFalse(result["is_error"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["evidence"]["permission_denial_count"], 1)
        self.assertEqual(result["evidence"]["permission_denied_tools"], ["write"])
        self.assertTrue(result["warnings"])

    def test_actual_default_inventory_and_removed_metadata_cannot_pass(self):
        exposed = self.parse(fixture("analysis-default-tools"))
        self.assertIn("effective tool inventory does not match the profile", exposed["errors"])
        self.assertIn("tool-free capability is unverified", exposed["errors"])
        missing = self.parse(fixture("analysis-success"))
        self.assertIn("effective cwd missing or does not match requested cwd", missing["errors"])

    def test_synthetic_init_mutations_fail_closed(self):
        cases = {"tools": ["read_file"], "permissionMode": "bypassPermissions", "cwd": "/fixture/elsewhere",
                 "mcp_servers": [{"name": "unexpected"}], "model": "grok-4.5"}
        for key, value in cases.items():
            for remove in (False, True):
                with self.subTest(key=key, missing=remove):
                    events = fixture("analysis")
                    if remove:
                        del events[0][key]
                    else:
                        events[0][key] = value
                    result = self.parse(events)
                    self.assertTrue(result["errors"], key)
                    self.assertEqual(result["result_text"], "PSTACK_GROK_PROBE_OK")

    def test_synthetic_unexpected_call_fails_even_with_empty_inventory(self):
        events = fixture("analysis")
        events[1]["message"]["content"].append({"type": "tool_use", "id": "synthetic-call", "name": "read_file", "input": {}})
        result = self.parse(events)
        self.assertIn("analysis attempted tool use", result["errors"])
        self.assertEqual(result["tool_calls"][0]["name"], "read_file")

    def test_synthetic_missing_and_mismatched_substantive_models_fail(self):
        for model in (None, "grok-4.5"):
            with self.subTest(model=model):
                events = fixture("reader")
                events[-2]["message"]["model"] = model
                result = parse_events(events, fixture_spec("reader"))
                expected = "response message lacks model attribution" if model is None else "observed model does not match requested model"
                self.assertIn(expected, result["errors"])

    def test_synthetic_final_truncation_is_not_hidden_by_success_result(self):
        for target in (-1, -2):
            events = fixture("reader")
            message = events[target]["message"] if target == -2 else events[target]
            message["stop_reason"] = "max_tokens"
            self.assertIn("incomplete stop reason: max_tokens", parse_events(events, fixture_spec("reader"))["errors"])

    def test_missing_terminal_retains_partial_text_without_completeness(self):
        result = self.parse(fixture("analysis")[:-1])
        self.assertFalse(result["complete"])
        self.assertEqual(result["result_text"], "PSTACK_GROK_PROBE_OK")
        self.assertTrue(result["evidence"]["partial_unterminated"])
        self.assertIn("missing terminal event", result["errors"])

    def test_empty_sandbox_startup_failure_has_no_inference_evidence(self):
        result = self.parse([])
        self.assertFalse(result["complete"])
        self.assertFalse(result["is_error"])
        self.assertEqual(result["observed_models"], [])
        self.assertIn("tool-free capability is unverified", result["errors"])

    def test_synthetic_error_after_terminal_is_preserved(self):
        result = self.parse(fixture("analysis") + [{"type": "error", "error": {"message": "synthetic provider failure"}}])
        self.assertTrue(result["is_error"])
        self.assertTrue(result["complete"])
        self.assertIn("provider reported an error", result["errors"])

    def test_synthetic_unfinished_tool_cannot_become_success(self):
        events = fixture("reader")
        del events[2]
        result = parse_events(events, fixture_spec("reader"))
        self.assertIn("attempted tools lack terminal tool results", result["errors"])
        self.assertEqual(len(result["tool_calls"]), 3)

    def test_synthetic_streamed_turns_reset_indices_and_deduplicate_calls(self):
        events = fixture("reader")
        call = events[1]["message"]["content"][-3]
        synthetic = [events[0],
            {"type": "message_start", "message": {"model": "grok-4.6", "content": []}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "First turn"}},
            {"type": "content_block_start", "index": 1, "content_block": dict(call, input={})},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"target_file":"readme.txt"}'}},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
            {"type": "message_stop"}, events[1], events[2],
            {"type": "message_start", "message": {"model": "grok-4.6", "content": []}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Final answer"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
            {"type": "message_stop"},
            {"type": "result", "subtype": "success", "is_error": False}]
        result = parse_events([item if item["type"] == "system" else {"type": "stream_event", "event": item} for item in synthetic], fixture_spec("reader"))
        self.assertEqual(result["result_text"], "Final answer")
        self.assertEqual([call["name"] for call in result["tool_calls"]], ["read_file", "list_dir", "grep"])
        self.assertEqual(result["tool_calls"][0]["input"], {"target_file": "readme.txt"})
        self.assertEqual(result["errors"], [])

    def test_synthetic_message_stop_without_result_is_incomplete(self):
        events = [fixture("analysis")[0],
                  {"type": "message_start", "message": {"model": "grok-4.6", "content": []}},
                  {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "Partial"}},
                  {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
                  {"type": "message_stop"}]
        result = self.parse(events)
        self.assertFalse(result["complete"])
        self.assertEqual(result["result_text"], "Partial")
        self.assertIn("missing terminal event", result["errors"])

    def test_synthetic_unclosed_stream_cannot_be_hidden_by_a_later_complete_turn(self):
        events = fixture("analysis")
        events[1:1] = [
            {"type": "message_start", "message": {"model": "grok-4.5", "content": []}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "Discarded"}},
            {"type": "message_start", "message": {"model": "grok-4.6", "content": []}},
        ]
        result = self.parse(events)
        self.assertEqual(result["result_text"], "PSTACK_GROK_PROBE_OK")
        self.assertIn("message started before the previous streamed message stopped", result["errors"])
        self.assertIn("unterminated streamed message", result["errors"])


class GrokExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        project = self.root / "project with spaces"
        project.mkdir()
        prompt = self.root / "prompt.txt"
        prompt.write_bytes(b"Synthetic prompt\r\nwith exact bytes\n")
        self.spec = {"backend": "grok", "model": "grok-4.6", "effort": "low", "profile": "analysis",
                     "cwd": str(project), "prompt_file": str(prompt), "run_dir": str(self.root / "run"),
                     "timeout_seconds": 5, "term_grace_seconds": 0.2}

    def fake_cli(self, version_code=None, events=None, task_code=None):
        binary = self.root / "fake-grok"
        if version_code is None:
            version_code = "print('grok 1.0.34 (3736acbc8658) [alpha]')"
        if events is None:
            events = fixture("analysis")
        events = copy.deepcopy(events)
        events[0]["cwd"] = self.spec["cwd"]
        (self.root / "events.json").write_text(json.dumps(events))
        if task_code is None:
            task_code = "(root / 'received-prompt').write_bytes(sys.stdin.buffer.read())\nfor event in json.loads((root / 'events.json').read_text()):\n    print(json.dumps(event))"
        binary.write_text(f"#!{sys.executable}\nimport json, os, sys, time\nfrom pathlib import Path\nroot=Path({str(self.root)!r})\nwith (root/'invocations').open('a') as log:\n    log.write(json.dumps(sys.argv[1:])+'\\n')\nif sys.argv[1:] == ['--version']:\n" + "\n".join("    " + line for line in version_code.splitlines()) + "\n    sys.exit(0)\n" + task_code + "\n")
        binary.chmod(0o700)
        return str(binary)

    def invoke(self, spec=None, binary=None):
        spec = self.spec if spec is None else spec
        path = self.root / "spec.json"
        path.write_text(json.dumps(spec))
        output, errors = io.StringIO(), io.StringIO()
        with patch("grok_worker.shutil.which", return_value=binary or str(self.root / "fake-grok")), \
                patch("grok_worker.sys.platform", "linux"), \
                patch("grok_worker.build_env", return_value=({"PATH": "/usr/bin:/bin"}, {"auth_route": "installed-cli-auth"})), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(["--spec", str(path)])
        return code, json.loads(output.getvalue()), errors.getvalue()

    def test_exact_build_runs_through_common_supervisor_with_stdin(self):
        binary = self.fake_cli()
        before = copy.deepcopy(self.spec)
        code, receipt, _ = self.invoke(binary=binary)
        self.assertEqual(code, 0, receipt["errors"])
        self.assertEqual(receipt["status"], "success")
        self.assertTrue(receipt["confirmed_terminated"])
        self.assertTrue(receipt["requested_model_verified"])
        self.assertEqual(receipt["adapter"]["compatibility"]["observed_version"], "grok 1.0.34 (3736acbc8658) [alpha]")
        self.assertEqual(receipt["prompt_sha256"], receipt["stdin_sha256"])
        self.assertEqual((self.root / "received-prompt").read_bytes(), b"Synthetic prompt\r\nwith exact bytes\n")
        self.assertEqual(self.spec, before)
        self.assertEqual(Path(receipt["result_path"]).read_text(), "PSTACK_GROK_PROBE_OK")
        argv = receipt["command_argv"]
        self.assertEqual(argv[argv.index("--prompt-file") + 1], "/dev/stdin")
        self.assertEqual(argv[argv.index("--tools") + 1], "read_file")
        self.assertEqual(argv[argv.index("--disallowed-tools") + 1], "read_file,search_tool,use_tool")
        self.assertEqual([argv[i + 1] for i, arg in enumerate(argv) if arg == "--deny"],
                         ["Bash", "Edit", "Read", "Grep", "MCPTool", "WebFetch", "WebSearch"])
        self.assertNotIn("Synthetic prompt", " ".join(argv))
        self.assertEqual(list(Path(self.spec["cwd"]).iterdir()), [])

    def test_reader_delivers_actual_multiturn_shape_with_scoped_file_rules(self):
        spec = dict(self.spec, profile="reader")
        code, receipt, _ = self.invoke(spec, self.fake_cli(events=fixture("reader")))
        self.assertEqual(code, 0, receipt["errors"])
        self.assertEqual(receipt["tool_calls_by_name"], {"read_file": 1, "list_dir": 1, "grep": 1})
        argv = receipt["command_argv"]
        cwd = str(Path(spec["cwd"]).resolve())
        self.assertEqual([argv[i + 1] for i, arg in enumerate(argv) if arg == "--allow"],
                         [f"Read({cwd})", f"Read({cwd}/**)", f"Grep({cwd})", f"Grep({cwd}/**)"])
        self.assertEqual(argv[argv.index("--tools") + 1], "read_file,list_dir,grep")
        self.assertGreater(int(argv[argv.index("--max-turns") + 1]), 3)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")

    def test_unknown_build_stops_before_prompt_dispatch_and_attempt_claim(self):
        binary = self.fake_cli("print('grok 1.0.35 (3736acbc8658) [alpha]')")
        code, receipt, _ = self.invoke(binary=binary)
        self.assertEqual(code, 2)
        self.assertEqual(receipt["status"], "unsupported_profile")
        self.assertEqual(receipt["adapter"]["compatibility"]["observed_version"], "grok 1.0.35 (3736acbc8658) [alpha]")
        self.assertEqual((self.root / "invocations").read_text(), '["--version"]\n')
        self.assertFalse((self.root / "received-prompt").exists())
        self.assertFalse(Path(self.spec["run_dir"]).exists())

    def test_version_preflight_bounds_time_and_output_and_reaps_process(self):
        for version_code in ("print('x' * 5000, flush=True)\ntime.sleep(30)", "time.sleep(30)"):
            with self.subTest(version_code=version_code), patch("grok_worker.VERSION_TIMEOUT_SECONDS", 0.15):
                code, receipt, _ = self.invoke(binary=self.fake_cli(version_code))
                self.assertEqual(code, 2)
                self.assertEqual(receipt["status"], "unsupported_profile")
                self.assertTrue(receipt["confirmed_terminated"])
                pgid = receipt["adapter"]["compatibility"]["pgid"]
                with self.assertRaises(ProcessLookupError):
                    os.killpg(pgid, 0)
                self.assertFalse((self.root / "received-prompt").exists())

    def test_writer_and_bash_are_rejected_without_any_binary_invocation(self):
        for updates in ({"profile": "writer"}, {"profile": "reader", "allowed_tools": ["Bash(git log:*)"]},
                        {"allowed_tools": ["Read"]}, {"resume": "previous-session"}):
            with self.subTest(updates=updates):
                code, receipt, _ = self.invoke(dict(self.spec, **updates))
                self.assertEqual(code, 2)
                self.assertEqual(receipt["status"], "unsupported_profile")
                self.assertTrue(receipt["errors"])
                self.assertFalse((self.root / "invocations").exists())

    def test_nonlinux_fails_before_binary_execution(self):
        from grok_worker import check_compatibility
        with patch("grok_worker.sys.platform", "darwin"), self.assertRaisesRegex(UnsupportedProfile, "only on Linux"):
            check_compatibility(self.fake_cli(), {}, self.spec["cwd"])
        self.assertFalse((self.root / "invocations").exists())

    def test_invalid_spec_and_unsafe_scope_rejected_before_preflight(self):
        for updates in ({"timeout_seconds": 0}, {"timeout_seconds": float("nan")}, {"cwd": "."},
                        {"cwd": "/"}, {"model": "--model"}, {"profile": "unknown"},
                        {"run_dir": str(Path(self.spec["cwd"]) / "attempt")}):
            with self.subTest(updates=updates):
                code, receipt, _ = self.invoke(dict(self.spec, **updates))
                self.assertEqual(code, 2)
                self.assertEqual(receipt["status"], "invalid_spec")
                self.assertFalse((self.root / "invocations").exists())
        for char in "*?[](){},\\\x7f":
            directory = self.root / ("unsafe" + char)
            directory.mkdir()
            with self.subTest(char=char), self.assertRaisesRegex(ValueError, "permission path"):
                build_command(dict(self.spec, profile="reader", cwd=str(directory)), "/fake/grok")
        alias = self.root / "safe-alias"
        alias.symlink_to(self.root / "unsafe*", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "permission path"):
            build_command(dict(self.spec, profile="reader", cwd=str(alias)), "/fake/grok")

    def test_shared_receipt_preserves_error_and_denial_outcomes(self):
        for name, expected in (("writer-sibling", "provider_error"), ("writer-symlink", "success")):
            events = fixture(name)
            events[0]["cwd"] = self.spec["cwd"]
            spec = dict(self.spec, profile="writer", run_dir=str(self.root / name))
            payload = "".join(json.dumps(event) + "\n" for event in events)
            receipt = worker_common.run_process(spec, [sys.executable, "-c", "import sys;sys.stdout.write(sys.argv[1])", payload], parse_events)
            self.assertEqual(receipt["status"], expected, receipt["errors"])
            self.assertTrue(receipt["complete"])
            self.assertTrue(receipt["confirmed_terminated"])
            self.assertEqual(receipt["tool_calls_by_name"], {"write": 1})
            if name == "writer-symlink":
                self.assertEqual(receipt["permission_denial_count"], 1)
                self.assertTrue(any("permission_denials: 1" in warning for warning in receipt["warnings"]))

    def test_shared_runner_preserves_startup_failure_and_model_mismatch(self):
        cases = ((None, "process_failed"), ("grok-4.5", "model_mismatch"))
        for model, expected in cases:
            with self.subTest(model=model):
                events = fixture("analysis")
                events[1]["message"]["model"] = model
                binary = self.fake_cli(events=events, task_code="sys.stderr.write('synthetic sandbox startup failure\\n');sys.exit(1)" if model is None else None)
                spec = dict(self.spec, run_dir=str(self.root / expected))
                code, receipt, _ = self.invoke(spec, binary)
                self.assertEqual(code, 1)
                self.assertEqual(receipt["status"], expected)
                self.assertFalse(receipt["requested_model_verified"])
                self.assertTrue(Path(receipt["receipt_path"]).is_file())

    def test_unexpected_runtime_failure_keeps_common_error_contract(self):
        with patch("grok_worker.run", side_effect=RuntimeError("synthetic adapter failure")):
            code, receipt, stderr = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(receipt["schema"], "pstack-codex/worker-receipt/1")
        self.assertEqual(receipt["status"], "internal_error")
        self.assertEqual(receipt["errors"], ["RuntimeError: synthetic adapter failure"])
        self.assertIn("synthetic adapter failure", stderr)

    def test_auth_override_values_are_not_exposed_or_removed_silently(self):
        for name in ("XAI_API_KEY", "GROK_CLI_CHAT_PROXY_BASE_URL"):
            with self.assertRaises(ValueError) as error:
                build_env({name: "synthetic-secret-never-print"})
            self.assertIn(name, str(error.exception))
            self.assertNotIn("synthetic-secret-never-print", str(error.exception))
        env, policy = build_env({"HOME": "/fixture-home", "GROK_HOME": "/fixture-home/.grok"})
        self.assertEqual(env, {"HOME": "/fixture-home", "GROK_HOME": "/fixture-home/.grok"})
        self.assertFalse(policy["adapter_configures_credentials"])


if __name__ == "__main__":
    unittest.main()
