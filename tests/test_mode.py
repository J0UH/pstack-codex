import importlib.util
import json
import os
import shlex
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pstack
module_spec = importlib.util.spec_from_file_location("mode_hook", ROOT / "hooks/mode.py")
hook = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(hook)


class ModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / "project one"
        self.project.mkdir()
        self.other = self.base / "project two"
        self.other.mkdir()
        self.env = patch.dict(os.environ, {"PSTACK_STATE_DIR": str(self.base / "state")})
        self.env.start()
        self.source = self.base / "package"
        (self.source / "skills/poteto-mode/playbooks").mkdir(parents=True)
        (self.source / "skills/poteto-mode/playbooks/investigation.md").write_text("Read-only investigation")
        (self.source / "skills/poteto-mode/SKILL.md").write_text("ROUTER_SOURCE_MARKER")
        (self.source / "adapters").mkdir()
        (self.source / "adapters/host.md").write_text("HOST_SOURCE_MARKER")
        self.root_patch = patch.object(pstack, "ROOT", self.source)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.env.stop()
        self.temp.cleanup()

    def event(self, prompt="", name="UserPromptSubmit", project=None, session="one", **extra):
        return {"hook_event_name": name, "session_id": session, "cwd": str(project or self.project), "prompt": prompt, **extra}

    def test_opt_in_persists_and_exit_stops_injection(self):
        self.assertEqual(hook.handle(self.event("Explain this function")), {})
        response = hook.handle(self.event("$poteto-mode explain this function"))
        context = response["hookSpecificOutput"]["additionalContext"]
        self.assertIn("ROUTER_SOURCE_MARKER", context)
        self.assertIn("HOST_SOURCE_MARKER", context)
        pstack.change_state("select", "one", str(self.project), "investigation")
        continued = hook.handle(self.event("continue"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("investigation", continued)
        hook.handle(self.event("exit poteto-mode"))
        self.assertEqual(hook.handle(self.event("continue")), {})

    def test_conversation_and_project_separation(self):
        hook.handle(self.event("/poteto-mode investigate"))
        self.assertEqual(hook.handle(self.event("continue", session="two")), {})
        self.assertEqual(hook.handle(self.event("continue", project=self.other)), {})

    def test_resume_compact_and_new_task(self):
        hook.handle(self.event("/poteto-mode investigate"))
        pstack.change_state("select", "one", str(self.project), "investigation")
        for source in ["resume", "compact"]:
            reply = hook.handle(self.event(name="SessionStart", source=source))
            self.assertIn("ROUTER_SOURCE_MARKER", reply["hookSpecificOutput"]["additionalContext"])
        hook.handle(self.event("new task. Find the cause"))
        self.assertIsNone(pstack.read_state("one", str(self.project))["playbook"])

    def test_mention_is_not_activation_and_stop_is_not_style_exit(self):
        self.assertEqual(hook.handle(self.event("Explain how poteto-mode works")), {})
        hook.handle(self.event("$poteto-mode explain"))
        hook.handle(self.event("stop"))
        self.assertTrue(pstack.read_state("one", str(self.project))["active"])

    def test_corruption_fails_and_identity_cannot_escape_directory(self):
        path = pstack.state_path("../../outside", str(self.project))
        self.assertEqual(path.parent, self.base / "state")
        hook.handle(self.event("/poteto-mode inspect"))
        pstack.state_path("one", str(self.project)).write_text('{"schema_version":1,"session":"wrong"}')
        with self.assertRaises(ValueError):
            hook.handle(self.event("continue"))

    def test_invalid_select_does_not_change_state(self):
        before = pstack.read_state("one", str(self.project))
        with self.assertRaises(ValueError):
            pstack.change_state("select", "one", str(self.project), "investigation")
        self.assertEqual(pstack.read_state("one", str(self.project)), before)

    def test_hook_and_shell_share_state_without_override(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, "home", return_value=self.base):
            with patch.dict(os.environ, {"PLUGIN_DATA": str(self.base / "plugin-cache-data")}):
                hook.handle(self.event("/poteto-mode inspect"))
            self.assertTrue(pstack.read_state("one", str(self.project))["active"])
            pstack.change_state("deactivate", "one", str(self.project))
            with patch.dict(os.environ, {"PLUGIN_DATA": str(self.base / "plugin-cache-data")}):
                self.assertEqual(hook.handle(self.event("continue")), {})

    def test_documentation_and_similar_names_do_not_activate(self):
        for prompt in ["    /poteto-mode inspect\nExplain this example", "```\n/poteto-mode inspect\n```", "> /poteto-mode inspect", "/poteto-mode-extra inspect", "`/poteto-mode` means what?", 'Explain "$poteto-mode"', "Explain '$poteto-mode'", "Use `$poteto-mode` as an example", r"Explain \$poteto-mode"]:
            self.assertEqual(hook.handle(self.event(prompt)), {})

    def test_both_published_default_prompts_activate(self):
        plugin = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())["interface"]["defaultPrompt"]
        metadata = (ROOT / "skills/poteto-mode/agents/openai.yaml").read_text()
        prompt_line = next(line for line in metadata.splitlines() if line.strip().startswith("default_prompt:"))
        skill = json.loads(prompt_line.split(":", 1)[1].strip())
        for number, prompt in enumerate([plugin, skill, "Let's use $poteto-mode for this task."]):
            response = hook.handle(self.event(prompt, session=f"default-{number}"))
            self.assertIn("ROUTER_SOURCE_MARKER", response["hookSpecificOutput"]["additionalContext"])
            self.assertTrue(pstack.read_state(f"default-{number}", str(self.project))["active"])

    def test_punctuated_and_later_line_mentions_activate(self):
        prompts = [
            "$poteto-mode: fix the bug",
            "/poteto-mode: fix the bug",
            "/poteto-mode, please fix the bug",
            "/poteto-mode.",
            "$pstack-codex:poteto-mode! fix the bug",
            "(try $poteto-mode).",
            "Context: the deploy fails at 03:00.\n\nUse $poteto-mode to find the root cause.",
            "Here is the plan.\n$pstack-codex:poteto-mode: implement step 2.",
            "  \n$poteto-mode fix the bug",
            "First `$poteto-mode` is only quoted here.\nNow really use $poteto-mode, thanks.",
            "The example:\n```\n$poteto-mode not this one\n```\nBut $poteto-mode this one.",
            '$poteto-mode fix the 5" display bug',
            'Fix the 5" display.\nUse $poteto-mode.',
            'The note says "panel 5".\nUse $poteto-mode.',
            'Use "$poteto-mode" as shown, then $poteto-mode for real.',
            'Docs say "use\n$poteto-mode" but really use $poteto-mode now.',
        ]
        for number, prompt in enumerate(prompts):
            session = f"punctuated-{number}"
            with self.subTest(prompt=prompt):
                response = hook.handle(self.event(prompt, session=session))
                context = response["hookSpecificOutput"]["additionalContext"]
                self.assertIn("ROUTER_SOURCE_MARKER", context)
                self.assertIn(f"Authoritative session ID: {session}", context)
                self.assertTrue(pstack.read_state(session, str(self.project))["active"])

    def test_examples_code_and_similar_names_on_any_line_do_not_activate(self):
        prompts = [
            "Explain this example:\n```\n$poteto-mode fix the bug\n```",
            "Example:\n~~~text\n$poteto-mode fix the bug\n~~~\nThanks",
            "Nested:\n````md\n```\n$poteto-mode fix the bug\n```\n````",
            "Unclosed fence:\n```\n$poteto-mode fix the bug",
            "Short closer:\n````\n```\n$poteto-mode fix the bug",
            "Tilde does not close backticks:\n```\n~~~\n$poteto-mode fix the bug",
            "Docs:\n    $poteto-mode fix the bug",
            "Docs:\n\t$poteto-mode fix the bug",
            "Quote:\n> $poteto-mode fix the bug",
            "Quote:\n  > $poteto-mode fix the bug",
            'The doc says "run\n$poteto-mode first."',
            'Docs say "use\n$poteto-mode" for that.',
            "He wrote: “first,\n$poteto-mode second,\nthird.”",
            'Compare "$poteto-mode" with "$poteto-mode" and `$poteto-mode`.',
            "Later:\nSee `$poteto-mode: x` for the syntax.",
            "Later:\n\\$poteto-mode is literal",
            "Later:\n$poteto-mode-extra fix the bug",
            "Later:\n$poteto-modes fix the bug",
            "Later:\n$poteto-mode:foo fix the bug",
            "Later:\n$poteto-mode.py is a file",
            "Later:\nfoo$poteto-mode fix the bug",
            "$poteto-mode:foo fix the bug",
            "/poteto-mode.py is a file",
            "/poteto-mode/README.md explains it",
            "/poteto-mode-extra: inspect",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual({}, hook.handle(self.event(prompt)))
                self.assertFalse(pstack.read_state("one", str(self.project))["active"])
        self.assertEqual({}, hook.handle(self.event("Later:\n/poteto-mode fix the bug")), "the slash form is explicit only on the first line")

    def test_new_task_after_punctuated_or_later_line_mention_resets_playbook(self):
        for prompt in ["$poteto-mode: new task. Find the cause", "Intro line.\n$poteto-mode new task: find the cause"]:
            with self.subTest(prompt=prompt):
                hook.handle(self.event("/poteto-mode investigate"))
                pstack.change_state("select", "one", str(self.project), "investigation")
                hook.handle(self.event(prompt))
                state = pstack.read_state("one", str(self.project))
                self.assertTrue(state["active"])
                self.assertIsNone(state["playbook"])
        pstack.change_state("select", "one", str(self.project), "investigation")
        hook.handle(self.event("$poteto-mode: keep going"))
        self.assertEqual("investigation", pstack.read_state("one", str(self.project))["playbook"])

    def test_first_line_exit_wins_over_later_line_mention(self):
        hook.handle(self.event("/poteto-mode investigate"))
        response = hook.handle(self.event("exit poteto-mode\nLater you may want $poteto-mode again."))
        self.assertIn("explicitly exited", response["hookSpecificOutput"]["additionalContext"])
        self.assertFalse(pstack.read_state("one", str(self.project))["active"])

    def test_stale_recorded_project_requires_authoritative_project_hint(self):
        hook.handle(self.event("/poteto-mode inspect"))
        hook.handle(self.event("/poteto-mode inspect", project=self.other))
        self.other.rmdir()
        with self.assertRaisesRegex(ValueError, "--project"):
            pstack.resolve_identity("one", None)

    def test_empty_and_tilde_codex_home_are_normalized_for_config_and_state(self):
        with patch.dict(os.environ, {"HOME": str(self.base), "CODEX_HOME": ""}):
            os.environ.pop("PSTACK_STATE_DIR", None)
            self.assertEqual(self.base / ".codex/pstack/models.json", pstack.config_path())
            self.assertEqual(self.base / ".codex/pstack/state", pstack.state_root())
            os.environ["CODEX_HOME"] = "~/alternate"
            self.assertEqual(self.base / "alternate/pstack/models.json", pstack.config_path())
            self.assertEqual(self.base / "alternate/pstack/state", pstack.state_root())

    def test_command_prefix_is_shell_safe_and_runs_without_placeholders(self):
        session = "thread 'one'"
        hook.handle(self.event("/poteto-mode investigate", session=session))
        context = hook.handle(self.event("continue", session=session))["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("<action>", context)
        prefix_line = next(line for line in context.splitlines() if line.startswith("Mode command prefix"))
        prefix = shlex.split(prefix_line.split(": ", 1)[1])
        self.assertEqual(["python3", str(self.source / "scripts/pstack.py"), "mode", "--session", session, "--project", str(self.project)], prefix)
        self.assertIn("Append exactly one action to that prefix: activate, deactivate, status, reset, or select --playbook", context)
        self.assertIn(f"Example: {prefix_line.split(': ', 1)[1]} status", context)
        command = [sys.executable, str(ROOT / "scripts/pstack.py")] + prefix[2:]
        status = json.loads(subprocess.run(command + ["status"], cwd=self.other, capture_output=True, text=True, check=True).stdout)
        self.assertEqual((session, str(self.project), True), (status["session"], status["project"], status["active"]))
        subprocess.run(command + ["select", "--playbook", "bug-fix"], cwd=self.other, capture_output=True, text=True, check=True)
        continuation = hook.handle(self.event("continue", session=session))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("current playbook: bug-fix", continuation)

    def test_cli_uses_recorded_context_after_working_directory_changes(self):
        hook.handle(self.event("/poteto-mode investigate"))
        env = {**os.environ, "CODEX_THREAD_ID": "one"}
        command = [sys.executable, str(ROOT / "scripts/pstack.py"), "mode"]
        result = subprocess.run(command + ["status"], cwd=self.other, env=env, capture_output=True, text=True, check=True)
        state = json.loads(result.stdout)
        self.assertEqual(state["project"], str(self.project))
        subprocess.run(command + ["select", "--playbook", "bug-fix"], cwd=self.other, env=env, capture_output=True, text=True, check=True)
        continuation = hook.handle(self.event("continue"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("current playbook: bug-fix", continuation)
        self.assertIn("Authoritative session ID: one", continuation)
        self.assertIn(f"Authoritative session project: {self.project}", continuation)

    def test_ambiguous_context_requires_explicit_project(self):
        hook.handle(self.event("/poteto-mode investigate"))
        hook.handle(self.event("/poteto-mode investigate", project=self.other))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            pstack.resolve_identity("one", None)
        self.assertEqual(pstack.resolve_identity("one", str(self.other)), ("one", str(self.other)))

    def test_no_recorded_playbook_does_not_instruct_restart(self):
        response = hook.handle(self.event("/poteto-mode investigate"))
        context = response["hookSpecificOutput"]["additionalContext"]
        self.assertIn("continue the workflow already in progress", context)
        self.assertNotIn("rematch the current request", context)

    def test_exit_keeps_following_task_text_out_of_style(self):
        hook.handle(self.event("/poteto-mode inspect"))
        hook.handle(self.event("exit poteto-mode\nContinue fixing the bug."))
        self.assertFalse(pstack.read_state("one", str(self.project))["active"])

    def test_continuation_is_brief_but_compaction_restores_sources(self):
        hook.handle(self.event("/poteto-mode inspect"))
        normal = hook.handle(self.event("continue"))["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("ROUTER_SOURCE_MARKER", normal)
        compact = hook.handle(self.event(name="SessionStart", source="compact"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("ROUTER_SOURCE_MARKER", compact)

    def test_nonobject_state_and_relative_model_config_fail_cleanly(self):
        path = pstack.state_path("one", str(self.project))
        path.parent.mkdir(parents=True)
        path.write_text("[]")
        with self.assertRaises(ValueError):
            pstack.read_state("one", str(self.project))
        with patch.dict(os.environ, {"PSTACK_MODEL_CONFIG": "models.json"}), self.assertRaises(ValueError):
            pstack.config_path()

    def test_relative_state_override_rejected_before_activation(self):
        previous_cwd = os.getcwd()
        try:
            os.chdir(self.base)
            with patch.dict(os.environ, {"PSTACK_STATE_DIR": "relative-state"}):
                with self.assertRaisesRegex(ValueError, "PSTACK_STATE_DIR must be an absolute path"):
                    pstack.change_state("activate", "relative-state-probe", str(self.project))
            self.assertFalse((self.base / "relative-state").exists())
        finally:
            os.chdir(previous_cwd)


if __name__ == "__main__":
    unittest.main()
