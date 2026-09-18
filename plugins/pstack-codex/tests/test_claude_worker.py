"""Claude contract tests use a fake executable and never invoke a provider."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import claude_worker as claude


FAKE_CLAUDE = r'''
import json,os,sys
args=sys.argv[1:]
def value(flag): return args[args.index(flag)+1]
assert '--safe-mode' in args
assert '--no-session-persistence' in args
assert '--bare' not in args
assert '--dangerously-skip-permissions' not in args
assert value('--permission-mode') == 'dontAsk'
assert value('--output-format') == 'stream-json'
prompt=sys.stdin.read()
model=value('--model')
scenario=os.environ.get('FAKE_SCENARIO','normal')
tools=[t for t in value('--tools').split(',') if t]
init={'type':'system','subtype':'init','model':model,'permissionMode':'dontAsk',
      'tools':tools,'mcp_servers':[],'apiKeySource':'none','cwd':os.getcwd()}
if scenario=='bad_permission': init['permissionMode']='bypassPermissions'
if scenario=='forbidden_init_tool': init['tools'].append('Write')
if scenario=='unknown_init_tool': init['tools'].append('UnclassifiedPowerTool')
if scenario=='mcp_init': init['mcp_servers']=[{'name':'unexpected'}]
if scenario=='key_auth': init['apiKeySource']='environment'
print(json.dumps(init),flush=True)
content=[{'type':'text','text':'fixture answer'}]
if scenario=='forbidden_tool_use': content.append({'type':'tool_use','id':'1','name':'Write','input':{}})
message={'model': 'wrong-model' if scenario=='wrong_model' else model,'content':content}
if scenario=='missing_model': message.pop('model')
if scenario!='usage_only': print(json.dumps({'type':'assistant','message':message}),flush=True)
if scenario=='incomplete': raise SystemExit(0)
result={'type':'result','subtype':'success','is_error':False,
        'result':json.dumps({'prompt':prompt,'tools':tools,'cwd':os.getcwd(),'effort':value('--effort')}),
        'modelUsage':{model:{'provider':'firstParty'},'auxiliary-helper':{'provider':'firstParty'}},
        'permission_denials':[]}
if scenario=='foreign_provider': result['modelUsage']['auxiliary-helper']['provider']='external'
if scenario=='provider_error': result.update(subtype='error',is_error=True)
if scenario=='permission_denial': result['permission_denials']=[{'tool_name':'Read'}]
print(json.dumps(result),flush=True)
'''


class ClaudeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pstack fake claude ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.binary_dir = self.root / "fake binaries"
        self.binary_dir.mkdir()
        binary = self.binary_dir / "claude"
        binary.write_text(f"#!{sys.executable}\n" + FAKE_CLAUDE)
        binary.chmod(0o755)
        self.prompt = self.root / "prompt file.txt"
        self.prompt.write_text("Synthetic stdin with spaces, quotes ' and æ.")
        self.count = 0

    def spec(self, **changes):
        self.count += 1
        return {"backend":"claude", "model":"fixture-fable", "effort":"xhigh", "profile":"analysis",
                "cwd":str(self.root), "prompt_file":str(self.prompt), "run_dir":str(self.root/f"run {self.count}"),
                "timeout_seconds":3, "term_grace_seconds":0.1, **changes}

    def run_fixture(self, scenario="normal", **changes):
        return claude.run_claude(self.spec(**changes), environ={"PATH":str(self.binary_dir),
                                "HOME":str(self.root), "FAKE_SCENARIO":scenario})

    def test_all_profiles_execute_with_scoped_capabilities_and_stdin(self):
        expected={"analysis":[], "reader":["Read","Glob","Grep"],
                  "writer":["Read","Write","Edit","Glob","Grep"]}
        for profile, tools in expected.items():
            with self.subTest(profile=profile):
                receipt=self.run_fixture(profile=profile)
                self.assertEqual("success",receipt["status"])
                result=json.loads(Path(receipt["result_path"]).read_text())
                self.assertEqual(self.prompt.read_text(),result["prompt"])
                self.assertEqual(tools,result["tools"])
                self.assertEqual(str(self.root),result["cwd"])
                self.assertEqual("xhigh",result["effort"])
                self.assertNotIn(self.prompt.read_text(),receipt["command_argv"])

    def test_auxiliary_usage_does_not_replace_response_model_attribution(self):
        receipt=self.run_fixture()
        self.assertTrue(receipt["requested_model_verified"])
        self.assertEqual(["fixture-fable"],receipt["observed_models"])
        self.assertEqual(["auxiliary-helper"],receipt["auxiliary_models"])

    def test_wrong_or_absent_message_models_cannot_verify(self):
        for scenario, status in [("wrong_model","model_mismatch"),("missing_model","unverified"),("usage_only","unverified")]:
            with self.subTest(scenario=scenario):
                receipt=self.run_fixture(scenario)
                self.assertEqual(status,receipt["status"])
                self.assertFalse(receipt["requested_model_verified"])

    def test_permission_and_effective_init_capabilities_are_verified(self):
        for scenario in ("bad_permission","forbidden_init_tool","mcp_init","key_auth","foreign_provider"):
            with self.subTest(scenario=scenario):
                receipt=self.run_fixture(scenario)
                self.assertEqual("unverified",receipt["status"])
                self.assertFalse(receipt["requested_model_verified"])

    def test_analysis_rejects_unclassified_extra_tools(self):
        receipt=self.run_fixture("unknown_init_tool")
        self.assertNotEqual("success",receipt["status"], "tool-free analysis exposed an unclassified extra tool")
        self.assertFalse(receipt["requested_model_verified"])

    def test_forbidden_actual_tool_use_cannot_pass_even_if_init_is_clean(self):
        receipt=self.run_fixture("forbidden_tool_use")
        self.assertEqual(1,receipt["tool_call_count"])
        self.assertNotEqual("success",receipt["status"], "analysis performed Write despite an empty init tool list")
        self.assertFalse(receipt["requested_model_verified"])

    def test_provider_failure_incomplete_stream_and_denials_are_distinct(self):
        self.assertEqual("provider_error",self.run_fixture("provider_error")["status"])
        self.assertEqual("incomplete",self.run_fixture("incomplete")["status"])
        receipt=self.run_fixture("permission_denial",profile="reader")
        self.assertEqual(1,receipt["permission_denial_count"])
        self.assertEqual(["Read"],receipt["evidence"]["permission_denied_tools"])

    def test_routing_overrides_rejected_without_disclosing_values(self):
        sentinel="synthetic-secret-do-not-echo"
        for name in claude.REJECTED_ENV:
            with self.subTest(name=name):
                with self.assertRaises(claude.SpecError) as raised:
                    claude.build_env({"PATH":str(self.binary_dir),name:sentinel})
                self.assertIn(name,str(raised.exception))
                self.assertNotIn(sentinel,str(raised.exception))
        env,policy=claude.build_env({"PATH":str(self.binary_dir),"ANTHROPIC_MODEL":sentinel})
        self.assertNotIn("ANTHROPIC_MODEL",env)
        self.assertEqual(["ANTHROPIC_MODEL"],policy["stripped_model_overrides"])
        self.assertNotIn(sentinel,json.dumps(policy))

    def test_profiles_reject_escalation_resume_and_unscoped_shell(self):
        invalid=[{"allowed_tools":["Read"]}, {"profile":"reader","allowed_tools":["Write"]},
                 {"profile":"writer","allowed_tools":["Bash"]},
                 {"profile":"writer","allowed_tools":["Bash(*)"]}, {"resume":"previous"},
                 {"session_id":"previous"}]
        for changes in invalid:
            with self.subTest(changes=changes):
                spec=self.spec(**changes)
                with self.assertRaises(claude.SpecError):
                    claude.run_claude(spec,environ={"PATH":str(self.binary_dir)})
                self.assertFalse(Path(spec["run_dir"]).exists())
        receipt=self.run_fixture(profile="writer",allowed_tools=["Bash(python3 -m unittest:*)"])
        self.assertEqual("success",receipt["status"])
        self.assertIn("Bash",json.loads(Path(receipt["result_path"]).read_text())["tools"])

    def test_dry_launch_plan_never_claims_attempt_or_contains_prompt(self):
        spec=self.spec()
        plan=claude.plan_claude(spec,environ={"PATH":str(self.binary_dir)})
        self.assertFalse(Path(spec["run_dir"]).exists())
        self.assertEqual(self.prompt.read_text(),plan["prompt_text"])
        self.assertNotIn(self.prompt.read_text(),plan["argv"])
        self.assertEqual(str(self.binary_dir/"claude"),plan["argv"][0])

    def test_reader_can_run_explicit_scoped_shell_without_edit_tools(self):
        receipt = self.run_fixture(profile="reader", allowed_tools=["Bash(git log:*)"])
        self.assertEqual(receipt["status"], "success")
        tools = json.loads(Path(receipt["result_path"]).read_text())["tools"]
        self.assertEqual(tools, ["Read", "Glob", "Grep", "Bash"])
        self.assertNotIn("Write", receipt["adapter"]["allowed_tools"])
        self.assertNotIn("Edit", receipt["adapter"]["allowed_tools"])

    def test_writer_uses_one_primary_directory_edit_rule_for_edit_and_write(self):
        plan = claude.plan_claude(self.spec(profile="writer"), environ={"PATH": str(self.binary_dir)})
        allowed = plan["adapter"]["allowed_tools"]
        self.assertIn("Edit(/**)", allowed)
        self.assertNotIn("Edit", allowed)
        self.assertNotIn("Write", allowed)
        self.assertFalse(any(rule.startswith("Write(") for rule in allowed))

    def test_partial_text_is_retained_without_claiming_completion(self):
        receipt = self.run_fixture("incomplete")
        self.assertEqual(receipt["status"], "incomplete")
        self.assertFalse(receipt["requested_model_verified"])
        self.assertEqual(Path(receipt["result_path"]).read_text(), "fixture answer")
        parsed = json.loads(Path(receipt["parsed_path"]).read_text())
        self.assertEqual(parsed["partial_text"], "fixture answer")
        self.assertTrue(receipt["evidence"]["partial_unterminated"])


if __name__ == "__main__":
    unittest.main()
