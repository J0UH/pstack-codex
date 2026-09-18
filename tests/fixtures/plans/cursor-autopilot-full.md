# Widget queue plan

Two independent PRs add a widget list and a widget filter to the operator dashboard. Dashboard users get a list they can narrow. The program enforces the verification rule below. PR ids in order are WQ-1, WQ-2.

## How to read this

One box is one unit of work. Every box names the evidence that checks it. A nested box is a sub-step of the box above it. Check a box only when its evidence exists, a file, a log line, a screenshot, a test run, or a SHA. The body is a how-to. The appendices explain and record.

The program runs `pstack/skills/poteto-mode/playbooks/autopilot-full.md`. The owner merges WQ-1. WQ-2 is the operator's item and stops at merge-ready.

Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

## Program checklist

### Arm the program

- [ ] State the protocol and this plan to the operator, then stop. Start execution only on the operator's explicit go.
- [ ] On the operator's go, arm a `/goal` with this exact text. "Run docs/widget-queue-plan.md. PR ids in order WQ-1, WQ-2. Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. The owner merges WQ-1. WQ-2 stops at merge-ready for the operator. Done when WQ-1 is merged and WQ-2 is merge-ready with a clean verdict."
- [ ] Read these from trunk at program start. Re-read them at every tick.
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-full.md`
  - [ ] `git show origin/main:pstack/skills/swarm/SKILL.md`
  - [ ] `git show origin/main:cursor-team-kit/skills/control-ui/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/opening-a-pr.md`
  - [ ] `git show origin/main:pstack/skills/show-me-your-work/SKILL.md`
- [ ] Arm the 30-minute audit tick. In a local session, a real terminal `/loop`. In a cloud root, a cloud-sleeper wake chain. Never leave the cadence to memory.
- [ ] Use this tick prompt, verbatim. "Re-read the execution playbook from trunk and the armed /goal. Audit the operation against both and fix drift in this tick. Probe every active lane and judge progress by side effects only. Stand down a stuck lane and dispatch its replacement now. Then post a status message to the operator in chat, whether or not anything changed, with the queue table of PR, owner, state, and head SHA, the verdicts since the last tick, what merged, open operator gates, and blockers."
- [ ] On the operator's hold or stand-down, send every owner a zero-writes order at once.

### Spawn owners

- [ ] Spawn one owner per PR with the full lifecycle the execution playbook names.
- [ ] Follow this dependency graph. Start dependent work only after its parent merges, or base it on the parent branch when the execution playbook stacks.
  - [ ] WQ-1 and WQ-2 are independent and first. Both branch from `main`.
- [ ] Hold the file boundaries. WQ-1 touches only `src/widgets/list/**`. WQ-2 touches only `src/widgets/filter/**`.
- [ ] Hold the review gate. WQ-2 changes an interaction. It waits for the operator's review in chat with screenshots and a video before merge.

### PR mechanics, for every PR

- [ ] Resolve the forge once. Default to `gh`; if `command -v origin` succeeds and Origin can resolve the repository, use `origin pr` for every PR operation. Record any fallback to `gh`. Never require `gt`.
- [ ] Open the PR ready, never draft, with `origin pr create --status open --base main` or `gh pr create --base main` according to the resolved forge. A stack child targets its parent branch.
- [ ] Run the repo's lint and typecheck once before the PR-facing push. Push with hooks on.
- [ ] Run `/deslop` before each commit and `/no-comments` before review.
- [ ] Triage every Bugbot and security-reviewer comment per `../references/bugbot-triage.md`.
- [ ] Rebase onto current trunk before babysit and again before the merge-ready report.

### Verdict and merge, for every PR

- [ ] At the merge-ready head SHA, run the swarm per `pstack/skills/swarm/SKILL.md`. One gates lane. The ten live lanes from the PR's **Verify, live** block. The perf lane from its **Verify, perf** block. One audit lane that reads the diff and the receipts and distrusts the PR body.
- [ ] Clean only when every lane is `PASS`. Findings go back to the owner. A new head gets a fresh swarm and a fresh verdict.
- [ ] The owner squash-merges WQ-1 on a clean verdict at a trunk-current head. WQ-2 stops at merge-ready. A changed patch-id after rebase voids the verdict per `playbooks/shipping.md`.

### Boot recipe, for every live lane

Each live lane runs on its own cloud VM at the PR head. Drive through `control-ui` or `control-cli` from `cursor-team-kit`.

- [ ] `git fetch origin <head-branch> && git checkout <head SHA>`.
- [ ] Start the dashboard backend with `npm run dev` and wait for `ready on :3000` in the log.
- [ ] Deliver input only through the control skill's commands. Read-only diagnostics are the server log and the browser console.
- [ ] Save every screenshot to `/tmp/swarm-<pr-id>/worker-<n>/<slug>.png` and return the paths with the report.

## Add the widget list (WQ-1)

**Depends on.** None.

**Files.**

- [ ] Create `src/widgets/list/WidgetList.tsx`.
- [ ] Edit `src/dashboard/Dashboard.tsx`.

**Build.**

- [ ] Add `WidgetList` in `src/widgets/list/WidgetList.tsx` and mount it in `Dashboard`.

**You see.**

- [ ] The dashboard renders one row per widget and logs `widgets: loaded 12`.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `src/widgets/list/WidgetList.test.tsx` gains a render case for twelve widgets. Run `npm test -- WidgetList`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.6-fast-xhigh` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Run the dashboard load with twelve widgets at trunk and head. If trunk lacks the feature, record that and gate the rendered list plus the loaded log line. Save `wq1-regression.png`. Pass when head shows twelve rows and trunk shows the recorded state.
- [ ] Lane 2. Load with zero widgets. Save `wq1-empty.png`. Pass when the empty state text is visible.
- [ ] Lane 3. Load with one widget. Save `wq1-one.png`. Pass when exactly one row renders.
- [ ] Lane 4. Load with two hundred widgets. Save `wq1-many.png`. Pass when the list scrolls and the last row is reachable.
- [ ] Lane 5. Reload the page after load. Save `wq1-reload.png`. Pass when the same rows render after reload.
- [ ] Lane 6. Resize the window to 800 pixels wide. Save `wq1-narrow.png`. Pass when no row overflows the viewport.
- [ ] Lane 7. Load with a widget whose name is 120 characters. Save `wq1-long-name.png`. Pass when the name truncates with an ellipsis.
- [ ] Lane 8. Load while the widgets endpoint returns 500. Save `wq1-error.png`. Pass when the error banner shows and no row renders.
- [ ] Lane 9. Load with the network throttled to slow 3G. Save `wq1-slow.png`. Pass when the loading state shows before the rows.
- [ ] Lane 10. Navigate away and back. Save `wq1-return.png`. Pass when the rows render again without a second load log line.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Time from navigation to the `widgets: loaded` log line at trunk and head with twelve widgets.
- [ ] Probe. Run `npm run probe:dashboard -- --widgets 12` five times at trunk and at head, interleaved. Both sides must produce the metric.
- [ ] Baseline. Record the trunk median first.
- [ ] Rule. Head median must not exceed the trunk median by more than 50 ms.

**Review gate.** None. WQ-1 is not review-gated.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The owner squash-merges WQ-1 through the resolved forge.

## Add the widget filter (WQ-2)

**Depends on.** WQ-1.

**Files.**

- [ ] Create `src/widgets/filter/WidgetFilter.tsx`.
- [ ] Edit `src/widgets/list/WidgetList.tsx`.

**Build.**

- [ ] Add `WidgetFilter` in `src/widgets/filter/WidgetFilter.tsx` and pass its query to `WidgetList`.

**You see.**

- [ ] Typing in the filter box narrows the rows and logs `widgets: filtered 3 of 12`.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `src/widgets/filter/WidgetFilter.test.tsx` gains a case that narrows twelve widgets to three. Run `npm test -- WidgetFilter`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.6-fast-xhigh` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Run the twelve-widget load and type a query at trunk and head. If trunk lacks the feature, record that and gate the narrowed rows plus the filtered log line. Save `wq2-regression.png`. Pass when head shows three rows and trunk shows the recorded state.
- [ ] Lane 2. Type a query that matches nothing. Save `wq2-none.png`. Pass when the no-match text is visible.
- [ ] Lane 3. Clear the query. Save `wq2-clear.png`. Pass when all twelve rows return.
- [ ] Lane 4. Type one character at a time. Save `wq2-typing.png`. Pass when the rows narrow after the 250 ms debounce.
- [ ] Lane 5. Paste a 100 character query. Save `wq2-paste.png`. Pass when the box accepts it and no row renders.
- [ ] Lane 6. Press Escape in the box. Save `wq2-escape.png`. Pass when the query clears and all rows return.
- [ ] Lane 7. Filter with two hundred widgets loaded. Save `wq2-many.png`. Pass when the rows narrow within one second.
- [ ] Lane 8. Filter while the widgets endpoint returns 500. Save `wq2-error.png`. Pass when the error banner stays and the box is disabled.
- [ ] Lane 9. Reload with a query in the URL. Save `wq2-url.png`. Pass when the rows load already narrowed.
- [ ] Lane 10. Use the box with the keyboard only. Save `wq2-keyboard.png`. Pass when focus order reaches the box and the rows.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Time from the last keystroke to the `widgets: filtered` log line at trunk and head. Trunk lacks the filter, so also measure the twelve-widget load end state the user waits for.
- [ ] Probe. Run `npm run probe:filter -- --widgets 12 --query wid` five times at head, interleaved with `npm run probe:dashboard -- --widgets 12` at trunk. Both sides must produce the load metric.
- [ ] Baseline. Record the trunk load median first.
- [ ] Rule. Head load median must not exceed the trunk load median by more than 50 ms, and the filter budget is 300 ms from keystroke to log line.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 2 screenshots into `docs/media/wq2-review-filter.png`.
- [ ] Record a 30 to 60 second video of the change on a lane VM. Save it as `docs/media/wq2-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at merge-ready. Wait for the operator's click.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] WQ-2 stops at merge-ready and waits for the operator's click.

## Close the program

- [ ] Every box above is checked with its evidence.
- [ ] Reply to the operator with the report the execution playbook names.

## Appendix A. Prototype evidence

The filter debounce question was settled on branch `proto/widget-filter` at SHA `1a2b3c4` with `proto-filter-250ms.png` and `proto-filter-0ms.png`. The 250 ms debounce stays. The list virtualization question stays unproven.

## Appendix B. Alternatives rejected

A single PR for list and filter lost because the filter's interaction needs its own review gate.

## Appendix C. Risks

WQ-2 depends on the list's row ids staying stable. The owner watches the id source in `WidgetList.tsx`.

## Appendix D. Links and reading list

Read `pstack/skills/how/SKILL.md` before editing the dashboard. WQ-2 gets `pstack/skills/interrogate/SKILL.md`. The trail per `pstack/skills/show-me-your-work/SKILL.md`.
