# Verification record

Date: 2026-09-18. This record separates source preservation, deterministic tests, live provider execution and host behavior. It is not a claim that every Cursor feature or every playbook has been exercised end-to-end.

## Preserved source

The build verifies the pinned upstream and companion hashes before generating output. It retains 47 registered pstack skills, 23 principle leaves, 23 playbooks, both agent-role definitions, three companion skills and the three dormant Benny skills. Tests recover the original bodies after removing only documented metadata/host notices, verify the complete catalog, reject changed/unpinned inputs, and compare relocated/repeated builds. The installed package has separate byte/mode drift checks.

The Codex plugin validator passes. During packaging it caught missing skill interface objects; the metadata generator was corrected without changing the original skill bodies.

## Automated tests

- **57 Python tests passed:** source preservation and reproducibility, mode lifecycle/isolation, Claude/Grok protocol handling, real fake-subprocess execution, attempt reuse, malformed streams, response-model evidence, permission/profile mismatches and cancellation.
- **52 unchanged upstream Bun tests passed:** orchestrator store/CLI and PR watcher policies/readers/CLI, with 206 expectations.
- Provider-fake tests are explicitly synthetic. CI does not call paid model providers or perform deployments.

Independent review found and reproduced a process-group bug: the first implementation returned when the leader exited while a TERM-ignoring descendant kept writing. The repair waits for the owned group, escalates to KILL if required, and rejects an apparently successful leader result when descendant cleanup was needed. Regression checks observed the heartbeat stop in both timeout and normal-leader-exit cases.

Two other regressions rejected an unknown exposed tool and an actual forbidden tool call in an analysis profile. Mode review caught split state storage between hooks and ordinary CLI calls, false activation on quoted/indented commands, and missed multiline opt-out. Those cases now have passing tests.

## Live Claude Code

All tested roles requested `claude-fable-5-1` and `xhigh`. The CLI stream identified that model on substantive assistant messages and reported firstParty usage. Effort was requested explicitly; the receipt is not an independent measurement of internal reasoning compute.

1. **Analysis:** a tool-free request returned the exact expected arithmetic response. The init reported `dontAsk`, empty tools and empty MCP servers. Transport and model checks passed.
2. **Writer:** a disposable Python utility mishandled tabs, newlines and long runs of spaces. Fable used the scoped writer profile, reproduced two failures, edited only the permitted function, and reran the three existing tests successfully. The parent independently reran all tests and verified that the test file was unchanged. The receipt recorded Read/Edit/Bash calls and no permission denial.
3. **Nested explanation:** an Astra coordinator received a read-only TypeScript question through the generated poteto-mode workflow, selected Investigation, loaded the packaged `how` instructions and dispatched its configured Fable explainer through the real adapter. Eight direct boundary checks passed; before/after source hashes matched.
4. **Complete local bug-fix route:** a fresh Python project went through poteto-mode's Bug fix playbook, `how`, the separate `why` investigation/synthesis roles, Fable implementation, same-surface verification and comment review. Five Fable role receipts passed. The parent independently confirmed all three tests pass and the test file is unchanged. Commit/PR stages were explicitly excluded by the task's local-only instructions.

Captured analysis/writer streams were reparsed with the stricter final profile checks. Raw prompts, transcripts, account metadata and machine paths stay local; [sanitized evidence](../evidence/verification.json) is included in the repository.

A separate tool-free final-review attempt returned text resembling unexecuted tool requests. Its transport succeeded, but the parent did **not** accept that as a completed review. A reader-capable review then exercised actual file-reading tools but reached its eight-minute limit without a verdict; its receipt correctly reports timeout and incomplete delivery. Neither attempt is counted as review approval. The independent native code reviews and regression checks above supplied the completed review evidence. This is why delivery success, task acceptance and time limits remain separate.

## Actual Codex mode lifecycle

The two plugin hooks were installed through the personal marketplace and trusted using Codex's normal `/hooks` review UI, following explicit user approval. No hook-trust bypass flag was used.

A real Codex CLI 0.154.0 conversation demonstrated:

- Explicit activation persisted state before the model's first answer, without model tool calls.
- A resumed turn restored the full router and host contract, then added a short continuation reminder.
- `exit poteto-mode` followed by another instruction disabled the mode.
- After reactivation and an actual `/compact`, the subsequent model request received restored router/host context. This was observed in the exact test conversation's transcript, not inferred from a model claiming to remember.
- `new task` cleared the selected playbook while preserving active mode.

Unit tests additionally cover independent conversations/projects, unsupported/malformed input, command examples that must not activate the mode, and consistent state storage across hook and shell environments. Desktop availability is installed, but the directly observed lifecycle proof is the local CLI; new desktop tasks must load the installed plugin normally.

## Grok Build

Grok Build 1.0.34 was installed and listed `grok-4.6` and `grok-4.5`. The protected synthetic launch failed before inference because the read-only sandbox refused a Docker socket symlink. The adapter retained that failure, saved a `process_failed` receipt, and claimed neither a response model nor successful inference. No sandbox was disabled to make the test pass.

The analysis adapter remains an optional candidate requiring a successful local probe. Reader/writer profiles are explicitly unsupported. Stream-success fixtures are synthetic, not records of a live Grok result. See [Grok details](grok.md).

## Remaining limits

- No matched, side-by-side Cursor execution baseline was run. Current claims are source-contract preservation plus selected real Codex flows.
- Cloud placement, Grok Bot webhooks, Benny event automations and some transcript integrations need real host adapters before use.
- The upstream plan checker still has explicit model/host assumptions. It was retained, not weakened to make alternate plans pass.
- Process groups do not contain deliberately escaped sessions or undo external side effects. Permission allowlists and worktrees are not OS security boundaries.
- Correctness and authorization remain the coordinating agent's responsibility. A successful model receipt is not acceptance of a PR, deployment, or business decision.

## Reproduce

Run the README's deterministic checks and upstream helper suite. For live provider checks, use a disposable directory and the documented spec/profile interfaces. Live runs use the operator's own CLI authentication and may consume that provider's allowance. Preserve exact input revisions, inspect the real artifact, and publish sanitized evidence only.
