# Integration candidate review and remaining work

Two independent Fable 5.1 source reviews returned **approve** for candidate [`ad93276dcf57`](https://github.com/J0UH/pstack-codex/commit/ad93276dcf570e68832af469abce7066b2d6edc3), each within an explicit scope. The first covered workflow fidelity, mode and model policy, the plan checker and worker runtime. The second covered the optional Grok Bot sender and readiness doctor. Their approval does **not** cover subsequent executable changes or a claim of complete Cursor parity.

Both reviewers received original pstack instructions, candidate source and tests, and the coordinator's observed evidence. They used standalone Claude CLI with requested `xhigh`; substantive assistant messages identified `claude-fable-5-1`. They inspected the supplied source but did not run tests. Internal reasoning compute was not measured. [Complete sanitized verdicts and execution evidence](../evidence/integration-review.json).

## Follow-up fixes

The coordinator addressed all twelve recorded follow-ups and added regression coverage. These include single-rule shell permission validation, fenced schedule detection, mode identity/home/quotation edge cases, finite payload values, secret redaction before JSON escaping, key/queue alias protection, queue directory checks and non-echoing argument errors. Those repairs passed 216 Python tests. The latest Grok implementation brings the complete suite to **223 passing Python tests** with the standard JSON Schema validator. The **52 unchanged upstream Bun tests** passed in the earlier integration pass.

These repairs still need a Fable delta review. The next requested Fable implementation run—for the separately verified Grok controls—stopped at the Claude session limit before any tools or edits. No implementation or approval is attributed to that failed run. The provider reported a reset at 1:30 a.m. Copenhagen time after the September 18 evening attempt.

The resumed Poteto run verified the three external companions in the installed package and corrected two historical audit passages. It applied the bundled comment-review and deslop workflows, removed redundant comments, represented a skipped launch separately from a spawn failure, and removed an unreachable secret guard. All 216 tests passed at that cleanup checkpoint. These changes also await the final Fable review. A fresh Fable retry confirmed the same session limit before any edits.

The user then authorized Astra to implement the remaining Grok adapter and retain Fable for later review. Astra completed Linux analysis and file-reader support. The production CLI passed both real acceptance calls, including actual file tools, unchanged project files, exact model identity, stdin hashes and cleanup. An actual older CLI was refused before prompt dispatch. Writer remains deliberately unsupported because inherited grants and managed sandbox settings prevent the claimed portable write boundary. [Acceptance evidence](../evidence/grok-adapter-acceptance.json).

## Before the next release

1. Have Fable review the exact new commit, including the follow-up fixes, Grok implementation and real acceptance evidence. Address findings and bind the verdict to that commit.
2. Publish the approved candidate after distribution validation and CI pass, then verify that exact installed package. The local development marketplace can refresh its cache during packaging without an explicit reinstall. A local development installation is not evidence of final approval or a published release.

## Optional capabilities and external prerequisites

Grok Bot is optional for work that benefits from its persistent cloud computer or Bot-native routines. Its public-page screenshot and paused-routine creation were observed. No real webhook key was obtained or event delivered. The sender's tests use synthetic keys and injected/loopback HTTP; a live harmless probe and a routine-accessible failure queue are prerequisites for the complete webhook workflow.

Native timed wake, cleanup and mode activation/resume/exit have live evidence. A durable external event bridge and isolated cloud execution remain separate prerequisites. Worktrees do not provide independent runtime isolation, and a timed heartbeat is polling. Not every playbook was run end to end; no matched Cursor runtime baseline was tested. See the [capability map](workflow-capabilities.json) and [verification record](verification.md).

## Scheduled review follow-up

Fable approved code commit `a3b0735` within its stated scope and reported one P2 evidence-maintenance item and six P3 follow-ups. The [checkpoint verdict](../evidence/prepublication-fable-review.json) is preserved. The coordinator addressed the findings together, verified that the original source hashes were correct, repeated actual Grok analysis/reader acceptance on the updated source, and passed 229 Python tests. The [follow-up record](../evidence/fable-followup-verification.json) distinguishes fixes from an already-safe non-object error path. The resulting source still requires its exact-commit follow-up verdict before release.
