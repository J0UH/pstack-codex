# Native Codex workflow adapter

This document maps the Cursor host facilities named in the 23 pstack playbooks onto the Codex mechanisms actually exposed to the coordinator. Its evidence is the runtime tool schema recorded on 2026-09-18 (`create_goal`, `get_goal`, `update_goal`, `automation_update`, native collaboration tools, scope-aware `read_thread`), the official long-running and scheduled-task documentation fetched the same day, the [host contract](../adapters/host.md), and the parent's recorded heartbeat evidence described below. It changes no playbook text, arms no automation, creates no goal, and builds no scheduler. Read it with the host contract before executing a wake-dependent playbook.

Every mapping below carries one of four labels.

- **Implemented mapping.** The Codex mechanism exists in the packet, the instructions are written, and any code is unit-tested. Nothing here is a claim that the mechanism has fired on a real host.
- **Verified by the parent.** The parent ran the real host action and recorded the evidence. The label names the exact scope of what was exercised and nothing beyond it.
- **Live proof pending.** The parent has not yet run the real host action. Documentation of a tool is not proof that it works as the playbook needs.
- **Unavailable.** No mechanism is exposed. The playbook route stays present and reports itself blocked instead of substituting a weaker behavior.

The machine-readable per-playbook map is [workflow-capabilities.json](workflow-capabilities.json). The parent updates its `live_proof` fields; this document explains the mapping behind them.

## Goal mode, from `/goal` to `create_goal`

Upstream arms a Cursor `/goal` on the operator's explicit go (Autopilot-full step 1, Autopilot-stack step 3, the Multi-phase plan program checklist) and re-reads it at every audit tick. Codex exposes `create_goal({objective, token_budget?})`, `get_goal({})`, and `update_goal({status: complete|blocked})`.

- **Create only on an explicit request for a goal.** Two requests qualify. The user asks for a goal in so many words, or the operator gives the explicit go on a reviewed plan whose checklist expressly says to call `create_goal`, as the Codex plan skeleton does. Nothing else qualifies. "Run until done", "keep going until X", and "/loop until X" ask for continuation to a predicate; they authorize the work and, where the user asked for unattended continuation, the heartbeat, but they do not authorize creating a goal silently. Without a goal the predicate lives in the heartbeat prompt, the decision trail, and the resume note. When a goal is created, say so in the reply. Never infer a goal from an ordinary task, however long it runs, and never create one during setup or install. Hillclimb's unattended form borrows only the wake, not a new rule.
- **Existing goal and budget.** Read `get_goal` before creating a goal. Continue a matching active goal; do not falsely complete or overwrite a different unfinished goal to clear the slot. Set a token budget only when the user explicitly requested one.
- **Objective content.** Use the plan's exact text where the playbook names it: plan path or predicate, PR ids in order, the verification rule, who merges, and the done condition. The objective is what every tick audits against.
- **Re-read.** `get_goal` at each tick replaces "re-read the armed /goal".
- **Hold and pause leave the goal active.** An operator hold is a zero-writes order: stop every writer and pause the heartbeat. It leaves the goal active. Pause safely does the same and records the goal in the resume note. Never call `update_goal` because the program paused; a pause is neither completion nor a blocker. The plan checker rejects a hold box that closes the goal.
- **Complete only on the verified predicate.** Call `update_goal({status: complete})` only after the done condition is confirmed on the real artifact. Mark `blocked` only after the same genuine blocker recurs on three consecutive goal turns. A first blocker is reported in the status message and worked around.
- **Operator command.** `/goal` typed by the operator in the desktop app, CLI, or IDE is the operator's own action, and the desktop app's pause, resume, edit, and clear controls belong to them. The agent uses the tool, never a fabricated slash command.

Status: implemented mapping, live proof pending. The parent creates a goal on an explicit request, reads it back, completes it on a verified predicate, and records the result.

## Wake mechanism, from `/loop` to the native heartbeat

Upstream `/loop` covers three uses: a fixed-interval or event-driven re-check in Autonomous run, the 30-minute audit tick in both Autopilots and the plan checklist, and the watcher-driven rearm in Babysit, Shipping, and Orchestrate. Codex exposes `automation_update` with `mode: create`, `kind: heartbeat`, `name`, `prompt`, `rrule`, `status: ACTIVE|PAUSED`, and `destination: thread` with an optional `targetThreadId`; `mode: view` with an `id`; and updates that must view the existing automation first and preserve its full field set. Existing automations are visible under `$CODEX_HOME/automations/*/automation.toml`.

Arm a heartbeat only when the user requested scheduling or unattended continuation, or when the invoked playbook step permits it under an authorization the user already gave. Never arm one during install, never because a playbook merely mentions a monitor, and never as a substitute for current-turn work that a bounded wait can finish. Attach it to the current thread. Do not replace it with a standalone scheduled task, which starts a fresh chat without the program's context.

Before creating, list the automation directory and view any candidate. Update a matching heartbeat instead of creating a duplicate, preserving every field you did not intend to change.

The prompt is a durable, human-readable instruction set. It must state all of these.

- **Predicate.** The exact done condition, or a pointer to the goal read through `get_goal` when one exists.
- **Scope and ownership.** Which files, branches, PRs, checkouts, and stores the tick may touch, who the single writer of each is, and what it must never do (no merges without the explicit grant, no topology changes outside the root, no writes during an operator hold).
- **Stale-work checks and replacement.** Judge progress by side effects only: commits, pushes, PR and check deltas, ledger rows, receipts. A lane past its expected runtime with no side effect is stuck. Stand it down, then dispatch its replacement only after a confirmed stop and a reconciliation of its effects: the branch, the checkout, the PR, and any process record. Per the host contract, an unknown cancellation keeps its resource ownership until the predecessor is confirmed stopped, and a clean tree does not prove process exit. When the stop or the ownership is uncertain, the tick reports it and dispatches nothing; it never promises a same-tick replacement.
- **Cancellation.** What ends the loop: the predicate met, the operator's hold or stop, a real dead end, or the playbook's own stop class. On any of these the tick pauses its own heartbeat and says so.
- **Notification policy.** The default is quiet: a tick that finds nothing changed and nothing actionable posts nothing. A tick posts when something changed, when a decision or gate needs the operator, when the loop ends, or when the playbook step or the approved plan names a per-tick report. The Multi-phase plan tick prompt names one, "post a status message whether or not anything changed", and the operator approves it with the plan. Periodic updates outside such a named report need the user's explicit request. Do not apply the every-tick report to every playbook.
- **Stop behavior.** Continue or pause per the cancellation rule. Never leave the cadence to memory.

Cadence is stated in words in every plan, prompt, and reply, for example "every 30 minutes" or "the 30-minute audit tick". The `rrule` argument of `automation_update` carries the RFC 5545 encoding for the selected audit interval, and that string never appears in a plan or a prompt; it belongs to the tool call and the automation record. Minute intervals are supported for thread follow-ups. In the recorded probe, a single-occurrence rule was rejected because it had no future run. The successful test used a recurring interval and explicitly paused and deleted it after the first observed run. Always validate the actual future schedule returned by the host. Autonomous run sizes its interval to when the result is worth re-checking. Babysit and Shipping choose an interval that lets each tick run one bounded watcher pass. Visual parity's per-component loop stays inside the turn unless the user asked for unattended work.

Cleanup is part of every playbook's stopping rule. On Close the program, on Pause safely, on an operator stop, and at a genuine dead end, view the heartbeat, set `status: PAUSED` with all other fields preserved, then handle the goal per the goal rules above: close it only on the verified predicate, otherwise leave it active. A finished or paused program must never leave an `ACTIVE` heartbeat behind. Pause safely's "cancel nested subagents" includes pausing the heartbeat and recording its id in the resume note so the resumer can re-activate it deliberately.

Limits from the official documentation and the packet: scheduled work on local files needs the desktop app running and the machine awake, so turn on "Prevent sleep while running". The CLI and IDE do not provide the desktop Scheduled management interface. Use the native automation tool when it is actually exposed to the current task; otherwise report scheduling unavailable on that surface. A successful `automation_update` call is armed configuration, not proof that a wake occurred. Runs use the default sandbox and `approval_policy = "never"` where policy allows, so the prompt must stay inside the narrowest scope that lets the tick succeed.

Status: timed wake verified by the parent, playbook lifecycles live proof pending. The parent attached one `automation_update` heartbeat to a known disposable task at a one-minute interval. The scheduled new turn ran under the workspace-write sandbox and wrote the expected file carrying the exact matching `CODEX_THREAD_ID`. The parent observed the completed turn and the file, set the automation to `PAUSED`, and deleted it. That proves the native timed-wake mechanism and the thread attachment. It does not prove a goal, cloud placement, a watcher pass inside a tick, or any full unattended playbook lifecycle; each of those stays live proof pending.

## Watcher events inside the turn, timed polling across turns

The unchanged GitHub watcher `skills/poteto-mode/scripts/watch-pr/watch-pr` stays the event reader. Its stop classes are unchanged: `READY` in single or stack mode, a queued `WAITING` with reason `merge-queue`, `ADVANCE`, and `COMPLETE`; Origin's merge-ready state comes from `origin pr view`, `origin pr thread list`, and `origin pr checks --watch`.

- **Inside the active turn, the event wake is immediate.** The bare watcher command blocks until a terminal verdict within the shell tool's time limit and the turn acts on it at once. `--status-only` serves `check`. Origin's `--watch` is bounded the same way. This is the upstream event wake, preserved while the turn is active, and it is the only place the event wake exists on this host.
- **Across turns, there is no event bridge.** Nothing wakes a finished turn when the forge changes. The heartbeat is time-based polling: each tick runs one bounded watcher pass, acts on any verdict, and either continues or pauses. That is not the upstream watcher-driven wake and must not be described as event-primary with a timed fallback. The strict event-dependent gates therefore stay unresolved: Babysit `drive` and `background` across turns (step 6), Shipping's frontier watch (step 8), Orchestrate's frontier watcher wake, and Autonomous run's "watcher subagent that wakes you". Report that gap when a user asks for one of them unattended, and offer the timed polling loop as what actually exists. A native queue probe accepted a message but did not start an unloaded task. Queue acceptance is therefore not a verified event wake. The native desktop send-message tool can dispatch a known task while a coordinator is running, but this is not a persistent external event bridge.
- **Stop classes and rearm are unchanged.** Babysit stops at `READY` in single or stack mode, reports a blocker-free queued frontier as the non-terminal `WAITING` with reason `merge-queue` and stops there, continues on `ADVANCE`, and treats `COMPLETE` as terminal. Shipping step 8 ignores `READY` and reads `gh pr view` for `state`, `mergedAt`, `mergeStateStatus`, `statusCheckRollup`, and `autoMergeRequest` after each pass, waiting for `mergedAt` or `state` `MERGED`; Babysit's queued stop class does not apply there, and its hard-fail rules are unchanged. Inside a turn, rearm the watcher after every push wave and after every verdict acted on, with the same frozen bottom-to-top list. Across turns the next tick is the rearm. Never nest a sleep loop inside a tick and never start a second poller.
- **Orchestrate drains.** The frontier watcher wake has no across-turn counterpart; a heartbeat tick with the long interval the playbook allows runs `orch` bookkeeping at the drain point. That is the fallback interval only, without the event wake it was meant to back up.

Status: implemented mapping for the in-turn event wake and the timed polling loop. The [verification record](verification.md) reports the watcher's unchanged Bun tests passing; a live authenticated `gh` run inside a heartbeat tick is live proof pending. The across-turn event bridge is unavailable.

## Per-lane isolated executors, preserved as a prerequisite

The source requires real independent executors. The Multi-phase plan boot recipe puts each live lane on its own cloud VM at the PR head. Shipping step 1 runs one cloud agent verifier per PR. Both Autopilots run one cloud agent owner per PR. The swarm skill fans out cloud workers. No verified isolated cloud-worker adapter is configured for this port, and the host contract forbids mapping cloud placement onto anything but a real, configured isolated execution service.

- **The requirement stands.** A Codex plan keeps the sentence "Each live lane runs on its own cloud VM at the PR head." and states that a lane reports blocked until the operator configures an isolated runtime per lane or explicitly approves an alternative. The checker requires both statements and fails a boot recipe that places lanes in worktrees without separate port, browser, and data evidence.
- **A worktree is not runtime isolation.** Native tasks and CLI workers share the local machine. A git worktree gives one writer per checkout and nothing else. Ten lanes that each start a backend on the same host collide on the port, the browser profile, and the data directory, so a worktree-per-lane recipe on one host does not satisfy the live lanes, the Shipping verifiers, or the Autopilot owners. The previous revision of this adapter equated the two; that was wrong and is withdrawn.
- **Approved alternatives need evidence.** When the operator explicitly approves running lanes on this machine instead of cloud VMs, the boot recipe must give each lane its own port, browser profile, and data directory and record the evidence of each. The approval and the evidence are recorded in the plan; the checker verifies the plan's form only and does not certify that the runtimes exist.
- **Block before spawning.** When the executor prerequisite is unmet, Autopilot-full, Autopilot-stack, Shipping, and the Multi-phase plan's execution report blocked at the spawn or boot step and spawn nothing. The route stays present. The checker prints a reminder that the isolated runtime, the goal, and the heartbeat are host prerequisites it does not certify.
- **Other cloud behaviors.** The cloud-sleeper wake chain, cloud-agent URLs, and `cloud_base_branch` have no mechanism here. Orchestrate's "after a restart, cloud work is not dead" becomes: pushed branches, worktrees, and the plain-file store supply recovery evidence. Reconcile the actual task and process state with those records; do not infer that a native or external worker is dead merely because the coordinator restarted. Unknown cancellation retains ownership until resolved. ChatGPT web scheduled tasks and their Gmail, Slack, and GitHub event triggers run without local folder access, are web and mobile facilities, and are not exposed to this coordinator; they are not a substitute for local or isolated lanes.

Status: cloud placement unavailable, per-lane isolation a prerequisite reported blocked until configured or explicitly approved with evidence.

## Native collaboration and transcripts

Use `spawn_agent` for a bounded task, then `send_message`, follow-up, interrupt, list, and wait as the packet exposes them. Pass an explicit model only where the user, an invoked skill, or AGENTS instructions requested it; configured external roles go through the Claude and Grok worker scripts. A user-owned `create_thread` needs an explicit request. Poll native tasks with the native read-only list and wait; never resume or follow up merely to inspect status.

Identifiers do not cross. A native `spawn_agent` agent id is not an app task id or a thread id and must not be passed to `read_thread`. `read_thread` takes a known app task id from the user, the hook context, or a recorded session, and it returns recent status and turn summaries for that task. It does not guarantee a complete tool-by-tool transcript.

- **Where summaries suffice.** Session pickup reads the prior trail through `read_thread` on the known task id plus pushed branches and git, which is enough to name the resume point. Worktree cleanup's chat cross-check for known sessions works the same way, with the actual pinned set from the host's read-only task inventory when exposed. Use available pin metadata before asking the user; summaries still do not replace required full traces.
- **Where exact traces are required.** Eval step 6 grades chain-following from the files each candidate actually opened, and the show-me-your-work audit walks the log against what actually happened. Summaries are not that evidence. Those steps need the full authorized transcript of the candidate or run; for CLI worker candidates the attempt directory's private raw stream and receipts are that transcript. When no full authorized transcript exists, report the step's evidence unavailable. A separately labeled code-quality assessment does not complete the required chain-following gate. Do not present summaries as the transcript.
- **No cross-project search.** Cursor's `agent-transcripts/` globs and the Cursor dashboard have no mechanism. When the needed transcript is not a known task, report the gap rather than scanning unrelated conversations.

Status: native delegation, messaging and result collection were exercised during this port. The app task status/summary interface was exercised separately on the disposable wake task. These proofs do not establish complete tool-trace availability for every host.

## Plan checker for Codex plans

Run the Codex checker instead of the unchanged upstream checker when the plan is written for this host.

```sh
node <plugin>/scripts/check_plan.mjs <plan.md> --policy <models.json>
node <plugin>/scripts/check_plan.mjs <plan.md> --lanes-model <exact-model>
```

The policy defaults to `PSTACK_MODEL_CONFIG`, then `$CODEX_HOME/pstack/models.json`, then `~/.codex/pstack/models.json`, the same resolution as `pstack.py models path`. The ten live lanes run on the policy's `swarm workers` model, per the swarm skill. That identity is never invented: a missing policy or an inheriting role requires `--lanes-model`, and `--lanes-model` must agree with an explicit policy entry or the checker exits 2. The report prints the resolved backend, effort, and source so a reviewer sees which model the lanes bind to, and a reminder that the per-lane runtime, the goal, and the heartbeat are host prerequisites the checker does not certify.

Every upstream structural check is retained with its original message. Only evidenced host and model assumptions change.

| Upstream assumption | Codex requirement |
|---|---|
| ``Ten lanes on `grok-4.6-fast-xhigh` at the PR head`` | ``Ten lanes on `<swarm workers model>` at the PR head`` from the explicit policy or `--lanes-model` |
| Arm a `/goal` on the operator's go | Call `create_goal` on the operator's explicit go on this plan, which names the goal; the hold box pauses the heartbeat (`PAUSED`) and may not call `update_goal` |
| `git show origin/main:pstack/...` at every tick | Re-read from the pinned installed package, `<plugin>/skills/poteto-mode/playbooks/<stem>.md`; the named playbook must exist in the package |
| 30-minute terminal `/loop`, or a cloud-sleeper chain | 30-minute `automation_update` heartbeat attached to the thread with `kind: heartbeat`, the cadence stated in words; a raw `RRULE:` string anywhere in the plan fails |
| Each live lane on its own cloud VM | Unchanged, plus the statement that a lane reports blocked until its isolated runtime is configured or an alternative is explicitly approved; a lane placed in a worktree needs separate port, browser, and data evidence |
| Stand a stuck lane down and dispatch a replacement at once | Confirm the stop and reconcile its effects before dispatching; the checklist must say `reconcile` |
| Close the program has no cleanup | Close the program pauses the heartbeat (`PAUSED`) and calls `update_goal` on the verified done condition |

The checker flags a Codex plan that still says `/loop`, `cloud-sleeper`, or `git show origin/main:pstack/` in its program checklist. Do not run the upstream checker on a Codex plan and then rewrite the plan's markers to satisfy it; the upstream file stays byte-identical and still describes the Cursor host. Tests in `tests/test_check_plan.py` prove that each retained gate fails when its content is removed, that the Cursor-form fixture passes upstream and fails here only for host reasons, that the Codex fixture fails upstream rather than being forged for it, that a worktree-only boot recipe fails, that a raw schedule string fails, and that a hold box closing the goal fails.

## Unresolved integrations awaiting capability evidence

- **Across-turn event bridge.** Unavailable. The parent is testing a native queue mechanism as a possible bridge; nothing is claimed until that test is recorded.
- **Grok Bot and Make Bot UI.** The [optional Bot adapter](grok-bot.md) supplies app-handoff guidance and the outbound sender. Routine management, secure secret entry and wake handling remain Bot-app facilities. A real sender key, accepted live probe, webhook delivery and routine-side queue drain remain unverified.
- **Slack and Benny.** No Slack tool or new-message event trigger is exposed to this coordinator. A time-based heartbeat is not a new-message trigger, so the dormant Benny pack stays dormant with its committed-file and fresh-project requirements intact.
- **Grok Build inference.** Analysis and reader profiles are verified on the exact tested Linux 1.0.34 build. Non-Linux dispatch is refused before the CLI starts. Writer, Bash and other versions remain unsupported. Unsupported roles report blocked rather than substituting another model. See [Grok status](grok.md).
- **Native skill creator.** The native Codex skill creator is available on the host, so Authoring a skill and automate-me have their authoring facility. Its draft, test, and iterate flow has not been exercised end to end here; live proof pending, and no proof is claimed.
- **Cloud placement.** Unavailable, as above; per-lane isolation stays a prerequisite.

## Playbook summary

Column meanings: host mapping covers the Codex facilities the playbook needs; unattended covers continuation after the turn; proof is the current live status the parent updates. Stopping rules, evidence predicates, owner boundaries, and user authority are unchanged from the source files and summarized in the JSON map.

| Playbook | Host mapping | Unattended | Proof |
|---|---|---|---|
| Investigation | Native or CLI how/why workers, unslop | Not applicable | Live-tested explainer route |
| Bug fix | Control skill, how/why, configured worker, tdd, Opening a PR | Heartbeat for a stubborn hunt on request | Live-tested locally without the PR stage |
| Perf issue | Control skill traces, how, configured worker | Not applicable | Pending |
| Hillclimb | Frozen harness, decision log, configured worker, worktrees | Heartbeat borrowed from Autonomous run | Pending |
| Runtime forensics | Control skill, live instrumentation, bulk parsing in a worker | Not applicable | Pending |
| Trace forensics | Parsers and sqlite, bulk parsing in a worker | Not applicable | Pending |
| Feature | how, architect and arena panels, configured worker, control skill | Not applicable | Pending |
| Refactoring | how, pin harness, configured worker | Not applicable | Pending |
| Prototype | Scratch directory, control skill screenshots | Not applicable | Pending |
| Visual parity | Image diff harness, per-component worktrees | Heartbeat only on request | Pending |
| Authoring a skill | Native Codex skill creator, project `.agents/skills/` | Not applicable | Pending, flow not exercised |
| Eval | Sanitized worktrees, panel candidates, full authorized transcripts or CLI raw streams | Not applicable | Prerequisite, pending |
| Babysit | Bounded watcher in the turn, forge CLI | Timed polling only; event wake across turns unavailable | Pending |
| Shipping | Independent verifiers on isolated runtimes, patch-id, watcher | Timed polling only; event wake across turns unavailable | Prerequisite, pending |
| Autonomous run | Goal only on an explicit goal request, heartbeat, decision trail | Heartbeat; an event to watch is polled | Pending |
| Orchestrate | `orch` store, native workers, heartbeat drains | Timed drains only | Prerequisite for Graphite frontier and isolated workers, pending |
| Autopilot-full | Goal, 30-minute heartbeat, isolated owners, swarm verdicts | Heartbeat | Prerequisite for isolated executors, pending |
| Autopilot-stack | Goal, 30-minute heartbeat, isolated owners, root-only topology | Heartbeat | Prerequisite for isolated executors, pending |
| Session pickup | `read_thread` summaries for a known task id, pushed branches | Not applicable | Pending |
| Pause safely | WIP commit, resume note, heartbeat pause, goal left active | Not applicable | Pending |
| Multi-phase plan | Prototype, explorers, `scripts/check_plan.mjs` | Not applicable | Checker unit-tested, plan run pending |
| Worktree cleanup | Audit script, `read_thread` for known sessions, user pinned set | Not applicable | Prerequisite, pending |
| Opening a PR | Worktree, companions, forge CLI | Not applicable | Pending |

## Proof ledger for the parent

The parent records these live actions in the JSON map. Each stays labeled live proof pending until recorded.

1. Create a goal on an explicit request for a goal, read it with `get_goal`, complete it with `update_goal` on a verified predicate. Pending.
2. Create one bounded harmless heartbeat attached to a known task with a minute interval, observe a scheduled turn and its effect, pause it, and confirm the paused state. Verified by the parent on 2026-09-18 for one harmless local file operation with the matching `CODEX_THREAD_ID`; the automation was then paused and deleted. Scope: the timed wake and thread attachment only.
3. Recorded. Native delegation/message/result collection and, separately, app `read_thread` status/summary retrieval on a known test task were exercised. These do not prove full tool traces, and a native agent id is never passed as an app task id.
4. Run the watcher once with an authenticated `gh` inside a heartbeat tick and record its stop class. Pending.
5. Run one Codex plan through `scripts/check_plan.mjs` against the real model policy and post its output as Multi-phase plan step 7 requires. Pending.
6. Recorded negative outcome. The queue command accepted a message but did not wake the unloaded task. This is evidence of a tested limitation, not a verified event bridge; see the queue record in integration-verification.json.
7. Record whether an isolated runtime per lane is configured, or the operator's explicit approval of an alternative with its per-lane port, browser, and data evidence. Pending; until then the executor-dependent playbooks report blocked at spawn.
