# Full pstack skill-catalog audit for a faithful Codex port

Audit date: 2026-09-18. Upstream: pstack 0.15.2, revision `5bf2b1544db739998121a306340631963c2ff3de`. This audit supersedes the earlier recommendation to substitute a minimal project-specific Chieftain workflow. The corrected objective is pstack's full behavior across all Codex projects and folders; the initial project is one pilot.

This is source research, not skill activation. No upstream workflow, installer, automation, model call, or ERP action was executed. The tables describe upstream facts. Port consequences are proposals or host constraints, not claims of implemented compatibility. Every linked skill was read in full, including frontmatter. The initial principles batch was truncated across two files; both were subsequently read again in full. No skill row is based only on a search excerpt.

**Coverage:** all 50 `SKILL.md` files, comprising 47 registered skills and three dormant Benny skills; both agent definitions; all skill-specific Markdown references outside `poteto-mode/references/`; Benny's entry document and three configuration/prompt templates; the plugin manifest, MIT license, decision-log helper and recovered TSV template. The parent owns the separate complete playbook/runtime audit. Exact supplemental coverage and the resolved acquisition gap are recorded below.

## Preserve the workflow graph, translate the host

The plugin manifest registers `./skills/` and `./agents/`. It does not register Benny. Forty-seven registered skills do not mean forty-seven unrelated slash commands: `poteto-mode` activates a routing graph, other skills call each other, and 23 leaf principles are loaded when applied. `disable-model-invocation: true` is pervasive but does not negate explicit calls from the mode or a workflow. `setup-pstack` lacks that flag. TypeScript guidance also has file-pattern metadata. These distinctions need a tested Codex discovery/routing equivalent rather than a blanket conversion to automatic invocation.

Faithful porting means retaining upstream workflow stages, named roles, stopping conditions, outputs, and exceptions. Existing local skills may supply host-specific capabilities behind explicit adapters, but must not replace pstack's reasoning or workflow semantics merely because their names overlap. Preserve qualified pstack names to avoid collisions with local `tdd`, review, and authoring skills.

Mode activation does **not** invoke all 47 registered skills on every task. Its explicit trigger list routes directly to `how`, `architect`, `swarm`, `arena`, `interrogate`, `unslop`, `technical-writing`, `no-comments`, and `show-me-your-work`, with `figure-it-out` selected by its broader-work rules. The selected playbook can add calls. Other skills are reached through another skill or manual/situational invocation; examples include `teach` calling `how`/`why`, `setup-pstack` offering verification generation, and `automate-me` creating a personal mode. Principles load when applied. The table's incoming-routing column distinguishes these routes instead of claiming universal automatic invocation.

## Registered workflow skills: 24

| Skill/source | Trigger and role | Incoming and outgoing routing | Runtime coupling and port consequence |
|---|---|---|---|
| [architect] | Explicit architect/design requests; mode invokes for code crossing a function boundary. Designs, implements, and scraps a wrong shape. | `poteto-mode`, `figure-it-out`, `no-comments` → `how`; `why` when ownership/layering changes; `arena` for competing sketches; optional `interrogate`. | Preserve caller-usage-first sketches, at least two structural alternatives, default implementation without checkpoint, explicit checkpoint opt-in, and repeated-friction redesign loop. `no-comments` deliberately invokes sketch-only. Translate model panel/worktrees, not the design method. |
| [arena] | Same-task parallel candidates, bakeoffs, or alternative artifact designs. | Mode/`architect`/`blast-radius` → configured runners, then one cross-family-preferred judge; parent picks base and grafts. | Candidates get the same task but not the grading rubric. Judge starts after candidate completion, alongside parent reading. Preserve isolated outputs, full candidate reading, N−1 dropouts, explicit graft/rejection record, and reframe-on-wild-divergence. |
| [automate-me] | Create/update/refresh a personal mode from working style. | User → scoped transcript-mining workers → built-in `create-skill` + `unslop`; reads `poteto-mode` for shape only. | Translate workspace transcript discovery, personal/project skill paths, AskQuestion multi-select, authoring workflow, worktree/PR delivery. Update mines since last edit; preserve existing uncontradicted rules. User feedback, not a benchmark loop, evaluates style. |
| [blast-radius] | What could this change break; risky small diff. | User → `why` history; wide change may use `arena`; output through `unslop`. | Preserve proof ladder and the decisive safety fact, including an actual script/test against real code when feasible. Caller enumeration alone is insufficient. Git/gh and real-app capabilities must be available or explicitly reported missing. |
| [bro] | Restate the last answer plainly. | Direct user request. | Pure text semantics. Preserve short, coherent restatement without jargon; no added investigation or orchestration. |
| [create-verification-skill] | Generate a project-specific way to drive a real app. | User or optional `setup-pstack` offer → repo interview → generated verification skill/map → offer `maintain-verification-skill`. | Translate `.cursor/skills/verify-*` placement and host control tools. Preserve launch/doctor/drive/evidence/cleanup, initial 3–5 feature map, real execution before delivery, and proof surviving cleanup. Broken startup must be fixed or reported before generation. |
| [figure-it-out] | No narrower playbook, large migration, ambitious multipart work, user away. | Mode → principle index; `architect`/`arena` for unsettled consequential design; `show-me-your-work` owns trail. | Preserve quantified falsifiable frame, one checkpoint for multi-hour commitment, workflow artifact before code, baseline/harness first, hypothesis loop per unit, delegated judges, and VERIFIED/NOT VERIFIED/INCONCLUSIVE distinction. Host scheduling is separate from loop semantics. |
| [how] | How a subsystem works; placement/ownership/layering; mode before nontrivial changes. | `poteto-mode`, `architect`, `teach`, comment review, Benny → explainer directly for simple case; 2–4 explorers then explainer for complex case. | Simple does not mean answer locally: it still spawns one explainer. Preserve exact explorer/explainer contracts and only light parent edits. Replace Cursor readonly tool assumptions with actual read capabilities; preserve explicit role models. |
| [interrogate] | Adversarial review, challenge/stress test, contested design. | Mode/`architect` → one identical prompt/rubric/code-quality lens per configured reviewer → parent judgment. | Preserve no automatic fixes, severity/evidence, agreement map and Act On/Consider/Noted/Dismissed filtering. Upstream retries an unresolvable slug with a closest valid equivalent; an explicitly requested exact model cannot be silently changed on Codex. Record that divergence. |
| [maintain-verification-skill] | Audit/periodically maintain generated verification skill and feature map. | User after generator → one source reader per feature → coordinator's required serial live pass. | Preserve clean/changed/blocked results, verification-directory-only edits, no product fixes, coverage of every feature, doctor after surprises, cleanup after failures, one retry for skill drift, retained evidence, at most one correction PR. Translate path discovery and control adapter. |
| [make-bot-ui] | A custom page/dashboard waking a Grok Bot webhook; sender key; Tailscale. | Direct user request → `update_state` routine creation → secret-request card → local server → webhook wake. | Hard Cursor/Grok coupling: routine API, UI panel, `SendToUser` secret card, webhook format, `[routine]` event. Preserve server-only secret handling, one POST/no retry, fallback log, shared Tailscale node. Codex heartbeat is not an equivalent webhook receiver. Mark unsupported until a real adapter exists. |
| [no-comments] | Before review; explicit comment cleanup. | Mode → Comment Sicko → parent audit; disputed keeps/kills → `how`/`why`; nontrivial accepted fixes → `architect` sketch-only, then parent implementation. | Preserve deletion policy and exception list, exact-scope enforcement, one rerun then fail, root-cause fixes, and interactive approval for constraint encodings. This is not merely a read-only reviewer. Its unusual delete-and-report behavior for unapproved encodings is upstream semantics requiring clear disclosure. |
| [poteto-mode] | `/poteto-mode`, poteto, style request; sticky activation across suitable turns. | Central router into all 23 playbooks, principles and workflow skills; playbook helpers use `poteto-agent`; routed skills choose their own agents. | Translate mode/reminder persistence, todos, Task/background/readonly/model/environment controls. Preserve automatic routing, exact playbook steps with explicit skip reasons, full principles reads, before-commit deslop, before-review no-comments, and full reply contracts. No replacement with Chieftain-only routing. |
| [recall] | Catch up/rebuild recent working context before work. | User → classify: one prior chat to session-pickup, habits to automate-me; history workers alongside `why` shared-record investigators → live git/gh check → unslop. | Translate workspace-scoped transcript access and actual timestamps. Default seven days; never silently narrow “all.” Named feature triggers shared-record sweep by default. Preserve capsule/status tags/problems/one next move, full transcript when actual actions matter. |
| [reflect] | Explicit reflect request after substantive session. | Three parallel judgment/tooling/divergent reviewers → separate synthesizer → structural-enforcement check → user-selected Accepted edits; create-skill for substantive/new/description tuning. | Preserve scoped transcript/digest fallback, referenced-context-only MCP lookups, no reviewer edits, approved-subset application, automatic backlog routing subject to host authorization, and validator when present. Do not turn this into automatic self-modifying skill behavior. |
| [setup-pstack] | Configure model roles/reasoning budget. | Direct user or initial setup → runtime model discovery → budget → role confirmation → persistent rule; optional verification-skill offer. | Translate user-wide `.cursor/rules/pstack-models.mdc` into a Codex-supported configuration consumed by every role. Preserve existing choices, aliases, panel list cardinality and defaults. Split provider model/effort rather than mechanically editing Codex IDs; disclose unavailable roles. |
| [show-me-your-work] | Long/unattended/autonomous/multiphase work; explicit trail. | Mode/figure-it-out/program workflows → log helper → transcript audit → different-family trail reviewer. | Preserve canonical six-column TSV, evidence pointers, formula-prefix escaping, checkpoints instead of every action, local by default/commit when auditable, and final Attention with reviewer model. Translate transcripts and model delegation. Append-only rule conflicts with audit's “cut invented rows”; record rather than silently resolve. |
| [swarm] | Parallel coverage, race, gauntlet, or exploration. | Mode/explicit call → N workers → terminal aggregate. | Preserve coverage vs race shapes and predeclared first-pass/rank-all/best-of rule. N means total workers, not concurrency. Cloud default/local exceptions and cloud base branch need explicit backend mapping. Dropout is reported; missing coverage cannot become a pass. |
| [tdd] | Explicit TDD/failing/regression test, or obvious cheap local bug test. | Bug-fix workflows/Benny/user → failing-before → minimal fix → passing-after → adjacent checks. | Pure workflow plus host test runner. Preserve the deliberate exemption for unclear/expensive/integration-heavy tests, explanation before skipping, nearest useful executable check, no weakened assertions, and before/after evidence. Do not substitute another TDD skill's broader trigger. |
| [teach] | Teach/explain a body of work for understanding. | User → real `how` and `why` invocations, often parallel; narrow why in request; output through unslop. | Preserve how/why findings, why's confidence, smallest complete answer first and conversational depth. For 3+ parts build successive diagrams; spatial explanations call image generation. Host visualization adapter must support this or report the gap. |
| [technical-writing] | Docs, RFCs, READMEs, PR descriptions, commits. | Mode and any covered writing → unslop; proposes offender additions without editing unslop. | Preserve four layers: Diátaxis, Google style, STE, Global English; one mode/document; reader-first concrete terminology; symbols/counts verified; tabs in snippets. Text semantics portable, subject to higher-priority host reply rules. |
| [typescript-best-practices] | Reading/editing `.ts` or `.tsx`; path metadata. | File-pattern activation → type-system-discipline first; boundary discipline and runnable tests. | Preserve `paths` semantics in adapter if Codex metadata differs; retain all rules/examples. No added schema dependency for one guard; strengthen types only for partial operations. Existing TS guidance is not a substitute for this exact policy. |
| [unslop] | Every prose surface, including replies and agent prompts. | Mode, teach, technical-writing, automate-me, logs, reviews, recall, Benny. | Preserve stable rule IDs/gaps and scan→rewrite→self-audit; no meaning/uncertainty loss. Apply host instruction precedence explicitly for contradictory formatting rules. Do not rename to or replace with local humanizer. |
| [why] | Rationale, tradeoffs, regressions, history, thresholds. | Mode-adjacent investigation, architect, teach, recall, comment review, Benny → git anchor → source investigators → separate synthesizer. | Preserve seven-category coverage and null/gap reporting, one owned source per investigator, cross-source leads relayed instead of chased, defensive-code incident overlay, and five confidence tiers. Discover Codex connectors instead of Cursor mcps directory; CLI models do not inherit connectors. |

## Principle skills: 23

All principles are registered leaf skills with `disable-model-invocation: true`. Their main incoming edge is the `poteto-mode` index; individual workflows also name them. The port must retain leaf reads and principle-specific conditions rather than replacing this index with a generic quality checklist.

| Skill/source | Trigger | Role and cross-skill relationship | Port consequence |
|---|---|---|---|
| [principle-attack-the-premise] | Two fixes sharing a premise fail the same gate. | Write premise; rerunnable per-actor census; remove concentrated asymmetry. Calls build-the-lever, fix-root-causes, laziness; distinguishes redesign-from-first-principles. | Preserve refusal to start next fix before census; an even census sends investigation elsewhere. |
| [principle-boundary-discipline] | Validation, errors, framework adapters. | Parse/validate at external boundaries; pure business logic; domain concepts in public interfaces. Used by architect, TypeScript and reviews. | Portable policy. Do not infer that external model/tool output is trusted internal data. |
| [principle-build-the-lever] | Any nontrivial work, including one-off checks. | First unit teaches recipe; deterministic rerunnable tool over manual fan-out; smallest tool; delegate recipe outside worker scope. | Preserve requirement to produce an actual file when claiming application; commit when work outlives session. |
| [principle-encode-lessons-in-structure] | Repeated correction/instruction. | Strongest feasible mechanism before prose; capture→route→apply; reflect routes structural candidates to backlog. | Portable policy. Generated mechanism must still respect scope/authorization; no implied autonomous global settings changes. |
| [principle-exhaust-the-design-space] | Novel interface/architecture without precedent. | Two or three structurally distinct prototypes before commitment; architect/arena make concrete. | Preserve established/mechanical/forced-choice exemptions and distinct alternatives, not merely model count. |
| [principle-experience-first] | Product/UX/scope tradeoff. | Consumer and maintainer experience determine target; foundational-thinking determines sequence. | Portable; no project-specific reduction or imposed user preference. |
| [principle-fix-root-causes] | Debugging. | Reproduce, trace why, instrument, look for pattern; restart bugs prioritize stale persisted state. | Preserve scope fences from callers, especially no-comments/Benny; principle alone cannot authorize wider edits. |
| [principle-foundational-thinking] | Before core types, logic, concurrency, scaffolding. | Data/access patterns first; isolation before shared writes; subtraction before scaffold; coherent increments. | Portable; map actual Codex shared resources, not imaginary isolation. |
| [principle-guard-the-context-window] | Large payloads/context pressure. | Bulk to scoped workers; main gets summaries; frequently used templates inline; phase scope capped. | Preserve bounded context transfers. CLI workers need explicit packet; native full-history inheritance has model constraints. |
| [principle-laziness-protocol] | Refactor, diff sizing, temptation to abstract/thread state. | Delete; flatten unhelpful layers; consolidate decisions; prefer direct solution. | Preserve as a design principle, not a license to omit requested pstack functionality. |
| [principle-make-operations-idempotent] | Commands/lifecycle/retry loops. | Reconcile partial state, adopt live sessions, clean stale artifacts; ask crash-at-every-point/twice behavior. | Preserve goal; translate PID/stale-lock assumptions for host identity and process lifecycle. A lease expiry alone proves nothing stopped. |
| [principle-migrate-callers-then-delete-legacy-apis] | New internal API with old callers. | Inventory/migrate/delete within one wave; temporary adapters exceptional; only when no external compatibility requirement. | Preserve applicability bounds. A host adapter serving two real runtimes is not automatically forbidden legacy code. |
| [principle-minimize-reader-load] | Hard-to-trace code/interface. | Track layers and mutable state separately; compress interfaces; narrow state; links guard-context-window. | Portable. Preserve deep-module vs deep-call-chain distinction. |
| [principle-model-the-domain] | Stateful logic/repeated branching/shapes. | Structures encode invariants and ownership; rejects temporal decomposition; no forced abstraction. | Portable; relevant to attempts vs tasks vs artifacts without collapsing their meanings. |
| [principle-never-block-on-the-human] | Reversible work tempted to ask permission. | Proceed/report; asynchronous supervision; product direction belongs to human; irreversible boundaries remain. | Preserve existing authorization and host controls. Upstream general team-message allowance elsewhere conflicts with this leaf's external-message confirmation example. |
| [principle-outcome-oriented-execution] | Planned rewrites/migrations. | Planned bounded temporary breakage acceptable; final static/runtime verification mandatory. | Portable; preserve declared verification boundaries rather than demanding all intermediate states work. |
| [principle-prove-it-works] | Before completion. | Actual artifacts/process/values, full integration path, delegated diff/runtime inspection; deterministic check preferred. | Preserve evidence levels; source review cannot certify runtime compatibility or model route. |
| [principle-redesign-from-first-principles] | New requirement in existing design. | Read affected files; derive shape as if requirement existed initially; update types/docs/examples; deliver incrementally. | Portable. Corrected full-port goal is binding input, not an optional premise to delete. |
| [principle-separate-before-serializing-shared-state] | Concurrent writers. | Eliminate shared write target; merge independent facts at read boundary; structural serialization only when sharing required. | Preserve worktree/output ownership and actual runtime-resource controls. Instructions are not locks. |
| [principle-sequence-verifiable-units] | Multistep sweeps, migration, stacked delivery. | Green per unit; baseline against trunk; test-before-fix story; complements prove-it-works/build-lever. | Preserve actual Git model and user changes when translating rebase advice; no hidden permission expansion. |
| [principle-subtract-before-you-add] | Addition/refactor/rewrite sequencing. | Remove dead weight before construction; observed usage; no speculative safeguards; remove contentless references. | Portable; “dead” cannot mean a requested skill whose host adapter is unfinished. |
| [principle-test-behavior-not-implementation] | Writing/reviewing/retaining tests. | Real subject plus literal observed result; diagnoses weak, mock-only, self-referential, constant-pin, fixture-only tests. | Preserve exception for cross-table relations/type tests. Treat undefined-return heuristic as upstream guidance, not a universally sound formal classifier. |
| [principle-type-system-discipline] | Typed-language type/signature design. | Construct valid shapes, semantic types, parse boundaries, exhaust variants, derive schemas, strengthen only where partial. | Portable semantics; TypeScript examples implement them. Preserve upstream examples with any source defects separately recorded, not silently corrected. |

## Dormant Benny automation skills: 3

| Skill/source | Trigger and role | Incoming and outgoing routing | Runtime coupling and port consequence |
|---|---|---|---|
| [setup-benny] | Explicit setup/configuration of optional pack. | User points to `FOR_AGENTS.md`; copy full pack; read setup directly; shared pstack skills; built-in automate once per new automation. | Preserve dormant status, user-owned configs outside pack, conflict-aware refresh, committed same-repo operational paths, fresh-project dependency verification, and seven thread-safety tests before enabling. Cursor editor-only creation and manual existing-automation updates need explicit Codex translation; do not pretend a heartbeat supplies Slack triggers. |
| [triage-issue-reports] | Only configured Benny triage automation on source report. | Immutable thread→full evidence→how/why cause tracing→routing→tracker adapter/dedupe→one marker verdict→bounded follow-up. | Preserve coordinator-only Slack writes, enforceable worker restrictions or no worker, fresh parent preflights, no root/cross-channel/DM fallback, compensation if created issue loses Slack handoff, exact markers and trusted identity. Unsupported write/compensation capability fails closed. |
| [reproduce-and-fix-issues] | Only configured repro automation after trusted marker. | Thread gates→ownership/existing-artifact check→control skill/feature map→how/why→real UI twice→media review→rejection window→verify existing or bounded fix→optional tdd→proof twice→draft PR→cleanup. | Preserve all seven control capabilities including recording; no repro means no fix; existing fix means no competing patch; isolated code worker only when Slack writes/credentials excluded. No merge/deploy. Native Codex tool availability must be tested, not assumed. |

## Agent definitions and nonobvious interactions

- **poteto-agent** is a routing wrapper, marked background, that reads the entire mode first and loads leaf principles when used. Its description prefers resuming the existing conversation agent. Mode text separately warns against interrupt-chained resumes and requires fresh consolidated scope in that case. Preserve the distinction; do not globally ban all resumes or always resume everything.
- **Comment Sicko** has an exact first utterance and narrow comment-editing scope. It protects license headers, proven external constraints, public API contracts, permitted links and certain suppressions. It flags exact symbols for root-cause work but must not edit application code. The parent owns the acceptance audit and actual repairs. The agent says “Report only” while discussing touched files/deletion counts; the surrounding no-comments workflow explicitly inspects its diff. Preserve the intended comment-editing role while flagging this wording ambiguity.
- **No-comments is consequential.** Accepted comments may be deleted even when a proposed structural encoding is not approved, with the constraint reported open. A faithful port must expose this behavior rather than call the skill a harmless read-only lint. Host/user scope still wins.
- **Cross-model work is structural.** Arena, interrogate, architect, how, why, reflect and trail review use different delegation graphs. A universal “spawn reviewer” function with one prompt would erase their behavior. Candidate generation, investigation, synthesis, judgment, comment cleanup and trail audit are distinct roles.
- **Role configuration is user-wide, execution context is project-specific.** Preserve one model policy across folders, but resolve the current project's repo, instructions, branch, transcript scope and connectors on each invocation. File pointers work only where the chosen worker can read them. Safe-mode CLI workers require explicit instructions/context; they do not inherit Codex tools.
- **Research and review do not grant mutation.** Why/reflect use `readonly: false` only because Cursor readonly strips MCP. Their prompts still forbid writes. Port to read capabilities plus a documented enforcement level, not blanket tool powers. Conversely, no-comments requires comment editing, so not every reviewer should be tool-free.
- **Automatic routing has specific exceptions.** Casual turns and opt-out bypass mode. Empirically answerable forks route to Prototype, but cited read-only Investigation stays read-only. Opening a PR does not start Babysit; PR-status requests do. Large bespoke runs go to figure-it-out even when Feature superficially matches. Standing multi-day programs go to Orchestrate.

## Compatibility boundaries to label rather than erase

1. **Activation:** Codex must provide an explicit equivalent for sticky mode, reminders, path triggers and slash/name discovery. Unsupported frontmatter silently ignored is not a port. Test fresh task, follow-up, opt-out, project switch, and delegated wrapper loading.
2. **Models:** represent role, backend, model and effort separately. Preserve upstream lists and inheritance semantics; negotiate explicitly when Grok/Opus or a required cross-family judge is unavailable. This session advertises Fable through Claude CLI, not native spawn. Do not replace unavailable roles merely because an existing local routing skill uses another model.
3. **Delegation/lifecycle:** map Cursor Task/background/local/cloud/depth to capabilities actually offered by the chosen Codex host. Queue under concurrency limits while preserving total work and dependency order. Visible user-owned tasks are not an automatic replacement for ephemeral workers. Parent-held routing state must survive a CLI packet without importing unrelated chat history.
4. **Authorization:** user/host rules override upstream external messaging, settings edits, commits, installation and deployment. Keep existing granted authorization; do not add repeated blanket approval pauses. Preserve explicit skill approval points such as reflect and optional architect checkpoints where applicable. Record each unavoidable divergence alongside the original behavior.
5. **Companions:** upstream explicitly relies on Cursor built-ins `create-skill` and `automate`, and `cursor-team-kit`'s `deslop`, `control-cli`, `control-ui`. The parent separately reads the companion skills at this revision; they remain part of the intended workflow, not removable extras. A Codex skill-creator or browser tool can implement a named capability adapter only after contract comparison. Availability of a similarly named local skill is not proof of parity.
6. **Unsupported integrations:** make-bot-ui's Cursor/Grok webhook and secret cards, Benny Slack event triggers/editor flow, cloud worker placement, and transcript access need real implementations or a clearly marked unavailable result. Keep their source and invocation routes in the port; do not delete them from the feature set.
7. **Licensing:** MIT, copyright 2026 Lauren Tan. Preserve copyright and permission notice in copied/substantial derived source. Record pinned revision and source hashes; separate upstream copies from compatibility changes so updates can be reconciled.

## Reference gaps and source ambiguities

- Resolved acquisition gap: `skills/show-me-your-work/references/decision-log-template.tsv` was initially absent locally despite being listed in the upstream tree. The parent recovered it; it was then fully read and hash-checked. Its six-column header agrees with `scripts/log.sh`. No upstream missing-template claim remains.
- Benny automation prompt templates provide `message_ts`, while operational skills use `trigger.ts` when `thread_ts` is empty. Translate/normalize those coordinates explicitly and test top-level reports; never guess a source thread.
- Why says both “one investigator per category” and “do not ask one agent to cover multiple MCPs.” With multiple MCPs in one category, retain source isolation and document the roster decision. With many sources, concurrency limits alter scheduling, not coverage obligations.
- The decision log says append-only, but its audit instructs cutting invented/padded entries. Preserve raw history and flag this conflict for an explicit port resolution instead of quietly selecting one instruction.
- The TypeScript duration example says start-plus-duration prevents negative ranges although `durationMs: number` permits negatives. This is a source example defect, not a reason to remove type-system-discipline or invent host behavior.
- Actual Cursor built-in/companion implementations are outside this pinned pstack corpus. Their exact contracts remain unverified here. The parent separately audits all 23 playbooks, mode references and runtime helpers; this document does not claim those files were read in this bounded subtask.

## Port-verification implications

Validate fidelity with scenario traces against the routing graph: simple/complex how, narrowed/full why, architect checkpoint and scrap, arena rubric separation, no-comments fix/encoding paths, reflect approval subset, all verification-map features, mode opt-out and reactivation, model-role inheritance, transcript scope, and both Benny failure paths. For every scenario, compare selected skills, delegated roles, required evidence, output shape and stopping condition. Do not evaluate success only by whether a Codex worker produced plausible code. These are proposed parity tests; none were executed by this audit.

## Exact read coverage

The 50 linked table entries form the complete SKILL.md read manifest. Supplemental file paths and source integrity results are appended below. Runtime scripts were not executed.

Fully read and SHA256-checked: **93 files**. All matched the current source manifest. This includes 50 skills, 34 reference files, and 9 agent/manifest/license/entry/template/helper files.

Supplemental full reads:

- `.cursor-plugin/plugin.json`
- `LICENSE`
- `agents/comment-sicko.md`
- `agents/poteto-agent.md`
- `automations/benny/FOR_AGENTS.md`
- `automations/benny/skills/reproduce-and-fix-issues/references/control-adapter.md`
- `automations/benny/skills/reproduce-and-fix-issues/references/feature-map.example.md`
- `automations/benny/skills/reproduce-and-fix-issues/references/verify-existing-fix.md`
- `automations/benny/skills/triage-issue-reports/references/routing.example.md`
- `automations/benny/templates/configuration.example.yaml`
- `automations/benny/templates/reproduce-automation-prompt.md`
- `automations/benny/templates/triage-automation-prompt.md`
- `skills/architect/references/design-red-flags.md`
- `skills/architect/references/rationale-template.md`
- `skills/architect/references/runner-prompt.md`
- `skills/create-verification-skill/references/feature-map-example/README.md`
- `skills/create-verification-skill/references/feature-map-example/create-note.md`
- `skills/create-verification-skill/references/feature-map-example/search.md`
- `skills/how/references/explainer-prompt.md`
- `skills/how/references/explorer-prompt.md`
- `skills/interrogate/references/code-quality-review.md`
- `skills/interrogate/references/lead-judgment.md`
- `skills/interrogate/references/reviewer-prompt.md`
- `skills/interrogate/references/rubric.md`
- `skills/reflect/references/divergent-reviewer.md`
- `skills/reflect/references/judgment-reviewer.md`
- `skills/reflect/references/synthesizer.md`
- `skills/reflect/references/tooling-reviewer.md`
- `skills/show-me-your-work/references/decision-log-template.tsv`
- `skills/show-me-your-work/scripts/log.sh`
- `skills/typescript-best-practices/references/patterns.md`
- `skills/why/references/epistemics.md`
- `skills/why/references/investigator-prompt.md`
- `skills/why/references/source-playbook.md`
- `skills/why/references/sources/code-archaeology.md`
- `skills/why/references/sources/databricks.md`
- `skills/why/references/sources/datadog.md`
- `skills/why/references/sources/incident-postmortem.md`
- `skills/why/references/sources/linear.md`
- `skills/why/references/sources/notion.md`
- `skills/why/references/sources/sentry.md`
- `skills/why/references/sources/slack.md`
- `skills/why/references/synthesizer-prompt.md`

Primary-source link definitions:

[reproduce-and-fix-issues]: ../upstream/pstack/automations/benny/skills/reproduce-and-fix-issues/SKILL.md
[setup-benny]: ../upstream/pstack/automations/benny/skills/setup-benny/SKILL.md
[triage-issue-reports]: ../upstream/pstack/automations/benny/skills/triage-issue-reports/SKILL.md
[architect]: ../upstream/pstack/skills/architect/SKILL.md
[arena]: ../upstream/pstack/skills/arena/SKILL.md
[automate-me]: ../upstream/pstack/skills/automate-me/SKILL.md
[blast-radius]: ../upstream/pstack/skills/blast-radius/SKILL.md
[bro]: ../upstream/pstack/skills/bro/SKILL.md
[create-verification-skill]: ../upstream/pstack/skills/create-verification-skill/SKILL.md
[figure-it-out]: ../upstream/pstack/skills/figure-it-out/SKILL.md
[how]: ../upstream/pstack/skills/how/SKILL.md
[interrogate]: ../upstream/pstack/skills/interrogate/SKILL.md
[maintain-verification-skill]: ../upstream/pstack/skills/maintain-verification-skill/SKILL.md
[make-bot-ui]: ../upstream/pstack/skills/make-bot-ui/SKILL.md
[no-comments]: ../upstream/pstack/skills/no-comments/SKILL.md
[poteto-mode]: ../upstream/pstack/skills/poteto-mode/SKILL.md
[principle-attack-the-premise]: ../upstream/pstack/skills/principle-attack-the-premise/SKILL.md
[principle-boundary-discipline]: ../upstream/pstack/skills/principle-boundary-discipline/SKILL.md
[principle-build-the-lever]: ../upstream/pstack/skills/principle-build-the-lever/SKILL.md
[principle-encode-lessons-in-structure]: ../upstream/pstack/skills/principle-encode-lessons-in-structure/SKILL.md
[principle-exhaust-the-design-space]: ../upstream/pstack/skills/principle-exhaust-the-design-space/SKILL.md
[principle-experience-first]: ../upstream/pstack/skills/principle-experience-first/SKILL.md
[principle-fix-root-causes]: ../upstream/pstack/skills/principle-fix-root-causes/SKILL.md
[principle-foundational-thinking]: ../upstream/pstack/skills/principle-foundational-thinking/SKILL.md
[principle-guard-the-context-window]: ../upstream/pstack/skills/principle-guard-the-context-window/SKILL.md
[principle-laziness-protocol]: ../upstream/pstack/skills/principle-laziness-protocol/SKILL.md
[principle-make-operations-idempotent]: ../upstream/pstack/skills/principle-make-operations-idempotent/SKILL.md
[principle-migrate-callers-then-delete-legacy-apis]: ../upstream/pstack/skills/principle-migrate-callers-then-delete-legacy-apis/SKILL.md
[principle-minimize-reader-load]: ../upstream/pstack/skills/principle-minimize-reader-load/SKILL.md
[principle-model-the-domain]: ../upstream/pstack/skills/principle-model-the-domain/SKILL.md
[principle-never-block-on-the-human]: ../upstream/pstack/skills/principle-never-block-on-the-human/SKILL.md
[principle-outcome-oriented-execution]: ../upstream/pstack/skills/principle-outcome-oriented-execution/SKILL.md
[principle-prove-it-works]: ../upstream/pstack/skills/principle-prove-it-works/SKILL.md
[principle-redesign-from-first-principles]: ../upstream/pstack/skills/principle-redesign-from-first-principles/SKILL.md
[principle-separate-before-serializing-shared-state]: ../upstream/pstack/skills/principle-separate-before-serializing-shared-state/SKILL.md
[principle-sequence-verifiable-units]: ../upstream/pstack/skills/principle-sequence-verifiable-units/SKILL.md
[principle-subtract-before-you-add]: ../upstream/pstack/skills/principle-subtract-before-you-add/SKILL.md
[principle-test-behavior-not-implementation]: ../upstream/pstack/skills/principle-test-behavior-not-implementation/SKILL.md
[principle-type-system-discipline]: ../upstream/pstack/skills/principle-type-system-discipline/SKILL.md
[recall]: ../upstream/pstack/skills/recall/SKILL.md
[reflect]: ../upstream/pstack/skills/reflect/SKILL.md
[setup-pstack]: ../upstream/pstack/skills/setup-pstack/SKILL.md
[show-me-your-work]: ../upstream/pstack/skills/show-me-your-work/SKILL.md
[swarm]: ../upstream/pstack/skills/swarm/SKILL.md
[tdd]: ../upstream/pstack/skills/tdd/SKILL.md
[teach]: ../upstream/pstack/skills/teach/SKILL.md
[technical-writing]: ../upstream/pstack/skills/technical-writing/SKILL.md
[typescript-best-practices]: ../upstream/pstack/skills/typescript-best-practices/SKILL.md
[unslop]: ../upstream/pstack/skills/unslop/SKILL.md
[why]: ../upstream/pstack/skills/why/SKILL.md
