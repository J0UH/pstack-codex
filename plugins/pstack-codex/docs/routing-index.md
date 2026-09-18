# Generated pstack routing index

Source: pstack 0.15.2, `5bf2b1544db739998121a306340631963c2ff3de`. Regenerate with `python3 scripts/build.py`.

Read [the host contract](../adapters/host.md). The 47 skills comprise 24 workflow/utility skills and 23 principles. They are not all invoked on every task. `poteto-mode` retains its automatic routing while active; explicit-only discovery does not forbid its explicit calls to another packaged skill.

Resolve named pstack skills to the paths below before considering similarly named skills outside this package. The original descriptions and routing bodies remain in those files. TypeScript `.ts`/`.tsx` path activation is implemented by the host contract, not unsupported frontmatter.

| Skill | Discovery | Source trigger |
|---|---|---|
| [architect](../skills/architect/SKILL.md) | explicit only | Sketch types, signatures, and module structure before code, then stay in the loop while implementation fills in. Use for /architect, 'architect this', 'design this', or non-trivial work where jumping to code would lock in the wrong shape. |
| [arena](../skills/arena/SKILL.md) | explicit only | Spawn N parallel candidates at the same task, pick a base, graft the strongest parts of the losers into it. Use for /arena, 'arena this', 'throw it in the arena', or when one attempt at a non-trivial artifact would lock in the wrong shape. |
| [automate-me](../skills/automate-me/SKILL.md) | explicit only | Use for "automate me", "create/update/refresh my -mode skill", "turn/capture my preferences or working style into a skill", or wanting agents to follow how the user works. Drafts or revises a personal -mode skill via create-skill + unslop, optionally pulling fresh evidence from recent transcripts. |
| [blast-radius](../skills/blast-radius/SKILL.md) | explicit only | Find what a change could break somewhere else before it ships, beyond the diff, and prove the one fact it's safe because of by running real code instead of writing it up. Use for 'blast radius of X', 'what could this break', or reviewing a small diff you don't trust. |
| [bro](../skills/bro/SKILL.md) | explicit only | Restate the last message in plain human language, with no jargon. |
| [create-verification-skill](../skills/create-verification-skill/SKILL.md) | explicit only | Generate a project-local verification skill that drives your app the way a user does — any language, framework, or platform. Use for /create-verification-skill, "make a control skill for this repo", or when a project has no scripted way to prove UI/CLI/service behavior. |
| [figure-it-out](../skills/figure-it-out/SKILL.md) | explicit only | Design an auditable playbook when no narrower one fits: a large migration, an ambitious multi-part change, or work a human reviews after stepping away. Scales rigor to the task, runs a hypothesis loop, and logs decisions via show-me-your-work. Use for /figure-it-out, 'figure it out', a large migration, or when no narrower playbook applies. |
| [how](../skills/how/SKILL.md) | explicit only | Use for "how does X work", code walkthroughs before changing something, and placement / ownership / layering questions ("where should this live", "which package owns this", "is this the right layer"). Explains subsystem architecture, runtime flow, onboarding mental models. Use why for motivation. |
| [interrogate](../skills/interrogate/SKILL.md) | explicit only | Use for "interrogate", "adversarial review", "multi-model review", "challenge this", "stress test this code", "find blind spots", or "tear this apart". Multiple LLM reviewers challenge changes from independent angles. |
| [maintain-verification-skill](../skills/maintain-verification-skill/SKILL.md) | explicit only | Periodic pass that keeps a project's verification skill and feature map honest: parallel source readers per feature, one live session driving every feature, at most one PR of proven corrections. Use for /maintain-verification-skill or "audit the verify skill". |
| [make-bot-ui](../skills/make-bot-ui/SKILL.md) | explicit only | Use when building a custom UI (page, dashboard, buttons) that should wake a Grok Bot over a webhook, when the user must provide a webhook sender key, or when exposing that UI on Tailscale. |
| [no-comments](../skills/no-comments/SKILL.md) | explicit only | Spawn Comment Sicko, fix accepted findings, and offer encodings for claimed constraints. |
| [poteto-mode](../skills/poteto-mode/SKILL.md) | explicit only | poteto's agent style for concise, detailed responses, deliberate subagents, unslopped prose, simple code, and verified work. Use for poteto, /poteto-mode, or requests to work in this style. |
| [principle-attack-the-premise](../skills/principle-attack-the-premise/SKILL.md) | explicit only | Apply when two or more fixes that share one premise have failed the same gate. Take a census of which actors hold the imbalance before the next fix, then question the premise instead of writing another fix that assumes it. |
| [principle-boundary-discipline](../skills/principle-boundary-discipline/SKILL.md) | explicit only | Apply when wiring validation, error handling, or framework adapters. Concentrate guards at system boundaries (CLI, config, network, external APIs); trust internal types and keep business logic in pure functions. |
| [principle-build-the-lever](../skills/principle-build-the-lever/SKILL.md) | explicit only | Apply to any non-trivial work, not just bulk work: edits, migrations, analyses, checks. Build the tool that does it or proves it (codemod, script, generator, or a skill your subagents follow) instead of working by hand. The tool is the artifact a reviewer can rerun. |
| [principle-encode-lessons-in-structure](../skills/principle-encode-lessons-in-structure/SKILL.md) | explicit only | Apply when you catch yourself writing the same instruction a second time, or notice a recurring correction. Encode the rule as a lint, metadata flag, runtime check, or script instead of more text. |
| [principle-exhaust-the-design-space](../skills/principle-exhaust-the-design-space/SKILL.md) | explicit only | Apply when facing a novel UI interaction or architectural decision with no precedent in the codebase. Build 2-3 competing prototypes and compare side by side before committing. |
| [principle-experience-first](../skills/principle-experience-first/SKILL.md) | explicit only | Apply when product, UX, or feature-scope tradeoffs come up. Choose user delight over implementation convenience; ship fewer polished features over more rough ones. |
| [principle-fix-root-causes](../skills/principle-fix-root-causes/SKILL.md) | explicit only | Apply when debugging. Trace each symptom to its root cause and fix it there; reproduce first, ask why until you reach it, resist nil-check guards that silence crashes. |
| [principle-foundational-thinking](../skills/principle-foundational-thinking/SKILL.md) | explicit only | Apply before writing logic: choosing core types and data structures, sequencing scaffold-vs-feature work, asking what concurrent actors share. Get the data structures right so downstream code becomes obvious. |
| [principle-guard-the-context-window](../skills/principle-guard-the-context-window/SKILL.md) | explicit only | Apply when context is filling up: large outputs, long files, repeated reads, fan-out planning. Route bulk to subagents; keep summaries in the main thread, not raw payloads. |
| [principle-laziness-protocol](../skills/principle-laziness-protocol/SKILL.md) | explicit only | Apply when refactoring, evaluating diff size, or tempted to add abstractions, layers, or signal threading. Bias toward deletion and the smallest change that solves the problem. |
| [principle-make-operations-idempotent](../skills/principle-make-operations-idempotent/SKILL.md) | explicit only | Apply when designing commands, lifecycle steps, or processing loops that run amid crashes, restarts, and retries. Converge to the same end state regardless of partial prior runs. |
| [principle-migrate-callers-then-delete-legacy-apis](../skills/principle-migrate-callers-then-delete-legacy-apis/SKILL.md) | explicit only | Apply when introducing a new internal API while old callers still exist. Migrate callers and delete the old API in the same wave instead of preserving compatibility layers. |
| [principle-minimize-reader-load](../skills/principle-minimize-reader-load/SKILL.md) | explicit only | Apply when reviewing or shaping code that's hard to trace. Count layers between question and answer, and hidden state in the reader's head; collapse one-caller wrappers and shrink mutable scope. |
| [principle-model-the-domain](../skills/principle-model-the-domain/SKILL.md) | explicit only | Apply when writing stateful logic, or when code branches a lot or repeats a shape assumption across files. Encode the domain in a structure instead of scattered conditionals. |
| [principle-never-block-on-the-human](../skills/principle-never-block-on-the-human/SKILL.md) | explicit only | Apply when tempted to ask 'should I do X?' on reversible work. Proceed, present the result, let the human course-correct after the fact; reserve confirmation for irreversible actions. |
| [principle-outcome-oriented-execution](../skills/principle-outcome-oriented-execution/SKILL.md) | explicit only | Apply during planned rewrites and migrations with explicit phase boundaries. Converge on the target architecture; don't preserve smooth intermediate states with throwaway compatibility code. |
| [principle-prove-it-works](../skills/principle-prove-it-works/SKILL.md) | explicit only | Apply after completing a task, before declaring done. Verify against the real artifact (run the feature, read the actual value, inspect the diff), not a proxy, self-report, or 'it compiles.' |
| [principle-redesign-from-first-principles](../skills/principle-redesign-from-first-principles/SKILL.md) | explicit only | Apply when integrating a new requirement into an existing design. Redesign as if the requirement had been a foundational assumption from day one, instead of bolting it on. |
| [principle-separate-before-serializing-shared-state](../skills/principle-separate-before-serializing-shared-state/SKILL.md) | explicit only | Apply when concurrent actors might write to the same file, branch, key, or state object. Eliminate the sharing first; serialize structurally only when one shared writer is a real invariant. |
| [principle-sequence-verifiable-units](../skills/principle-sequence-verifiable-units/SKILL.md) | explicit only | Apply to multi-step work (sweeps, migrations, runs of similar edits) and to how you stack commits and PRs. Break work into small units that each end in a verifiable state, check each before the next, and order delivery so the sequence proves itself to a reviewer. |
| [principle-subtract-before-you-add](../skills/principle-subtract-before-you-add/SKILL.md) | explicit only | Apply when sequencing an addition, refactor, or rewrite. Remove dead code, redundant validators, and stub references first, then build on the simpler base. |
| [principle-test-behavior-not-implementation](../skills/principle-test-behavior-not-implementation/SKILL.md) | explicit only | Apply when you write, change, or keep a test. Call the code the way its users do and assert the result they observe against a literal expected value. If the test would still pass when every imported function returns undefined, rewrite the assertion or delete the test. |
| [principle-type-system-discipline](../skills/principle-type-system-discipline/SKILL.md) | explicit only | Apply when designing types, reviewing a function signature, or writing code in any statically-typed language. Make illegal states unrepresentable, brand semantic primitives, parse external data at boundaries, refuse to lie to the compiler, exhaust variants, derive from authoritative schemas. |
| [recall](../skills/recall/SKILL.md) | explicit only | Reconstruct your recent working context from your own chat history, live state, and the shared record (user reports, prior fixes, incidents), then hand back a tight current-state brief. Use for 'recall my work on X', 'catch me up', 'what have I been working on', 'where did I leave off', before starting or resuming work. |
| [reflect](../skills/reflect/SKILL.md) | explicit only | Spawn three parallel review subagents over the active transcript, surface learnings, and route each to a concrete edit on an existing skill. Use when the user says reflect. |
| [setup-pstack](../skills/setup-pstack/SKILL.md) | implicit allowed | Configure which models pstack uses per role and at what reasoning budget. Detects your available models and writes an always-applied rule that overrides the skill defaults. Use for /setup-pstack, "configure pstack models", "pstack budget", or changing pstack's model choices. |
| [show-me-your-work](../skills/show-me-your-work/SKILL.md) | explicit only | Keep a reviewable decision trail for long-running or unattended work: a TSV log with one row per decision (what, why, evidence, result). Local by default; commit it when a reviewer needs the trail to trust the result. Use for /show-me-your-work, autonomous or multi-phase runs, or work a human reviews after stepping away. |
| [swarm](../skills/swarm/SKILL.md) | explicit only | Fan out N parallel workers, drain them, and return one report. Use for /swarm, 'swarm this', or parallel coverage, races, gauntlets, and exploration. |
| [tdd](../skills/tdd/SKILL.md) | explicit only | Use only when the user explicitly asks for TDD, a failing test, or a regression test, OR when the bug has an obvious cheap local test target. Skip when the test path is unclear, expensive, integration-heavy, or not requested. |
| [teach](../skills/teach/SKILL.md) | explicit only | Explain a body of work plainly so a person actually understands it. Runs the `how` and `why` skills and weaves what they find into one clear explanation. Use for 'teach me this', 'help me really understand X', 'explain this change or subsystem to me'. |
| [technical-writing](../skills/technical-writing/SKILL.md) | explicit only | Layered technical-writing standard: Diátaxis structure, Google developer style sentences, STE instruction rules, Global English syntax. Use for /technical-writing or when writing or reviewing docs, RFCs, readmes, PR descriptions, or commit messages. |
| [typescript-best-practices](../skills/typescript-best-practices/SKILL.md) | explicit only | TypeScript best practices. Use when reading or editing any .ts or .tsx file. |
| [unslop](../skills/unslop/SKILL.md) | explicit only | Cut AI tells from any writing. Must always apply. |
| [why](../skills/why/SKILL.md) | explicit only | Use for 'why does X work this way', 'why we picked Y', design rationale, regressions, postmortems, or data-backed thresholds. Discovers available MCPs and queries each evidence category (source control, issue tracker, long-form docs, real-time chat, infrastructure observability, error tracking, product analytics warehouse) in parallel, then returns a cited read on decisions and tradeoffs. Use how for runtime behavior. |

## Playbooks

- [authoring-a-skill](../skills/poteto-mode/playbooks/authoring-a-skill.md)
- [autonomous-run](../skills/poteto-mode/playbooks/autonomous-run.md)
- [autopilot-full](../skills/poteto-mode/playbooks/autopilot-full.md)
- [autopilot-stack](../skills/poteto-mode/playbooks/autopilot-stack.md)
- [babysit](../skills/poteto-mode/playbooks/babysit.md)
- [bug-fix](../skills/poteto-mode/playbooks/bug-fix.md)
- [eval](../skills/poteto-mode/playbooks/eval.md)
- [feature](../skills/poteto-mode/playbooks/feature.md)
- [hillclimb](../skills/poteto-mode/playbooks/hillclimb.md)
- [investigation](../skills/poteto-mode/playbooks/investigation.md)
- [multi-phase-plan](../skills/poteto-mode/playbooks/multi-phase-plan.md)
- [opening-a-pr](../skills/poteto-mode/playbooks/opening-a-pr.md)
- [orchestrate](../skills/poteto-mode/playbooks/orchestrate.md)
- [pause-safely](../skills/poteto-mode/playbooks/pause-safely.md)
- [perf-issue](../skills/poteto-mode/playbooks/perf-issue.md)
- [prototype](../skills/poteto-mode/playbooks/prototype.md)
- [refactoring](../skills/poteto-mode/playbooks/refactoring.md)
- [runtime-forensics](../skills/poteto-mode/playbooks/runtime-forensics.md)
- [session-pickup](../skills/poteto-mode/playbooks/session-pickup.md)
- [shipping](../skills/poteto-mode/playbooks/shipping.md)
- [trace-forensics](../skills/poteto-mode/playbooks/trace-forensics.md)
- [visual-parity](../skills/poteto-mode/playbooks/visual-parity.md)
- [worktree-cleanup](../skills/poteto-mode/playbooks/worktree-cleanup.md)

## Companion skills and agent roles

The three companions are path-loaded dependencies, not registered Codex skills. Load the exact files linked below; never resolve these calls to a same-named external skill or slash command.

- [control-cli](../companion-skills/control-cli/SKILL.md)
- [control-ui](../companion-skills/control-ui/SKILL.md)
- [deslop](../companion-skills/deslop/SKILL.md)
- [poteto-agent](../agents/poteto-agent.md)
- [Comment Sicko](../agents/comment-sicko.md)

## Dormant Benny pack

[Enter through FOR_AGENTS.md](../automations/benny/FOR_AGENTS.md) with the host contract loaded. Its three SKILL.md files are direct automation instructions, not registered slash skills. The pack is byte-preserved; Cursor-specific triggers and editor flows require a verified host adapter. No automation is enabled by this build.

- [reproduce-and-fix-issues](../automations/benny/skills/reproduce-and-fix-issues/SKILL.md)
- [setup-benny](../automations/benny/skills/setup-benny/SKILL.md)
- [triage-issue-reports](../automations/benny/skills/triage-issue-reports/SKILL.md)
