# Prerequisite doctor

`scripts/doctor.py` is a read-only diagnosis of the external tools pstack-codex can dispatch to: the Codex CLI (coordinator), the Claude Code CLI (core worker), the optional Grok Build CLI and the optional Grok Bot desktop app. It is not an installer, a login tool or a launcher. Core Codex and Claude work does not depend on either Grok component, and the report never lets a missing or blocked optional component change the core verdict.

```sh
python3 scripts/doctor.py
python3 scripts/doctor.py --grok-receipt /absolute/path/to/grok-run-dir
python3 scripts/doctor.py --claude-receipt /absolute/path/to/claude-run-dir/receipt.json
python3 scripts/doctor.py --skip-grok-models --timeout 10
```

## What it does and does not do

By default it runs exactly four commands, each with stdin closed under a bounded per-command timeout (default 15 s, maximum 120 s): `codex --version`, `claude --version`, `grok --version` and the read-only `grok models` listing. `--skip-grok-models` drops the last one.

It never logs in, installs, updates, changes any setting, performs inference, or makes a network call of its own. It reads no credential files. From supplied evidence it opens only the receipt file itself and a `stderr.txt` beside it; paths named inside a receipt are never opened. Output contains no environment values, account identifiers, e-mail addresses or absolute home paths. Values of known key variables, if set, are additionally erased from every string. Raw command output is never echoed; only versions and classifications are reported.

Grok Bot is detected from `/Applications/Grok Bot.app` or `~/Applications/Grok Bot.app` bundle metadata only (`--grok-bot-app` overrides the path). Nothing is launched and no process is inspected, so its sign-in state and runtime are reported as `not_checked`. Its webhook configuration is checked offline by `python3 scripts/grok_bot.py check --config <file>`, not by the doctor.

## Reading the report

Each component has separate `installed`, `auth` and, for workers, `sandbox_probe` and `inference` blocks, plus a roll-up `status`:

| Status | Meaning |
|---|---|
| `installed` | The CLI answered `--version`, or the app bundle has metadata. Nothing more. |
| `not_installed` | Not found on `PATH` (or no app bundle). For optional components this is informational. |
| `check_failed` | The version command timed out, failed or printed no recognizable version. |
| `installed_auth_unknown` | Installed; no read-only command produced a negative marker and no receipt proves inference. This is the normal state until a worker run is supplied. |
| `needs_login` | Installed; `grok models` (or a supplied receipt) printed a not-authenticated marker. Exit code 0 and printed fallback model names do not override this. Complete the ordinary interactive login yourself; the doctor never runs it. |
| `sandbox_blocked` | A supplied Grok receipt failed before inference for an environment reason (see `sandbox_probe.classification`). |
| `verified_by_supplied_receipt` | A supplied receipt is internally consistent: correct schema and backend, `status` `success`, `complete`, `lifecycle` `exited` with return code 0, every observed model equal to the requested model, no errors. This is the caller's claimed evidence, not a fresh measurement. |

`auth` is only ever `needs_login`, `unknown`, `not_checked` or `verified_by_supplied_receipt`. Codex session identity and hook trust are observed in the running Codex session, not by this tool.

`sandbox_probe` is classified only from a supplied Grok receipt. A successful inference receipt leaves sandbox enforcement `unverified`; neither success flags nor a requested sandbox argument prove the protection boundary. `sandbox_socket_symlink` is the known host failure: the read-only sandbox refused a runtime-socket deny path that is a symlink (`/var/run/docker.sock`) and exited before inference. The listed prerequisites are environment repairs; the doctor never suggests running with a downgraded or disabled sandbox. `sandbox_profile_refused`, `not_authenticated`, `unknown_option` and `unclassified` are the other outcomes.

`core.status` follows the Claude component. `optional.statuses` lists Grok Build and Grok Bot. `policy.commands_run` lists the exact commands executed. `environment.override_variables_present` lists the names of routing or key variables that are set, because the worker adapters refuse them; values are never shown. `problems` lists supplied evidence the doctor rejected.

Exit codes: 0 report produced; 1 the Claude Code CLI is missing or failed its version check, or the doctor itself failed; 2 a supplied receipt or argument was invalid. Every failure is JSON on stdout, never a traceback.

## Where receipts come from

A receipt is the `receipt.json` a worker writes into its `run_dir` (`scripts/claude_worker.py`, `scripts/grok_worker.py`). Pass the file or the directory. Supply only receipts from attempts you are entitled to disclose; the doctor summarizes their status fields and scrubbed error strings into its output.

## Limitations

The doctor cannot prove authentication positively. A verified receipt proves that one past run succeeded, not that the login is still valid. The `grok models` marker text is matched by pattern; a future CLI wording change would fall back to `unknown`, never to a positive. Tests in `tests/test_doctor.py` use injected runners, local app bundles and synthetic receipts; they do not call any provider.
