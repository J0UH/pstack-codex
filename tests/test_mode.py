import importlib.util
import json
import os
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
