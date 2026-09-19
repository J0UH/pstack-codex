# Companion skills and built-ins

Pstack calls skills from other packages and capabilities built into Cursor. This port includes the three `cursor-team-kit` companions. You do not need to install that package separately.

| Dependency | Included in this port | How Poteto uses it |
| --- | --- | --- |
| `deslop` | [Pinned companion instructions](../companion-skills/deslop/SKILL.md) | Before committing, inspect the diff and remove unnecessary code or comments while preserving behavior. |
| `control-cli` | [Pinned companion instructions](../companion-skills/control-cli/SKILL.md) | Exercise the real command-line interface with the project's existing tools or a bounded local terminal driver. |
| `control-ui` | [Pinned companion instructions](../companion-skills/control-ui/SKILL.md) | Exercise the real interface through available browser or computer-use tools. Required capabilities such as recording still need a supported tool. |
| Cursor `create-skill` | Codex capability adaptation | Use Codex's skill-creator while retaining the calling workflow's authoring, test and feedback requirements. This is not a copied Cursor built-in. Dedicated description optimization and full authoring parity remain unverified. |
| Cursor `automate` | Host adaptation with unresolved prerequisites | Native Codex scheduling covers supported timed work. Cursor's reviewed automation-editor handoff and Slack event triggers are not supplied by copying skill files. Benny stays dormant until its actual event and connector requirements are met. |

The three companions live under `companion-skills/`. They are path-loaded dependencies, not separate Codex slash commands. The [host contract](../adapters/host.md#resolve-the-intended-skill) resolves their names to those exact files. It never substitutes a similarly named installed skill. Their instructions are present in the generated distribution and the local installed cache.

The [recorded installed-cache comparison](../evidence/companion-verification.json) was captured at commit `517e1a1`. It is historical evidence. The generator's source-hash and body-preservation checks carry those unchanged companions forward in later releases; the old record does not claim a fresh cache inspection for every subsequent commit.

Their source comes from the same pinned Cursor plugins revision as pstack, with its license retained. The generator verifies source hashes and preserves the original instruction bodies, adding the explicit Codex host notice. Run `python3 scripts/build.py --check` to verify source preservation and `python3 scripts/package.py --check` to verify the distributable copy. Those checks establish inclusion and fidelity. They do not establish every possible UI, terminal or external automation workflow.

`unslop`, `no-comments`, and `technical-writing` are already registered pstack skills. They are separate from `deslop`. Poteto retains their original triggers for prose, comment review, and technical documents.
