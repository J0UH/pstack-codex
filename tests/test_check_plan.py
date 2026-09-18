"""Codex plan checker: retained upstream gates, host-evidenced markers and an explicit lane policy."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_plan.mjs"
UPSTREAM_CHECKER = ROOT / "upstream/pstack/skills/poteto-mode/scripts/check-plan.mjs"
PACKAGED_CHECKER = ROOT / "skills/poteto-mode/scripts/check-plan.mjs"
FIXTURES = ROOT / "tests/fixtures/plans"
CODEX_PLAN = FIXTURES / "codex-autopilot-full.md"
CURSOR_PLAN = FIXTURES / "cursor-autopilot-full.md"
POLICY = FIXTURES / "models.codex.json"
INHERIT_POLICY = FIXTURES / "models.inherit.json"
EXAMPLE_POLICY = ROOT / "examples/models.astra-claude.json"
CAPABILITIES = ROOT / "docs/workflow-capabilities.json"
NATIVE_DOC = ROOT / "docs/native-workflows.md"
NODE = shutil.which("node")

LANE_SENTENCE = "Ten lanes on `claude-fable-5-1` at the PR head"
STRUCTURAL_MESSAGES = (
    "sub-blocks are", "lanes are [", "names no screenshot", "has no pass predicate", "live box is not a lane",
    "perf boxes are", "does not open with the rule", "Review gate", "has no box", "names nothing", "long dash",
    "curly quote", "mid-sentence colon", "intro is", "no H1 title", "How to read this lacks", "not an appendix",
    "Prototype evidence", "no PR sections", "in order",
)
HOST_MESSAGES = (
    'Verify, live lacks "Ten lanes on', 'lacks "create_goal"', 'lacks "automation_update"', 'lacks "heartbeat"',
    'lacks "pinned"', 'lacks "PAUSED"', 'lacks "reconcile"', "playbooks", "still uses Cursor", "Boot recipe lacks",
    "Close the program lacks",
)
WORKTREE_ONLY_BOOT = (
    "Each live lane runs in its own git worktree at the PR head on this machine. Cloud placement is unavailable on this host, "
    "so a lane that needs it reports blocked instead of pretending. Drive through `control-ui` from the packaged companion skills.\n"
    "\n"
    "- [ ] `git fetch origin <head-branch> && git worktree add /tmp/swarm-<pr-id>/worker-<n>/tree <head SHA>`.\n"
    "- [ ] Start the dashboard backend with `npm run dev` in the worktree and wait for `ready on :3000` in the log.\n"
)
CODEX_BOOT_PROSE = (
    "Each live lane runs on its own cloud VM at the PR head. Drive through `control-ui` from the packaged companion skills. "
    "This host exposes no cloud placement, so a lane reports blocked at this step until the operator configures an isolated runtime "
    "per lane or explicitly approves an alternative that gives each lane its own port, browser profile, and data directory, with "
    "evidence of each. A git worktree alone is write ownership, not runtime isolation, and ten lanes on one host's port collide. "
    "Passing the plan checker verifies this plan's form, not runtime readiness.\n"
)
CODEX_BOOT_BOXES = (
    "- [ ] `git fetch origin <head-branch> && git checkout <head SHA>` on the lane's own runtime.\n"
    "- [ ] Start the dashboard backend with `npm run dev` on the lane's own runtime and wait for `ready on :3000` in its log.\n"
)
WORKTREE_RULE = "Boot recipe places a lane in a worktree without separate port, browser, and data evidence; a worktree alone is not runtime isolation"
APPROVED_WORKTREE_BOX = (
    "- [ ] The operator approved the alternative in writing. `git fetch origin <head-branch> && git worktree add "
    "/tmp/swarm-<pr-id>/worker-<n>/tree <head SHA>` and run the lane in its own worktree.\n"
)
CLOUD_RULE = 'Boot recipe lacks "/own cloud VM/"'
BLOCKED_RULE = 'Boot recipe lacks "blocked"'
HOLD_RULE = "Program checklist closes the goal on the operator's hold; a hold pauses the heartbeat and leaves the goal active"
RRULE_RULE = "raw RRULE string; state the cadence in words and keep the schedule encoding in the automation_update arguments"


def run(checker, *args, env=None, cwd=None):
    base = {key: value for key, value in os.environ.items() if key not in ("PSTACK_MODEL_CONFIG", "CODEX_HOME")}
    base.update(env or {})
    return subprocess.run([NODE, str(checker), *[str(a) for a in args]], capture_output=True, text=True, env=base, cwd=str(cwd or ROOT))


@unittest.skipIf(NODE is None, "node is not installed; the plan checker cannot be exercised")
class CheckPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / "codex-home"
        self.home.mkdir()
        self.env = {"CODEX_HOME": str(self.home)}
        self.plan = CODEX_PLAN.read_text()

    def check(self, text, *args, policy=POLICY, env=None):
        path = self.base / f"plan-{len(list(self.base.iterdir()))}.md"
        path.write_text(text)
        extra = ("--policy", policy) if policy is not None else ()
        return run(CHECKER, path, *extra, *args, env={**self.env, **(env or {})})

    def mutate(self, old, new, count=None, text=None):
        source = self.plan if text is None else text
        self.assertIn(old, source, f"fixture no longer contains {old!r}")
        if count is not None:
            self.assertEqual(count, source.count(old))
        return source.replace(old, new)

    def assert_problem(self, result, *messages):
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        for message in messages:
            self.assertIn(message, result.stderr, result.stdout + result.stderr)

    def assert_passes(self, result):
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stderr)

    def test_upstream_checker_stays_byte_identical_and_separate(self):
        self.assertEqual(UPSTREAM_CHECKER.read_bytes(), PACKAGED_CHECKER.read_bytes())
        source = CHECKER.read_text()
        self.assertNotEqual(UPSTREAM_CHECKER.read_bytes(), CHECKER.read_bytes())
        self.assertNotIn('const LANES = "Ten lanes on', source)
        self.assertIn("lanePolicy.model", source)
        for retained in ('"Depends on."', "1,2,3,4,5,6,7,8,9,10", "Save `[^`]+`", "Pass when", '"Metric.", "Probe.", "Baseline.", "Rule."',
                         '["screenshot", "video", "operator"]', "Prototype evidence", "long dash", "curly quote", "mid-sentence colon",
                         "/30[- ]minute/", '"status message"'):
            self.assertIn(retained, source)
        self.assertNotIn("RRULE:FREQ", source)

    def test_explicit_model_rejects_terminal_whitespace_before_checking_plan(self):
        for suffix in ("\n", "\r", "\u0085", "\ufeff"):
            with self.subTest(suffix=repr(suffix)):
                result = self.check(self.plan, "--lanes-model", "claude-fable-5-1" + suffix, policy=None)
                self.assertEqual(2, result.returncode)
                self.assertIn("exact model token", result.stderr)

    def test_raw_schedule_encoding_is_rejected_inside_fenced_plan_text(self):
        text = self.plan + "\n```text\nRRULE:FREQ=MINUTELY;INTERVAL=30\n```\n"
        self.assert_problem(self.check(text), RRULE_RULE)

    def test_empty_and_tilde_codex_home_match_the_python_policy_resolver(self):
        for configured in ("", "~/alternate"):
            with self.subTest(configured=configured):
                home = self.base / ("empty-home" if not configured else "tilde-home")
                policy_home = home / (".codex" if not configured else "alternate")
                policy_file = policy_home / "pstack/models.json"
                policy_file.parent.mkdir(parents=True)
                policy_file.write_bytes(POLICY.read_bytes())
                env = {"HOME": str(home), "CODEX_HOME": configured}
                result = run(CHECKER, CODEX_PLAN, env=env)
                self.assert_passes(result)
                self.assertIn("source=" + str(policy_file), result.stdout)
                python_env = {k: v for k, v in os.environ.items() if k != "PSTACK_MODEL_CONFIG"}
                python_env.update(env)
                result = subprocess.run([sys.executable, str(ROOT / "scripts/pstack.py"), "models", "path"],
                                        env=python_env, capture_output=True, text=True, check=True)
                self.assertEqual(str(policy_file), result.stdout.strip())

    def test_cursor_form_passes_upstream_and_codex_form_is_not_forged_for_upstream(self):
        upstream_on_cursor = run(UPSTREAM_CHECKER, CURSOR_PLAN)
        self.assertEqual(0, upstream_on_cursor.returncode, upstream_on_cursor.stdout + upstream_on_cursor.stderr)
        self.assertIn("2 PR sections, 0 problems", upstream_on_cursor.stdout)
        upstream_on_codex = run(UPSTREAM_CHECKER, CODEX_PLAN)
        self.assertEqual(1, upstream_on_codex.returncode)
        self.assertIn('Verify, live lacks "Ten lanes on `grok-4.6-fast-xhigh` at the PR head"', upstream_on_codex.stderr)
        self.assertIn('Program checklist lacks "/goal"', upstream_on_codex.stderr)
        self.assertIn('Program checklist lacks "git show origin/main:"', upstream_on_codex.stderr)

    def test_host_correct_plan_passes_with_explicit_policy(self):
        result = run(CHECKER, CODEX_PLAN, "--policy", POLICY, env=self.env)
        self.assert_passes(result)
        self.assertIn("Add the widget list (WQ-1)  boxes=", result.stdout)
        self.assertIn("verify-live=10", result.stdout)
        self.assertIn(f"lanes  model=claude-fable-5-1 backend=claude effort=xhigh source={POLICY}", result.stdout)
        self.assertIn("runtime  per-lane isolated runtime, goal, and heartbeat are host prerequisites this checker does not certify", result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith("2 PR sections, 0 problems"))

    def test_codex_fixture_keeps_the_original_placement_and_states_the_cadence_in_words(self):
        boot = self.plan[self.plan.index("### Boot recipe"):self.plan.index("## Add the widget list")]
        self.assertIn("Each live lane runs on its own cloud VM at the PR head.", boot)
        self.assertIn("reports blocked", boot)
        self.assertIn("not runtime isolation", boot)
        self.assertNotIn("worktree add", boot)
        self.assertIn("on the lane's own runtime", boot)
        for claim in ("cloud executor verified", "cloud placement is configured", "isolated runtime is verified"):
            self.assertNotIn(claim, self.plan.lower())
        self.assertIn("video of the change on a lane VM", self.plan)
        self.assertNotIn("RRULE", self.plan)
        self.assertNotIn("FREQ=", self.plan)
        self.assertIn("Arm the 30-minute audit tick as a native heartbeat", self.plan)
        self.assertIn("The schedule encoding is a tool argument and never plan text.", self.plan)
        self.assertNotIn("/goal", self.plan)
        self.assertNotIn("/loop", self.plan)
        self.assertIn("Leave the goal active. A hold is not completion and not a blocker.", self.plan)
        self.assertIn("confirm it stopped, and reconcile its branch, checkout, and PR effects before dispatching its replacement", self.plan)
        self.assertIn("When the stop or the ownership is uncertain, report it and dispatch nothing.", self.plan)

    def test_cursor_form_fails_codex_checker_only_for_host_reasons(self):
        result = run(CHECKER, CURSOR_PLAN, "--policy", POLICY, env=self.env)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        self.assertTrue(lines)
        for line in lines:
            self.assertTrue(any(marker in line for marker in HOST_MESSAGES), line)
            self.assertFalse(any(marker in line for marker in STRUCTURAL_MESSAGES), line)
        joined = "\n".join(lines)
        for expected in (f'Verify, live lacks "{LANE_SENTENCE}"', 'lacks "create_goal"', 'lacks "automation_update"',
                         'lacks "heartbeat"', 'lacks "pinned"', 'lacks "PAUSED"', 'lacks "reconcile"', 'still uses Cursor "/loop"',
                         'still uses Cursor "cloud-sleeper"', 'still uses Cursor "git show origin/main:pstack/"',
                         BLOCKED_RULE, 'Close the program lacks "update_goal"', 'Close the program lacks "PAUSED"'):
            self.assertIn(expected, joined)
        self.assertNotIn(CLOUD_RULE, joined)
        self.assertNotIn(WORKTREE_RULE, joined)

    def test_retained_structural_gates_fail_when_removed(self):
        lane7 = "- [ ] Lane 7. Load with a widget whose name is 120 characters. Save `wq1-long-name.png`. Pass when the name truncates with an ellipsis.\n"
        gate_boxes = ("- [ ] Copy lane 2 screenshots into `docs/media/wq2-review-filter.png`.\n"
                      "- [ ] Record a 30 to 60 second video of the change on a lane VM. Save it as `docs/media/wq2-review.mp4`.\n"
                      "- [ ] Post the screenshots and the video in chat. Stop at merge-ready. Wait for the operator's click.\n")
        merge_boxes = ("- [ ] Root's clean verdict at the exact head SHA.\n- [ ] Bugbot triage done.\n"
                       "- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.\n")
        cases = [
            ("missing lane", lane7, "", "Add the widget list (WQ-1): lanes are [1,2,3,4,5,6,8,9,10], expected 1 to 10"),
            ("lane without screenshot", "Save `wq1-empty.png`. Pass when the empty state text is visible.",
             "Pass when the empty state text is visible.", "Add the widget list (WQ-1): lane 2 names no screenshot"),
            ("lane without predicate", "Save `wq1-one.png`. Pass when exactly one row renders.",
             "Save `wq1-one.png`. Exactly one row renders.", "Add the widget list (WQ-1): lane 3 has no pass predicate"),
            ("live box not a lane", "- [ ] Lane 10. Navigate away and back.", "- [ ] Navigate away and back.",
             "Add the widget list (WQ-1): live box is not a lane"),
            ("sub-block order", "**Build.**\n\n- [ ] Add `WidgetList`", "**Built.**\n\n- [ ] Add `WidgetList`",
             "Add the widget list (WQ-1): sub-blocks are [Depends on., Files., You see., Verify, unit., Verify, live., Verify, perf., Review gate., Merge.]"),
            ("perf boxes", "- [ ] Baseline. Record the trunk median first.\n", "",
             "Add the widget list (WQ-1): perf boxes are [Metric., Probe., Rule.], expected [Metric., Probe., Baseline., Rule.]"),
            ("verify unit rule", "**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.\n\n- [ ] `src/widgets/list/WidgetList.test.tsx`",
             "**Verify, unit.** Unit tests.\n\n- [ ] `src/widgets/list/WidgetList.test.tsx`", "Add the widget list (WQ-1): Verify, unit. does not open with the rule"),
            ("verify live rule", "**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `claude-fable-5-1` at the PR head, per the boot recipe.\n\n- [ ] Lane 1. Regression lane against trunk. Run the twelve-widget",
             "**Verify, live.** Ten lanes on `claude-fable-5-1` at the PR head, per the boot recipe.\n\n- [ ] Lane 1. Regression lane against trunk. Run the twelve-widget",
             "Add the widget filter (WQ-2): Verify, live. does not open with the rule"),
            ("review gate lacks video", "- [ ] Record a 30 to 60 second video of the change on a lane VM. Save it as `docs/media/wq2-review.mp4`.\n"
             "- [ ] Post the screenshots and the video in chat.", "- [ ] Post the screenshots in chat.",
             'Add the widget filter (WQ-2): Review gate lacks "video"'),
            ("review gate none with boxes", "**Review gate.** None. WQ-1 is not review-gated.\n",
             "**Review gate.** None. WQ-1 is not review-gated.\n\n- [ ] Post a screenshot anyway.\n", "Add the widget list (WQ-1): Review gate says None but has boxes"),
            ("review gate no box", gate_boxes, "The operator reviews the screenshot and video in chat.\n", "Add the widget filter (WQ-2): Review gate has no box"),
            ("merge no box", merge_boxes + "- [ ] The owner squash-merges WQ-1 through the resolved forge.\n", "The owner merges after the verdict.\n",
             "Add the widget list (WQ-1): Merge. has no box"),
            ("files no box", "- [ ] Create `src/widgets/list/WidgetList.tsx`.\n- [ ] Edit `src/dashboard/Dashboard.tsx`.\n",
             "Edit the list and dashboard files.\n", "Add the widget list (WQ-1): Files. has no box"),
            ("depends on empty", "**Depends on.** None.", "**Depends on.**", "Add the widget list (WQ-1): Depends on names nothing"),
            ("close missing", "## Close the program", "## Close the show", 'no "## Close the program" section'),
            ("prototype appendix missing", "## Appendix A. Prototype evidence", "## Appendix A. Sketch evidence", 'no "## Appendix ... Prototype evidence" section'),
            ("non-appendix after close", "## Appendix B. Alternatives rejected", "## Alternatives rejected",
             '"## Alternatives rejected" after Close the program is not an appendix'),
            ("program H3 missing", "### Verdict and merge, for every PR", "### Verdicts, for every PR", 'Program checklist lacks "### Verdict and merge" in order'),
            ("how to read marker", "One box is one unit of work.", "One box is one task.", 'How to read this lacks "One box is one unit of work"'),
            ("status message", "Then post a status message to the operator", "Then post an update to the operator", 'Program checklist lacks "status message"'),
            ("thirty minute cadence", "Arm the 30-minute audit tick", "Arm the half-hour audit tick", 'Program checklist lacks "/30[- ]minute/"'),
            ("long dash", "Dashboard users get a list they can narrow.", "Dashboard users get a list — they can narrow.", "long dash"),
            ("curly quote", "Dashboard users get a list they can narrow.", "Dashboard users get a “list” they can narrow.", "curly quote"),
            ("mid-sentence colon", "Dashboard users get a list they can narrow.", "Dashboard users: they get a list they can narrow.", "mid-sentence colon"),
            ("no H1", "# Widget queue plan", "Widget queue plan", "no H1 title"),
            ("how to read missing", "## How to read this", "## How to read", 'no "## How to read this" section'),
            ("program checklist missing", "## Program checklist", "## Program list", 'no "## Program checklist" section'),
        ]
        for label, old, new, message in cases:
            with self.subTest(label):
                self.assert_problem(self.check(self.mutate(old, new)), message)
        first_pr = self.plan.index("## Add the widget list (WQ-1)")
        close = self.plan.index("## Close the program")
        self.assert_problem(self.check(self.plan[:first_pr] + self.plan[close:]), "no PR sections between Program checklist and Close the program")

    def test_program_H3_order_and_intro_length_are_enforced(self):
        swapped = self.mutate("### PR mechanics, for every PR", "### Boot recipe extras").replace("### Boot recipe, for every live lane", "### PR mechanics, for every PR")
        self.assert_problem(self.check(swapped), 'Program checklist lacks "### Verdict and merge" in order')
        padding = "".join(f"Intro line {i}.\n" for i in range(9))
        long_intro = self.mutate("# Widget queue plan\n\n", "# Widget queue plan\n\n" + padding)
        self.assert_problem(self.check(long_intro), "intro is 10 lines, under ten required")

    def test_host_markers_fail_when_removed(self):
        cases = [
            ("goal tool", "call `create_goal` with this exact objective", "arm the goal with this exact objective", 'Program checklist lacks "create_goal"'),
            ("heartbeat tool", "Call `automation_update` with `mode: create`", "Call the scheduler with `mode: create`", 'Program checklist lacks "automation_update"'),
            ("heartbeat kind", "heartbeat", "tick", 'Program checklist lacks "heartbeat"'),
            ("pinned source", "pinned", "installed", 'Program checklist lacks "pinned"'),
            ("stale loop", "Never leave the cadence to memory.", "Never leave the cadence to memory or a terminal `/loop`.",
             'Program checklist still uses Cursor "/loop"; arm the native automation_update heartbeat instead'),
            ("stale cloud sleeper", "Never leave the cadence to memory.", "In a cloud root, a cloud-sleeper wake chain. Never leave the cadence to memory.",
             'Program checklist still uses Cursor "cloud-sleeper"'),
            ("stale trunk read", "  - [ ] `<plugin>/skills/swarm/SKILL.md`\n", "  - [ ] `git show origin/main:pstack/skills/swarm/SKILL.md`\n",
             'Program checklist still uses Cursor "git show origin/main:pstack/"'),
            ("close goal", "then call `update_goal` with `status: complete`", "then mark the goal complete", 'Close the program lacks "update_goal"'),
            ("close pause", "so its status is `PAUSED`", "so it stops", 'Close the program lacks "PAUSED"'),
            ("unknown playbook", "skills/poteto-mode/playbooks/autopilot-full.md", "skills/poteto-mode/playbooks/autopilot-fully.md",
             'Program checklist names playbook "autopilot-fully", which the pinned package does not contain'),
        ]
        for label, old, new, message in cases:
            with self.subTest(label):
                self.assert_problem(self.check(self.mutate(old, new)), message)
        no_path = self.mutate("  - [ ] `<plugin>/skills/poteto-mode/playbooks/autopilot-full.md`\n", "").replace(
            "  - [ ] `<plugin>/skills/poteto-mode/playbooks/opening-a-pr.md`\n", "")
        self.assert_problem(self.check(no_path), 'Program checklist lacks "/skills\\/poteto-mode\\/playbooks\\/[a-z0-9-]+\\.md/"')

    def test_cadence_is_stated_in_words_and_raw_rrule_is_rejected(self):
        in_words = self.mutate("Arm the 30-minute audit tick", "Arm the audit tick every 30 minutes")
        self.assert_passes(self.check(in_words))
        raw_arg = self.mutate("on that cadence", "with `rrule: RRULE:FREQ=MINUTELY;INTERVAL=30`")
        self.assert_problem(self.check(raw_arg), RRULE_RULE)
        raw_prompt = self.mutate("Use this tick prompt, verbatim. \"", "Use this tick prompt, verbatim. \"Cadence RRULE:FREQ=MINUTELY;INTERVAL=30. ")
        self.assert_problem(self.check(raw_prompt), RRULE_RULE)
        no_cadence = self.mutate("Arm the 30-minute audit tick", "Arm the audit tick")
        self.assert_problem(self.check(no_cadence), 'Program checklist lacks "/30[- ]minute/"; state the audit cadence in words')

    def test_boot_recipe_requires_the_original_per_lane_isolation(self):
        prose_start = self.plan.index(CODEX_BOOT_PROSE)
        boxes_end = self.plan.index(CODEX_BOOT_BOXES) + len(CODEX_BOOT_BOXES)
        downgraded = self.plan[:prose_start] + WORKTREE_ONLY_BOOT + self.plan[boxes_end:]
        self.assert_problem(self.check(downgraded), CLOUD_RULE, WORKTREE_RULE)
        self.assertNotIn(BLOCKED_RULE, self.check(downgraded).stderr)

        no_cloud = self.mutate("Each live lane runs on its own cloud VM at the PR head.", "Each live lane runs at the PR head.")
        self.assert_problem(self.check(no_cloud), CLOUD_RULE)

        no_block = self.mutate(
            "This host exposes no cloud placement, so a lane reports blocked at this step until the operator configures an isolated runtime per lane or explicitly approves an alternative that gives each lane its own port, browser profile, and data directory, with evidence of each. ",
            "The operator may approve an alternative that gives each lane its own port, browser profile, and data directory. ")
        self.assert_problem(self.check(no_block), BLOCKED_RULE)

        checkout_box = "- [ ] `git fetch origin <head-branch> && git checkout <head SHA>` on the lane's own runtime.\n"
        approved = self.mutate(checkout_box, APPROVED_WORKTREE_BOX)
        self.assert_passes(self.check(approved))
        no_evidence = self.mutate("its own port, browser profile, and data directory, with evidence of each", "its own checkout", text=approved)
        no_evidence = self.mutate("A git worktree alone is write ownership, not runtime isolation, and ten lanes on one host's port collide. ", "", text=no_evidence)
        self.assert_problem(self.check(no_evidence), WORKTREE_RULE)
        self.assertNotIn(CLOUD_RULE, self.check(no_evidence).stderr)

        no_worktree_mention = self.mutate("A git worktree alone is write ownership, not runtime isolation, and ten lanes on one host's port collide. ", "")
        self.assert_passes(self.check(no_worktree_mention))

    def test_goal_gates_hold_leaves_the_goal_active(self):
        closes_on_hold = self.mutate("Leave the goal active. A hold is not completion and not a blocker.", "Then call `update_goal` with `status: blocked`.")
        self.assert_problem(self.check(closes_on_hold), HOLD_RULE)
        completes_on_hold = self.mutate("Leave the goal active. A hold is not completion and not a blocker.", "Then call `update_goal` with `status: complete`.")
        self.assert_problem(self.check(completes_on_hold), HOLD_RULE)
        no_pause = self.mutate("send every owner a zero-writes order at once and set the heartbeat to `status: PAUSED`.", "send every owner a zero-writes order at once.")
        self.assert_problem(self.check(no_pause), 'Program checklist lacks "PAUSED"; the operator\'s hold pauses the heartbeat and leaves the goal active')
        goal_in_close_only = self.mutate("Leave the goal active. A hold is not completion and not a blocker.", "Leave the goal in place.")
        self.assert_passes(self.check(goal_in_close_only))

    def test_stuck_lane_replacement_waits_for_a_confirmed_stop(self):
        same_tick = self.mutate(
            "Stand down a stuck lane, confirm it stopped, and reconcile its branch, checkout, and PR effects before dispatching its replacement. When the stop or the ownership is uncertain, report it and dispatch nothing.",
            "Stand down a stuck lane and dispatch its replacement now.")
        self.assert_problem(self.check(same_tick), 'Program checklist lacks "reconcile"; confirm a stuck lane\'s stop and reconcile its effects before dispatching its replacement')

    def test_lane_model_comes_from_policy_and_inconsistency_fails(self):
        example = run(CHECKER, CODEX_PLAN, "--policy", EXAMPLE_POLICY, env=self.env)
        self.assertEqual(1, example.returncode, example.stdout + example.stderr)
        self.assertIn("lanes  model=gpt-6-astra backend=native effort=xhigh", example.stdout)
        self.assertEqual(2, example.stderr.count('Verify, live lacks "Ten lanes on `gpt-6-astra` at the PR head"'))
        self.assertNotIn("lanes are", example.stderr)

        disagree = run(CHECKER, CODEX_PLAN, "--policy", POLICY, "--lanes-model", "gpt-6-astra", env=self.env)
        self.assertEqual(2, disagree.returncode)
        self.assertIn('--lanes-model gpt-6-astra disagrees with', disagree.stderr)
        agree = run(CHECKER, CODEX_PLAN, "--policy", POLICY, "--lanes-model", "claude-fable-5-1", env=self.env)
        self.assertEqual(0, agree.returncode, agree.stdout + agree.stderr)

        cursor_slug = self.mutate("Ten lanes on `claude-fable-5-1` at the PR head", "Ten lanes on `grok-4.6-fast-xhigh` at the PR head", count=2)
        self.assert_problem(self.check(cursor_slug), f'Verify, live lacks "{LANE_SENTENCE}"')

    def test_inherited_or_missing_lane_role_needs_an_explicit_model(self):
        inherit = run(CHECKER, CODEX_PLAN, "--policy", INHERIT_POLICY, env=self.env)
        self.assertEqual(2, inherit.returncode)
        self.assertIn('"swarm workers" is inherit-parent; the lanes need an exact identity', inherit.stderr)
        pinned = run(CHECKER, CODEX_PLAN, "--policy", INHERIT_POLICY, "--lanes-model", "claude-fable-5-1", env=self.env)
        self.assertEqual(0, pinned.returncode, pinned.stdout + pinned.stderr)
        self.assertIn('source=--lanes-model (', pinned.stdout)
        self.assertIn("inherit-parent", pinned.stdout)
        other = run(CHECKER, CODEX_PLAN, "--policy", INHERIT_POLICY, "--lanes-model", "gpt-6-astra", env=self.env)
        self.assertEqual(1, other.returncode)
        self.assertIn('Verify, live lacks "Ten lanes on `gpt-6-astra` at the PR head"', other.stderr)

        unresolved = self.base / "unresolved.json"
        unresolved.write_text(json.dumps({"schema_version": 1, "roles": {"bug-fix": {"backend": "claude", "model": "claude-fable-5-1", "effort": "xhigh"}}}))
        result = run(CHECKER, CODEX_PLAN, "--policy", unresolved, env=self.env)
        self.assertEqual(2, result.returncode)
        self.assertIn('roles["swarm workers"] is unresolved', result.stderr)
        explicit = run(CHECKER, CODEX_PLAN, "--policy", unresolved, "--lanes-model", "claude-fable-5-1", env=self.env)
        self.assertEqual(0, explicit.returncode, explicit.stdout + explicit.stderr)
        self.assertIn('leaves "swarm workers" unresolved', explicit.stdout)

    def test_policy_resolution_follows_the_pstack_config_path(self):
        missing = run(CHECKER, CODEX_PLAN, env=self.env)
        self.assertEqual(2, missing.returncode)
        self.assertIn(f"no model policy at {self.home / 'pstack/models.json'}", missing.stderr)
        by_flag = run(CHECKER, CODEX_PLAN, "--lanes-model", "claude-fable-5-1", env=self.env)
        self.assertEqual(0, by_flag.returncode, by_flag.stdout + by_flag.stderr)
        self.assertIn("source=--lanes-model\n", by_flag.stdout)

        (self.home / "pstack").mkdir()
        shutil.copy(POLICY, self.home / "pstack/models.json")
        by_home = run(CHECKER, CODEX_PLAN, env=self.env)
        self.assertEqual(0, by_home.returncode, by_home.stdout + by_home.stderr)
        self.assertIn(f"source={self.home / 'pstack/models.json'}", by_home.stdout)

        by_env = run(CHECKER, CODEX_PLAN, env={**self.env, "PSTACK_MODEL_CONFIG": str(EXAMPLE_POLICY)})
        self.assertEqual(1, by_env.returncode)
        self.assertIn("lanes  model=gpt-6-astra", by_env.stdout)
        relative = run(CHECKER, CODEX_PLAN, env={**self.env, "PSTACK_MODEL_CONFIG": "relative/models.json"})
        self.assertEqual(2, relative.returncode)
        self.assertIn("PSTACK_MODEL_CONFIG must be an absolute path", relative.stderr)
        absent = run(CHECKER, CODEX_PLAN, "--policy", self.base / "none.json", env=self.env)
        self.assertEqual(2, absent.returncode)
        self.assertIn("model policy not found", absent.stderr)

    def test_malformed_policies_and_usage_errors_exit_two(self):
        bad = {
            "panel": {"schema_version": 1, "roles": {"swarm workers": [{"backend": "native", "model": "auto"}]}},
            "effort": {"schema_version": 1, "roles": {"swarm workers": {"backend": "claude", "model": "m", "effort": "ultra"}}},
            "missing effort": {"schema_version": 1, "roles": {"swarm workers": {"backend": "claude", "model": "m"}}},
            "alias effort": {"schema_version": 1, "roles": {"swarm workers": {"backend": "native", "model": "auto", "effort": "high"}}},
            "alias backend": {"schema_version": 1, "roles": {"swarm workers": {"backend": "claude", "model": "inherit-parent"}}},
            "backend": {"schema_version": 1, "roles": {"swarm workers": {"backend": "cursor", "model": "m", "effort": "high"}}},
            "token": {"schema_version": 1, "roles": {"swarm workers": {"backend": "claude", "model": "two words", "effort": "high"}}},
            "schema": {"schema_version": 2, "roles": {}},
            "roles": {"schema_version": 1},
        }
        for label, value in bad.items():
            with self.subTest(label):
                path = self.base / f"{label.replace(' ', '-')}.json"
                path.write_text(json.dumps(value))
                result = run(CHECKER, CODEX_PLAN, "--policy", path, env=self.env)
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.assertIn("Usage: node check_plan.mjs", result.stderr)
        broken = self.base / "broken.json"
        broken.write_text("{")
        self.assertEqual(2, run(CHECKER, CODEX_PLAN, "--policy", broken, env=self.env).returncode)
        self.assertEqual(2, run(CHECKER, env=self.env).returncode)
        self.assertEqual(2, run(CHECKER, CODEX_PLAN, "--policy", POLICY, "--bogus", env=self.env).returncode)
        self.assertEqual(2, run(CHECKER, CODEX_PLAN, "--policy", POLICY, "--lanes-model", "auto", env=self.env).returncode)
        self.assertEqual(2, run(CHECKER, self.base / "absent.md", "--policy", POLICY, env=self.env).returncode)

    def test_checker_reads_the_plan_from_any_working_directory(self):
        result = run(CHECKER, CODEX_PLAN, "--policy", POLICY, env=self.env, cwd=self.base)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


class CapabilityMapTests(unittest.TestCase):
    STATUSES = {"native-mapped", "live-tested", "prerequisite", "unavailable"}
    EVENT_DEPENDENT = ("babysit", "shipping", "orchestrate")
    HEARTBEAT_PLAYBOOKS = ("autonomous-run", "autopilot-full", "autopilot-stack", "babysit", "shipping", "orchestrate", "hillclimb", "visual-parity")
    ISOLATION_DEPENDENT = ("autopilot-full", "autopilot-stack", "shipping")

    def setUp(self):
        self.text = CAPABILITIES.read_text()
        self.data = json.loads(self.text)
        self.mechanisms = self.data["host_mechanisms"]
        self.playbooks = {entry["playbook"]: entry for entry in self.data["playbooks"]}

    def test_capability_map_covers_every_playbook_with_a_known_status(self):
        stems = sorted(p.stem for p in (ROOT / "skills/poteto-mode/playbooks").glob("*.md"))
        upstream = sorted(p.stem for p in (ROOT / "upstream/pstack/skills/poteto-mode/playbooks").glob("*.md"))
        self.assertEqual(23, len(stems))
        self.assertEqual(stems, upstream)
        self.assertEqual(self.STATUSES, set(self.data["status_vocabulary"]))
        self.assertEqual(stems, sorted(self.playbooks))
        for entry in self.data["playbooks"]:
            with self.subTest(entry["playbook"]):
                self.assertTrue((ROOT / entry["source"]).is_file())
                self.assertTrue((ROOT / entry["packaged"]).is_file())
                self.assertIn(entry["mapping"], self.STATUSES)
                self.assertIn(entry["live_proof"]["status"], {"live-tested", "pending", "blocked"})
                self.assertIn(entry["unattended"], self.STATUSES | {"not-applicable"})
                self.assertTrue(entry["stopping_rule"].strip())
                self.assertTrue(entry["dependencies"])
                for dependency in entry["dependencies"]:
                    self.assertIn(dependency["status"], self.STATUSES)
                    self.assertTrue(dependency["facility"] and dependency["codex"])
        for name in ("create_goal", "get_goal", "update_goal", "automation_update", "spawn_agent", "read_thread", "claude_worker", "grok_worker",
                     "grok_bot_mcp", "slack_event_trigger", "cloud_placement", "event_bridge", "codex_skill_creator", "mode_hooks"):
            self.assertIn(name, self.mechanisms)
            self.assertIn(self.mechanisms[name]["status"], self.STATUSES)
            self.assertIn(self.mechanisms[name]["live_proof"], {"live-tested", "pending", "blocked", "not-applicable"})

    def test_parent_wake_evidence_does_not_claim_unrelated_workflow_completion(self):
        heartbeat = self.mechanisms["automation_update"]
        self.assertEqual("live-tested", heartbeat["status"])
        self.assertEqual("live-tested", heartbeat["live_proof"])
        self.assertIn("CODEX_THREAD_ID", heartbeat["evidence"])
        self.assertIn("PAUSED", heartbeat["evidence"])
        record = self.data["parent_evidence"]["automation_update_heartbeat"]
        self.assertTrue(record["scheduled_turn_observed"] and record["sentinel_verified"] and record["session_identity_matches"])
        self.assertTrue(record["pause_confirmed"] and record["delete_confirmed"] and record["workspace_write_sandbox"])
        self.assertIn("not", record["scope"])
        for name in ("create_goal", "get_goal", "update_goal", "cloud_placement", "event_bridge", "watch_pr"):
            with self.subTest(name):
                self.assertNotEqual("live-tested", self.mechanisms[name]["live_proof"])
        for stem in self.HEARTBEAT_PLAYBOOKS:
            with self.subTest(stem):
                self.assertNotEqual("live-tested", self.playbooks[stem]["live_proof"]["status"])
        bridge = self.mechanisms["event_bridge"]
        self.assertEqual("unavailable", bridge["status"])
        self.assertEqual("pending", bridge["live_proof"])
        self.assertIn("did not start", bridge["evidence"])
        self.assertIn("not verified", bridge["notes"])
        self.assertNotIn("bridge verified", bridge["notes"])
        for stem in self.EVENT_DEPENDENT:
            with self.subTest(stem):
                self.assertEqual("prerequisite", self.playbooks[stem]["unattended"])

    def test_isolation_and_transcript_limits_are_not_downgraded(self):
        cloud = self.mechanisms["cloud_placement"]
        self.assertEqual("unavailable", cloud["status"])
        self.assertIn("not runtime isolation", cloud["notes"])
        self.assertNotIn("use local git worktrees", cloud["notes"])
        for stem in self.ISOLATION_DEPENDENT:
            with self.subTest(stem):
                self.assertEqual("prerequisite", self.playbooks[stem]["mapping"])
        read_thread = self.mechanisms["read_thread"]
        self.assertIn("summaries", read_thread["notes"])
        self.assertIn("not", read_thread["notes"])
        eval_transcripts = [d for d in self.playbooks["eval"]["dependencies"] if "transcript" in d["facility"].lower()]
        self.assertTrue(eval_transcripts)
        self.assertEqual("prerequisite", eval_transcripts[0]["status"])
        skill_creator = self.mechanisms["codex_skill_creator"]
        self.assertEqual("native-mapped", skill_creator["status"])
        self.assertEqual("pending", skill_creator["live_proof"])
        self.assertIsNone(skill_creator["evidence"])
        grok_bot = self.mechanisms["grok_bot_mcp"]
        self.assertEqual("unavailable", grok_bot["status"])
        self.assertEqual("not-applicable", grok_bot["live_proof"])
        self.assertEqual("live-tested", self.mechanisms["grok_bot_app"]["live_proof"])
        self.assertEqual("pending", self.mechanisms["grok_bot_sender"]["live_proof"])
        self.assertNotIn("delivery verified", grok_bot["notes"])
        self.assertNotIn("RRULE:", self.text)

    def test_native_workflow_doc_names_the_real_mechanisms_and_the_checker(self):
        text = NATIVE_DOC.read_text()
        for needle in ("create_goal", "update_goal", "automation_update", "kind: heartbeat", "scripts/check_plan.mjs", "--policy", "--lanes-model",
                       "workflow-capabilities.json", "live proof pending", "own cloud VM", "not runtime isolation", "verified by the parent",
                       "event bridge", "summaries", "leaves the goal active", "confirmed stop", "quiet", "never appears in a plan"):
            self.assertIn(needle, text)
        for absent in ("—", "–", "each live lane in its own git worktree", "dispatch a replacement in the same tick",
                       "read its thread with `read_thread`", "heartbeat as fallback"):
            self.assertNotIn(absent, text)


if __name__ == "__main__":
    unittest.main()
