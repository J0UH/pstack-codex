# Auditable adaptations from pstack 0.15.2

The pinned source is revision `5bf2b1544db739998121a306340631963c2ff3de` of `cursor/plugins`. `upstream/pstack/` is the full source snapshot. `upstream/cursor-team-kit/` contains the three referenced companion skills and their license. Both manifests include pinned URLs, byte lengths and SHA256 hashes. `scripts/build.py` checks all declared files, rejects missing/unmanifested inputs, and never runs source helpers or downloads dependencies.

## Deterministic generation

Run `python3 scripts/build.py` from any directory, or `python3 scripts/build.py --check` for a read-only drift check. `--output <directory>` generates the same bytes into an isolated directory. No timestamp, machine name, local checkout path, absolute source location or user model selection enters generated output. Executable helper files are identified by their source shebang and emitted with mode 0755; other files use 0644.

The build owns only `skills/`, `agents/`, `automations/`, `companion-skills/`, `docs/routing-index.md` and `adaptations.json`. It does not modify upstream, model policy, user settings, hooks, worker implementations or unrelated documentation. A future build removes only stale generated files recorded in the prior ledger, never unrecorded user files. This is not an installer.

`adaptations.json` records every generated source-derived file's source path/hash, output path/hash/mode and exact transformation identifiers. Untouched bodies and reference/helper files remain byte-identical. The generator has no generic text-rewriting engine and does not rewrite model slugs inside upstream prose or weaken source checks.

| Transformation | Files | Exact change |
|---|---|---|
| `codex-frontmatter` | 47 registered SKILL.md files, two agent definitions, three companion skills | Emit supported `name` and `description`; normalize names to folder/file identity. Preserve original frontmatter verbatim in the ledger. Cursor mode/reminder/icon/color/paths/background flags are interpreted by the host adapter, not silently claimed as native Codex metadata. |
| `host-contract-notice` | Those entrypoints plus all 23 playbooks | Insert one marked notice linking relatively to `adapters/host.md`. Preserve the original body bytes after it. |
| `invocation-policy` | 47 registered skills | Add `agents/openai.yaml`. Preserve upstream explicit-only discovery: 46 pstack skills false, setup-pstack true. Companions are path-loaded dependencies, not registered skills; they have no discovery policy files. |
| `routing-index` | `docs/routing-index.md` | Generate a complete linked catalog, 23 playbooks, role definitions, companions, and dormant entrypoint without replacing the router's decisions. |
| No transformation | Remaining copied references/helpers/assets; entire dormant Benny pack | Byte-for-byte copy. The host contract is loaded before entering the dormant pack. |

## Deliberate host translations

`host.md` is the explicit operational translation for preserved inline Cursor instructions. It maps package-relative skills and helpers, per-session mode state, model-role configuration, native collaboration, optional external workers, transcript access, project skill authoring, and controls. It distinguishes host capabilities from implemented workflow policy. Its mapping overrides incompatible host operations, not the upstream reasoning steps.

The current user's Astra/Fable/optional-Grok policy is supplied separately by the coordinator's model configuration. The build never selects, substitutes or calls a provider. Missing role capability is reported, not hidden by choosing a cheaper or nearby model. Existing local skills are not replacements for pstack's own workflows. Built-in authoring and companion control contracts need explicit adapters where their host differs.

## Preserved limitations requiring separate proof

The setup host notice points to `docs/setup.md` and the model schema. Original discovery, budget, confirmation and override decisions remain; Codex represents backend/model/effort separately. Mode starter prompts contain an explicit mention, hook context supplies authoritative session/project identity, and the CLI resolves recorded identity across worktree cwd changes. TypeScript path-trigger metadata is enforced inside active pstack workflows by the host contract; global path-trigger discovery outside the mode is not implemented.

- The source plan checker is unchanged. It has fixed ten-lane, Grok-slug, `/goal`, trunk-command and timing markers. A different Codex plan cannot honestly pass by weakening or forging them. A reviewed parameterization remains future work.
- The source worktree audit has Cursor transcript assumptions. Filesystem inspection alone does not make transcript-based liveness accurate on Codex.
- Graphite-dependent stack state, Bun/bootstrap behavior, remote/cloud placement, and watcher wake behavior retain their source implementations and prerequisites. Native Codex tools do not automatically supply those guarantees.
- Cursor-only routine/webhook/secret cards and Benny event triggers/editor flows are unsupported until real host adapters are verified. Dormant files remain intact and are not registered as slash skills or enabled.
- Source log rules contain an append-only versus audit-deletion tension; Comment Sicko has report-only wording despite comment-edit diffs; why has category-versus-MCP roster ambiguity; the TypeScript duration example permits negative values despite its prose claim. These source issues are not silently rewritten by a portability build.
- The full source, generated bytes and link targets can be verified locally. Automatic mode restoration, cross-model handoffs, workspace isolation, cancellation, and actual task results require behavioral/runtime evidence in addition to these checks.

Retain the upstream MIT copyright and permission notices. Licenses and original sources are part of the shareable repository; the generated companion directory also carries its license. This document is an adaptation record, not a claim that every preserved integration is available on every Codex host.
