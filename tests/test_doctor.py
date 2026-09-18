"""Injected-runner tests for the read-only prerequisite doctor.  No provider, login or network call."""

import contextlib
import io
import json
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import doctor


def ok(stdout="", stderr="", returncode=0):
    return {"returncode": returncode, "stdout": stdout, "stderr": stderr, "error": None}


# Synthetic reproductions of the 2026-09-18 host observations.  The wording is a
# fixture for this test, not a contract of the installed CLIs.
NOT_AUTH_LISTING = "You are not authenticated. Falling back to default models:\n  grok-4.6\n  grok-4.5\n"
REAL_SANDBOX_STDERR = (
    "warning: sandbox could not be applied: socket deny resolution failed: "
    "could not resolve runtime-socket deny path /var/run/docker.sock: endpoint is a symlink\n"
    "error: could not apply the 'read-only' sandbox profile; see the warning above for the cause. "
    "Refusing to start with its protections missing.\n"
)
RESPONSES = {
    ("codex", "--version"): ok("codex-cli 0.154.0\n"),
    ("claude", "--version"): ok("2.1.274 (Claude Code)\n"),
    ("grok", "--version"): ok("grok 1.0.34 (3736acbc8658)\n"),
    ("grok", "models"): ok(NOT_AUTH_LISTING),
}
BINARIES = {"codex": "/synthetic/bin/codex", "claude": "/synthetic/bin/claude", "grok": "/synthetic/bin/grok"}
ALL_COMMANDS = [["codex", "--version"], ["claude", "--version"], ["grok", "--version"], ["grok", "models"]]


class FakeRunner:
    def __init__(self, responses=None):
        self.responses = dict(RESPONSES if responses is None else responses)
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        key = (os.path.basename(argv[0]), *argv[1:])
        return self.responses.get(key, {"returncode": None, "stdout": "", "stderr": "", "error": "no fixture"})


def receipt_for(backend="grok", model="grok-4.6", **changes):
    """A consistent successful worker receipt in the worker_common shape; ``changes`` break it deliberately."""
    receipt = {
        "schema": doctor.WORKER_RECEIPT_SCHEMA, "status": "success", "exit_code": 0, "lifecycle": "exited",
        "backend": backend, "requested_model": model, "observed_models": [model],
        "requested_model_verified": True, "complete": True, "provider_is_error": False,
        "returncode": 0, "errors": [], "warnings": [],
    }
    receipt.update(changes)
    return receipt


def startup_failure_receipt(**changes):
    """The receipt shape the shared launcher wrote for the real pre-inference Grok failure."""
    failure = dict(
        status="process_failed", exit_code=1, returncode=1, observed_models=[], requested_model_verified=False, complete=False,
        errors=["process_failed: child exited with returncode 1", "incomplete: no terminal result event in the stream"],
    )
    failure.update(changes)
    return receipt_for(**failure)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pstack doctor ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.runner = FakeRunner()
        self.count = 0

    def report(self, runner=None, which=None, environ=None, **kwargs):
        # Point at an absent bundle by default so the host's real /Applications never influences a test.
        kwargs.setdefault("grok_bot_app", str(self.root / "Absent.app"))
        return doctor.build_report(runner=runner or self.runner, which=which or BINARIES.get,
                                   environ={} if environ is None else environ, home=str(self.home), **kwargs)

    def run_main(self, *args, runner=None, which=None, environ=None):
        argv = list(args)
        if "--grok-bot-app" not in argv:
            argv += ["--grok-bot-app", str(self.root / "Absent.app")]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = doctor.main(argv, runner=runner or self.runner, which=which or BINARIES.get,
                               environ={} if environ is None else environ, home=str(self.home))
        return code, json.loads(out.getvalue())

    def write_receipt(self, receipt, stderr=None):
        self.count += 1
        run_dir = self.root / f"attempt {self.count}"
        run_dir.mkdir()
        (run_dir / "receipt.json").write_text(json.dumps(receipt))
        if stderr is not None:
            (run_dir / "stderr.txt").write_text(stderr)
        return run_dir

    def write_bot_app(self, plist_bytes=None):
        contents = self.root / "Grok Bot.app" / "Contents"
        contents.mkdir(parents=True)
        if plist_bytes is None:
            with open(contents / "Info.plist", "wb") as handle:
                plistlib.dump({"CFBundleShortVersionString": "0.56.1", "CFBundleIdentifier": "com.example.synthetic-bot"}, handle)
        else:
            (contents / "Info.plist").write_bytes(plist_bytes)
        return contents.parent

    def test_not_authenticated_listing_with_exit_zero_is_needs_login(self):
        report = self.report()
        grok = report["components"]["grok_build"]
        self.assertEqual("installed", grok["installed"]["status"])
        self.assertEqual("1.0.34 (3736acbc8658)", grok["installed"]["version"])
        self.assertEqual("needs_login", grok["auth"]["status"])
        self.assertEqual("needs_login", grok["status"])
        self.assertEqual(["needs_login"], grok["blockers"])
        self.assertNotIn("grok-4.6", json.dumps(report), "fallback model names must not be presented as entitlements")
        self.assertFalse(report["policy"]["login_performed"])
        self.assertEqual(0, report["exit_code"], "an optional worker needing login does not fail the core verdict")
        self.assertEqual("installed_auth_unknown", report["core"]["status"])

    def test_listing_without_marker_stays_unknown_and_marker_variants_are_recognized(self):
        self.runner.responses[("grok", "models")] = ok("grok-4.6\ngrok-4.5\n")
        grok = self.report()["components"]["grok_build"]
        self.assertEqual("unknown", grok["auth"]["status"])
        self.assertEqual("installed_auth_unknown", grok["status"])
        self.assertIn("not proof", grok["auth"]["evidence"])
        for response in (ok("\x1b[33mYou are not authenticated\x1b[0m\ngrok-4.6\n"), ok("grok-4.6\n", stderr="Not logged in. Run `grok login`.\n"),
                         ok("Authentication required\n", returncode=1)):
            with self.subTest(response=response):
                self.runner.responses[("grok", "models")] = response
                self.assertEqual("needs_login", self.report()["components"]["grok_build"]["auth"]["status"])

    def test_sandbox_socket_symlink_receipt_is_blocked_without_weakening_or_echoing(self):
        run_dir = self.write_receipt(startup_failure_receipt(), stderr=REAL_SANDBOX_STDERR + "PRIVATE-STDERR-LINE-7f3a\n")
        for supplied in (run_dir / "receipt.json", run_dir):
            with self.subTest(supplied=supplied.name):
                report = self.report(grok_receipt=str(supplied))
                grok = report["components"]["grok_build"]
                probe = grok["sandbox_probe"]
                self.assertEqual("blocked", probe["status"])
                self.assertEqual("sandbox_socket_symlink", probe["classification"])
                self.assertIn("/var/run/docker.sock", probe["detail"])
                self.assertFalse(probe["policy_weakened"])
                self.assertTrue(any("downgraded or disabled sandbox" in step for step in probe["prerequisites"]))
                self.assertEqual("unverified", grok["inference"]["status"])
                self.assertEqual(["needs_login", "sandbox_socket_symlink"], grok["blockers"])
                self.assertEqual("needs_login", grok["status"])
                self.assertEqual(0, report["exit_code"])
                text = json.dumps(report)
                self.assertNotIn("PRIVATE-STDERR-LINE-7f3a", text)
                self.assertNotIn("Refusing to start", text)
                self.assertNotIn(str(run_dir), text)
        stderr_only_refusal = REAL_SANDBOX_STDERR.splitlines()[1]
        run_dir = self.write_receipt(startup_failure_receipt(), stderr=stderr_only_refusal)
        probe = self.report(grok_receipt=str(run_dir))["components"]["grok_build"]["sandbox_probe"]
        self.assertEqual(("blocked", "sandbox_profile_refused"), (probe["status"], probe["classification"]))

    def test_paths_named_inside_a_receipt_are_never_opened(self):
        outside = self.root / "elsewhere.txt"
        outside.write_text(REAL_SANDBOX_STDERR + "OUTSIDE-MARKER-91c2\n")
        receipt = startup_failure_receipt(stderr_path=str(outside), raw_path=str(outside), result_path=str(outside))
        plain = self.write_receipt(receipt)
        linked = self.write_receipt(receipt)
        os.symlink(outside, linked / "stderr.txt")
        for run_dir in (plain, linked):
            with self.subTest(run_dir=run_dir.name):
                report = self.report(grok_receipt=str(run_dir / "receipt.json"))
                evidence = report["evidence_supplied"]["grok_receipt"]
                self.assertTrue(evidence["stderr_source"].startswith("unavailable"))
                self.assertEqual("unclassified", evidence["sandbox_probe"]["classification"])
                self.assertEqual("failed_unclassified", evidence["sandbox_probe"]["status"])
                self.assertNotIn("OUTSIDE-MARKER-91c2", json.dumps(report))
                self.assertNotIn(str(outside), json.dumps(report))

    def test_success_receipt_requires_agreeing_fields_not_one_boolean(self):
        self.runner.responses[("grok", "models")] = ok("grok-4.6\n")
        cases = [
            ({}, "verified_by_supplied_receipt", None),
            ({"status": "model_mismatch", "observed_models": ["grok-4.5"]}, "unverified", "observed_models do not all match requested_model"),
            ({"observed_models": ["grok-4.5"]}, "unverified", "observed_models do not all match requested_model"),
            ({"observed_models": []}, "unverified", "observed_models is empty"),
            ({"complete": False}, "unverified", "complete is not true"),
            ({"requested_model_verified": False}, "unverified", "requested_model_verified is not true"),
            ({"provider_is_error": True}, "unverified", "provider_is_error is true"),
            ({"backend": "claude"}, "unverified", "backend is 'claude', expected 'grok'"),
            ({"schema": "other/1"}, "unverified", "expected 'pstack-codex/worker-receipt/1'"),
            ({"returncode": 1}, "unverified", "returncode is 1, not 0"),
            ({"lifecycle": "timeout"}, "unverified", "lifecycle is 'timeout', not 'exited'"),
            ({"errors": ["unverified: no response message carried a model attribution"]}, "unverified", "errors present (1)"),
        ]
        for changes, expected, reason in cases:
            with self.subTest(changes=changes):
                run_dir = self.write_receipt(receipt_for(**changes))
                report = self.report(grok_receipt=str(run_dir))
                grok = report["components"]["grok_build"]
                self.assertEqual(expected, grok["inference"]["status"])
                self.assertFalse(grok["inference"]["live_measurement"])
                self.assertEqual("user_supplied_receipt", grok["inference"]["evidence_kind"])
                if reason is None:
                    self.assertEqual("verified_by_supplied_receipt", grok["status"])
                    self.assertEqual("unverified", grok["sandbox_probe"]["status"])

                else:
                    self.assertTrue(any(reason in item for item in grok["inference"]["reasons"]), grok["inference"]["reasons"])
                    self.assertEqual("installed_auth_unknown", grok["status"])
        claude_dir = self.write_receipt(receipt_for(backend="claude", model="claude-fable-5-1"))
        report = self.report(claude_receipt=str(claude_dir))
        self.assertEqual("verified_by_supplied_receipt", report["components"]["claude"]["status"])
        self.assertEqual("verified_by_supplied_receipt", report["components"]["claude"]["auth"]["status"])
        self.assertEqual("verified_by_supplied_receipt", report["core"]["status"])
        self.assertEqual("unverified", self.report(grok_receipt=str(claude_dir))["components"]["grok_build"]["inference"]["status"])

    def test_successful_inference_receipt_does_not_certify_sandbox_enforcement(self):
        for argv in ([], ["grok", "--sandbox", "off"], ["grok", "--sandbox", "read-only"]):
            with self.subTest(argv=argv):
                run_dir = self.write_receipt(receipt_for(command_argv=argv))
                grok = self.report(grok_receipt=str(run_dir))["components"]["grok_build"]
                self.assertEqual("verified_by_supplied_receipt", grok["inference"]["status"])
                self.assertEqual("unverified", grok["sandbox_probe"]["status"])
                self.assertIsNone(grok["sandbox_probe"]["policy_weakened"])

    def test_malformed_receipts_and_arguments_produce_json_problems_not_tracebacks(self):
        (self.root / "bad.json").write_text("{not json")
        (self.root / "array.json").write_text("[1, 2]")
        (self.root / "empty dir").mkdir()
        for path in (self.root / "missing.json", self.root / "bad.json", self.root / "array.json", self.root / "empty dir"):
            with self.subTest(path=path.name):
                code, payload = self.run_main("--grok-receipt", str(path))
                self.assertEqual(2, code)
                self.assertEqual(doctor.REPORT_SCHEMA, payload["schema"])
                self.assertEqual(1, len(payload["problems"]))
                self.assertIn("error", payload["evidence_supplied"]["grok_receipt"])
                self.assertIn("grok_build", payload["components"], "the rest of the diagnosis is still delivered")
                self.assertNotIn(str(self.root), json.dumps(payload))
        for timeout in ("0", "-1", "1000", "nan"):
            with self.subTest(timeout=timeout):
                code, payload = self.run_main("--timeout", timeout)
                self.assertEqual((2, "error"), (code, payload["status"]))
        with patch.object(doctor, "summarize", side_effect=RuntimeError("synthetic internal failure")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = doctor.main([], runner=self.runner, which=BINARIES.get, environ={}, home=str(self.home))
        payload = json.loads(out.getvalue())
        self.assertEqual((1, "error"), (code, payload["status"]))
        self.assertIn("RuntimeError", payload["error"])
        self.assertNotIn("Traceback", out.getvalue())

    def test_command_failures_and_timeouts_stay_bounded(self):
        self.runner.responses[("grok", "models")] = {"returncode": None, "stdout": "", "stderr": "", "error": "timeout after 0.5s"}
        grok = self.report()["components"]["grok_build"]
        self.assertEqual("unknown", grok["auth"]["status"])
        self.assertIn("timeout", grok["auth"]["evidence"])
        self.runner.responses[("grok", "models")] = ok(NOT_AUTH_LISTING)
        self.runner.responses[("claude", "--version")] = {"returncode": None, "stdout": "", "stderr": "", "error": "timeout after 15s"}
        report = self.report()
        self.assertEqual("check_failed", report["components"]["claude"]["status"])
        self.assertEqual(("claude_check_failed", 1), (report["core"]["status"], report["exit_code"]))
        self.assertEqual("needs_login", report["components"]["grok_build"]["status"], "optional diagnosis continues")

        def exploding(argv, timeout):
            raise RuntimeError("synthetic runner failure")

        for runner in (exploding, lambda argv, timeout: "nonsense"):
            with self.subTest(runner=runner.__name__):
                report = self.report(runner=runner)
                self.assertEqual({"check_failed"}, {report["components"][name]["installed"]["status"] for name in ("codex", "claude", "grok_build")})
                self.assertEqual("not_checked", report["components"]["grok_build"]["auth"]["status"])

        result = doctor.default_runner([sys.executable, "-c", "import time; time.sleep(5)"], 0.2)
        self.assertTrue(result["error"].startswith("timeout"))
        self.assertIsNone(result["returncode"])
        self.assertEqual("FileNotFoundError", doctor.default_runner([str(self.root / "absent binary")], 1.0)["error"])
        result = doctor.default_runner([sys.executable, "-c", f"print('1.2.3'); print('x' * {2 * doctor.MAX_OUTPUT_BYTES})"], 10.0)
        self.assertEqual(0, result["returncode"])
        self.assertLessEqual(len(result["stdout"]), doctor.MAX_OUTPUT_BYTES)
        self.assertEqual("1.2.3", doctor.extract_version(result["stdout"]))

    def test_secret_values_identities_and_home_paths_never_reach_the_report(self):
        secret = "xai-SYNTHETIC-SECRET-VALUE-0123456789"
        environ = {"XAI_API_KEY": secret, "ANTHROPIC_BASE_URL": "https://proxy.example.invalid", "HOME": str(self.home)}
        errors = [
            f"provider rejected key {secret}", "contact ops@example.com", f"see {self.home}/private/notes.txt",
            "Authorization: Bearer abcdefghijklmnop", "api_key=plainsecretvalue", "token: sk-ant-api03-synthetic-key-material",
        ]
        run_dir = self.write_receipt(startup_failure_receipt(errors=errors), stderr=f"key {secret}\n{REAL_SANDBOX_STDERR}")
        self.runner.responses[("grok", "models")] = ok(f"using key {secret}\nYou are not authenticated\n")
        self.runner.responses[("claude", "--version")] = ok(f"2.1.274 (Claude Code) {secret}\n")
        code, payload = self.run_main("--grok-receipt", str(run_dir), environ=environ)
        text = json.dumps(payload)
        for leaked in (secret, "ops@example.com", str(self.home), str(self.root), "abcdefghijklmnop", "plainsecretvalue",
                       "sk-ant-api03", "proxy.example.invalid"):
            self.assertNotIn(leaked, text)
        self.assertEqual(0, code)
        self.assertEqual(["ANTHROPIC_BASE_URL", "XAI_API_KEY"], payload["environment"]["override_variables_present"])
        self.assertFalse(payload["environment"]["values_shown"])
        self.assertEqual(6, payload["evidence_supplied"]["grok_receipt"]["error_count"])
        self.assertIn("<email>", json.dumps(payload["evidence_supplied"]["grok_receipt"]["errors"]))
        self.assertEqual("sandbox_socket_symlink", payload["components"]["grok_build"]["sandbox_probe"]["classification"])
        self.assertEqual("2.1.274", payload["components"]["claude"]["installed"]["version"])

    def test_missing_optional_components_do_not_break_core(self):
        code, payload = self.run_main(which={"codex": BINARIES["codex"], "claude": BINARIES["claude"]}.get)
        self.assertEqual(0, code)
        grok = payload["components"]["grok_build"]
        self.assertEqual(("not_installed", "not_checked", True), (grok["status"], grok["auth"]["status"], grok["optional"]))
        self.assertEqual("not_installed", payload["components"]["grok_bot"]["status"])
        self.assertEqual("installed_auth_unknown", payload["core"]["status"])
        self.assertEqual({"grok_build": "not_installed", "grok_bot": "not_installed"}, payload["optional"]["statuses"])
        self.assertEqual(ALL_COMMANDS[:2], payload["policy"]["commands_run"])
        self.assertTrue(all("not required for core" in line for line in payload["summary"] if "(optional)" in line))
        code, payload = self.run_main(which={"claude": BINARIES["claude"]}.get)
        self.assertEqual((0, "installed_auth_unknown"), (code, payload["core"]["status"]))
        self.assertIn("codex_note", payload["core"])
        code, payload = self.run_main(which={"codex": BINARIES["codex"], "grok": BINARIES["grok"]}.get)
        self.assertEqual((1, "claude_not_installed"), (code, payload["core"]["status"]))
        self.assertEqual("needs_login", payload["components"]["grok_build"]["status"])

    def test_grok_bot_is_reported_from_bundle_metadata_only(self):
        app = self.write_bot_app()
        report = self.report(grok_bot_app=str(app))
        bot = report["components"]["grok_bot"]
        self.assertEqual(("installed", "installed"), (bot["status"], bot["installed"]["status"]))
        self.assertEqual("0.56.1", bot["installed"]["version"])
        self.assertEqual("com.example.synthetic-bot", bot["installed"]["bundle_identifier"])
        self.assertEqual({"auth": "not_checked", "runtime": "not_checked", "webhook": "not_checked"},
                         {key: bot[key]["status"] for key in ("auth", "runtime", "webhook")})
        self.assertTrue(bot["optional"])
        self.assertEqual(ALL_COMMANDS, report["policy"]["commands_run"], "nothing is executed for the app")
        self.assertNotIn("logged in", json.dumps(bot).lower())
        scrubber = doctor.Scrubber(str(self.home), {})
        (self.home / "Applications").mkdir()
        os.rename(app, self.home / "Applications" / "Grok Bot.app")
        found = doctor.discover_app_bundle(("~/Applications/Grok Bot.app",), str(self.home), scrubber)
        self.assertEqual(("installed", "~/Applications/Grok Bot.app"), (found["status"], found["path"]))
        self.assertEqual("not_found", doctor.discover_app_bundle((str(self.root / "Absent.app"),), str(self.home), scrubber)["status"])
        broken = self.write_bot_app(plist_bytes=b"not a plist")
        found = doctor.discover_app_bundle((str(broken),), str(self.home), scrubber)
        self.assertEqual(("installed", None, "app bundle directory present; Info.plist unreadable"), (found["status"], found["version"], found["evidence"]))

    def test_only_allowlisted_read_only_commands_run_under_the_timeout(self):
        report = self.report(timeout=3.5)
        self.assertEqual(ALL_COMMANDS, report["policy"]["commands_run"])
        self.assertEqual(3.5, report["policy"]["command_timeout_seconds"])
        for argv, timeout in self.runner.calls:
            self.assertEqual(3.5, timeout)
            self.assertTrue(argv[0].startswith("/synthetic/bin/"), argv)
            self.assertFalse({"login", "logout", "auth", "install", "config", "setup"} & {arg.lower() for arg in argv[1:]}, argv)
        self.runner.calls.clear()
        report = self.report(run_grok_models=False)
        self.assertEqual(ALL_COMMANDS[:3], report["policy"]["commands_run"])
        self.assertEqual("not_checked", report["components"]["grok_build"]["auth"]["status"])
        for flag in ("login_performed", "inference_performed", "settings_modified", "credential_files_read", "doctor_network_calls"):
            self.assertFalse(report["policy"][flag])


if __name__ == "__main__":
    unittest.main()
