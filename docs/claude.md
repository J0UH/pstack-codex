# Standalone Claude Code worker

The parent remains in Codex. A scoped worker runs through the independently installed `claude` CLI and returns a receipt; it is not a native Codex Claude model slot.

## Profiles

| Profile | Effective tools | Use |
|---|---|---|
| analysis | None | Judgment on a complete supplied source packet. |
| reader | Read, Glob, Grep | Investigation using explicitly accessible files. |
| writer | Read, Write, Edit, Glob, Grep; Bash only with scoped caller rules | A bounded implementation or repair in its assigned directory. |

All profiles request safe mode and `dontAsk`, disable session persistence, set an exact model and effort, and capture streaming JSON. Safe mode suppresses automatic customizations, so the prompt must explicitly include the appropriate pstack role/skill instructions, constraints and context. The adapter never adds `--bare`, bypass flags, API keys or gateway configuration.

Example writer `allowed_tools`: `["Bash(python3 -m unittest*)"]`. This is a permission rule, **not a security sandbox**. A broad interpreter command can do more than its name suggests. Only authorize commands appropriate to the assignment; a worktree alone does not contain filesystem, network or credential access. Reader and writer work still require the parent to inspect scope and results.

## One attempt

Use the README's spec with absolute `cwd`, `prompt_file` and a **new** `run_dir`. The adapter refuses reuse, persists launch intent before spawning, records process identity, sends the UTF-8 prompt on stdin, and captures private raw output/stderr plus a final answer and bounded JSON receipt. There is no shell interpolation of the prompt and no automatic retry.

```sh
python3 scripts/claude_worker.py --spec /absolute/path/to/spec.json --dry-run
python3 scripts/claude_worker.py --spec /absolute/path/to/spec.json
```

Dry-run validates without starting a provider or claiming the attempt. A real request succeeds only with a clean process exit, terminal success, parseable stream, the requested response model and expected effective profile. Unknown tools, unexpected tool calls, permission-mode changes, missing identity or incomplete output cannot silently pass. Auxiliary model usage is reported separately from the substantive assistant model.

The receipt's `success` means delivery succeeded. A result can be delivered while the requested project task is blocked. The parent must inspect `permission_denial_count`, findings, diff, tests and acceptance criteria; a model saying “done” is not proof.

## Cancellation and recovery

Each attempt owns a POSIX process group. Timeout or parent interruption sends TERM, then KILL if required, and checks the whole owned group rather than only its leader. A leader that exits while descendants remain triggers cleanup and an unverified result. An unconfirmed stop retains resource ownership.

Processes that deliberately start a new session can escape the group. This launcher is not an OS containment system and cannot undo external effects. Reconcile those effects before another writer uses the same resource. A clean Git tree is insufficient. Automatic session resumption is not implemented: provide a fresh consolidated brief and a new attempt after the preceding one is reconciled.

## Authentication and evidence

The adapter uses the installed CLI login. Conflicting inherited routing/authentication overrides are rejected by variable name without displaying values; model override variables are normalized explicitly. Do not copy credentials into a spec or prompt. Local raw transcripts may contain sensitive source content: keep them outside version control and publish only sanitized summaries.

Live analysis and a small writer task were exercised with Fable 5.1. The writer changed only the permitted source file and its existing tests passed on an independent rerun. Reader/other-model/other-host behavior must be checked where used. Fake subprocess fixtures cover protocol failures and cancellation but do not replace live provider proof. See [verification](verification.md).
