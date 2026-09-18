# Verification record

Date: 2026-09-18. This record separates source preservation, deterministic tests, live provider execution and host behavior. It is not a claim that every Cursor feature or every playbook has been exercised end-to-end.

## Preserved source

The build verifies the pinned upstream and companion hashes before generating output. It retains 47 registered pstack skills, 23 principle leaves, 23 playbooks, both agent-role definitions, three companion skills and the three dormant Benny skills. Tests recover the original bodies after removing only documented metadata/host notices, verify the complete catalog, reject changed/unpinned inputs, and compare relocated/repeated builds. The installed package has separate byte/mode drift checks.

The Codex plugin validator passes. During packaging it caught missing skill interface objects; the metadata generator was corrected without changing the original skill bodies.

## Automated tests

- **87 Python tests passed:** source preservation and reproducibility, model-policy validation, mode lifecycle/identity/isolation, Claude/Grok protocol handling, real fake-subprocess execution, attempt reuse, malformed streams, response-model evidence, permission/profile mismatches and cancellation.
- **52 unchanged upstream Bun tests passed:** orchestrator store/CLI and PR watcher policies/readers/CLI, with 206 expectations.
- Provider-fake tests are explicitly synthetic. CI does not call paid model providers or perform deployments.

Independent review found and reproduced a process-group bug: the first implementation returned when the leader exited while a TERM-ignoring descendant kept writing. The repair waits for the owned group, escalates to KILL if required, and rejects an apparently successful leader result when descendant cleanup was needed. Regression checks observed the heartbeat stop in both timeout and normal-leader-exit cases.

Real OS-signal tests now send SIGTERM, SIGINT and SIGHUP to a wrapper running the common launcher with a fake child that ignores TERM. They verify exit code 130, matching public and durable `interrupted` receipts, confirmed process-group termination and stopped heartbeats. Additional interruption cases cover the exclusive directory claim, prompt hashing, output-file creation, the window after Popen starts a child but before returning ownership, process-record writing and receipt writing. Interrupted attempts remain exclusively claimed. These are real subprocess/lifecycle tests with synthetic workers, not interruptions of live provider sessions. SIGKILL, process crashes, indefinitely blocked calls and unavailable artifact storage remain outside the orderly-receipt guarantee.

Two other regressions rejected an unknown exposed tool and an actual forbidden tool call in an analysis profile. Mode review caught split state storage between hooks and ordinary CLI calls, false activation on quoted/indented commands, and missed multiline opt-out. Those cases now have passing tests.

## Live Claude Code

The initial task roles below requested `claude-fable-5-1` at `xhigh`. The subsequent focused repair probes requested the same model at `low`; they test capabilities, not equivalence of reasoning quality. Both sets identify that exact model on substantive assistant messages and report firstParty usage. Auxiliary Haiku usage is recorded separately. Effort is an explicit request, not an independent measurement of internal reasoning compute.

1. **Analysis:** a tool-free request returned the exact expected arithmetic response. The init reported `dontAsk`, empty tools and empty MCP servers. Transport and model checks passed.
2. **Writer:** a disposable Python utility mishandled tabs, newlines and long runs of spaces. Fable used the scoped writer profile, reproduced two failures, edited only the permitted function, and reran the three existing tests successfully. The parent independently reran all tests and verified that the test file was unchanged. The receipt recorded Read/Edit/Bash calls and no permission denial.
3. **Nested explanation:** an Astra coordinator received a read-only TypeScript question through the generated poteto-mode workflow, selected Investigation, loaded the packaged `how` instructions and dispatched its configured Fable explainer through the real adapter. Eight direct boundary checks passed; before/after source hashes matched.
4. **Local bug-fix handoffs:** a fresh Python project went through poteto-mode's Bug fix playbook, `how`, separate `why` investigation/synthesis roles, Fable implementation, same-surface verification and comment review. Five Fable role receipts passed. The investigator analyzed a coordinator-gathered source-control packet; it did not execute its own git/gh queries, so this was not proof of the complete autonomous `why` search contract. The parent independently confirmed all three tests pass and the test file is unchanged. Commit/PR stages were explicitly excluded by the task's local-only instructions.

Captured analysis/writer streams were reparsed with the stricter final profile checks. Raw prompts, transcripts, account metadata and machine paths stay local; [sanitized evidence](../evidence/verification.json) is included in the repository.

Three later functional probes completed at `low`:

- **Reader shell:** the reader ran one explicitly allowed local `git log` command and returned the fixture's actual commit subject. The observed tools were Read/Glob/Grep/Bash, with no Write/Edit, no permission denial and a verified response model. This proves that scoped local Git query; it does not prove authenticated `gh` or a complete autonomous seven-source `why` investigation.
- **Writer boundary:** the writer changed the permitted workspace file. A Write attempt outside that workspace was denied under `dontAsk`; an independent check confirmed the outside file stayed unchanged. The receipt reports one permission denial. This exercises the configured file-tool rule for this case, not OS containment or a guarantee against arbitrary shell commands.
- **Safe mode with positive control:** with a synthetic project instruction, SessionStart hook and MCP server present, the safe-mode analysis run produced neither sentinel file, did not include the instruction phrase, and reported zero MCP servers. A separate positive control without safe mode produced both sentinels, included the phrase and reported one MCP server. The control establishes that the test customizations were live rather than merely absent or broken.

An isolated model-setup walkthrough also reported the absent configuration, validated an explicit role mapping, saved it through an absolute test override and read it back. It made no provider call and wrote no Cursor rule.

## Fable review status

Two completed source reviews by `claude-fable-5-1` at requested `xhigh` assessed commit `7782ebe26fb150c5340c54dec4ade5f6e4825b12`. Their substantive assistant messages identified Fable; transport completed with structured verdicts. The reviewers read supplied source/evidence packets and did not execute their own tests.

- **Workflow:** `changes_required`. There was no workflow approval. The reviewer required explicit durable-wakeup limitations, working default-prompt activation, authoritative hook/CLI state identity, and a concrete Codex model-setup procedure/schema.
- **Runtime:** `approve` only for the stated limited alpha runtime scope. It excluded unproved parent-interruption behavior, autonomous investigator/verifier shell capabilities, safe-mode suppression and applied-effort claims; Grok was not approved as live-enabled.

The repair tests and probes above address reported gaps, but they do not revise those historical verdicts. **Final Fable re-review of the repaired candidate is pending. The package is not yet jointly approved.** Internal reasoning effort remains unmeasured, and the reader proof is limited to local Git.

Earlier experimental review attempts remain excluded from approval evidence: one tool-free run returned nonexecuted tool-request text, and one reader run timed out after actual reads without a verdict. They are distinct from the two completed source reviews above. Delivery success, task acceptance and approval scope remain separate.

## Actual Codex mode lifecycle

The two plugin hooks were installed through the personal marketplace and trusted using Codex's normal `/hooks` review UI, following explicit user approval. No hook-trust bypass flag was used.

A real Codex CLI 0.154.0 conversation demonstrated:

- Explicit activation persisted state before the model's first answer, without model tool calls.
- A resumed turn restored the full router and host contract, then added a short continuation reminder.
- `exit poteto-mode` followed by another instruction disabled the mode.
- After reactivation and an actual `/compact`, the subsequent model request received restored router/host context. This was observed in the exact test conversation's transcript, not inferred from a model claiming to remember.
- `new task` cleared the selected playbook while preserving active mode.

Unit tests additionally cover independent conversations/projects, unsupported/malformed input, command examples that must not activate the mode, and consistent state storage across hook and shell environments. Desktop availability is installed, but the directly observed lifecycle proof is the local CLI; new desktop tasks must load the installed plugin normally.

A later real CLI check exercised the repaired identity path. The published default prompt activated the mode. `CODEX_THREAD_ID` matched the trusted hook session identity. From a real sibling Git worktree, `mode status` and `mode select` with omitted identity flags resolved the original project; selecting Investigation produced generation 2. On resuming that conversation, two developer hook contexts in its actual transcript carried the authoritative identity and generation-2 Investigation selection. This was checked in the transcript, not inferred from the model's response.

That check first hit a real permission boundary: the default workspace-write sandbox denied the CLI's state-file write. Granting only the configured pstack state directory through the authorized `--add-dir` option allowed it to complete while retaining the workspace sandbox. The package documents that prerequisite. The proof does not claim that an ordinary workspace sandbox automatically permits writes to user-level state, and no trust bypass was used.

## Grok Build

Grok Build 1.0.34 was installed and listed `grok-4.6` and `grok-4.5`. The protected synthetic launch failed before inference because the read-only sandbox refused a Docker socket symlink. The adapter retained that failure, saved a `process_failed` receipt, and claimed neither a response model nor successful inference. No sandbox was disabled to make the test pass.

The analysis adapter remains an optional candidate requiring a successful local probe. Reader/writer profiles are explicitly unsupported. Stream-success fixtures are synthetic, not records of a live Grok result. See [Grok details](grok.md).

The repaired adapter's exact current argv was also exercised, including the empty tool list and all seven deny rules. It again reached the sandbox startup error, with no unknown-option error. Its exact-argv SHA256 is published in the sanitized evidence. This removes the earlier command-drift gap but does not prove inference, an empty runtime tool inventory, or enforcement after startup. Protections were not weakened.

## Remaining limits

- No matched, side-by-side Cursor execution baseline was run. Current claims are source-contract preservation plus selected real Codex flows.
- Cloud placement, Grok Bot webhooks, Benny event automations and some transcript integrations need real host adapters before use.
- Durable wakeups (`/loop`, `/goal`, timed audit ticks and watcher-driven wakes) are not supplied by this package. Autonomous run, Babysit drive, Shipping watch, both Autopilots and Orchestrate can do current-turn work with bounded waits but cannot promise unattended continuation without an authorized host wake adapter. Their stopping conditions are unchanged.
- The upstream plan checker still has explicit model/host assumptions. It was retained, not weakened to make alternate plans pass.
- Process groups do not contain deliberately escaped sessions or undo external side effects. Permission allowlists and worktrees are not OS security boundaries.
- A hard-killed launcher can leave an unreconciled detached child. The invoking tool must allow time for the worker timeout and termination grace; incomplete process records require ownership/effect reconciliation before retrying.
- Correctness and authorization remain the coordinating agent's responsibility. A successful model receipt is not acceptance of a PR, deployment, or business decision.

## Reproduce

Run the README's deterministic checks and upstream helper suite. For live provider checks, use a disposable directory and the documented spec/profile interfaces. Live runs use the operator's own CLI authentication and may consume that provider's allowance. Preserve exact input revisions, inspect the real artifact, and publish sanitized evidence only.
