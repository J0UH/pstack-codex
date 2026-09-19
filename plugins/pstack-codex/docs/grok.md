# Optional Grok Build worker

Grok Build is an optional external CLI worker supervised by Codex. Ordinary Claude-backed workflows do not require it. Grok Bot is a separate integration described in [the Bot adapter](grok-bot.md).

The adapter implements analysis and file-reader profiles for the tested Linux `grok 1.0.34 (3736acbc8658) [alpha]` build. Both passed real checks through the production adapter's public CLI. These results do not establish support on other platforms or versions. [Production acceptance evidence](../evidence/grok-adapter-acceptance.json).

## Profiles and authority

| Profile | Effective tool inventory | Sandbox requested | Turn limit | Dispatch |
|---|---|---|---|---|
| analysis | Empty | `read-only` | 1 | Linux 1.0.34 only |
| reader | `read_file`, `list_dir`, `grep` | `read-only` | 16 | Linux 1.0.34 only |
| writer | Candidate inventory includes `search_replace` and `write` | Candidate requires `strict` | 16 | Unsupported |

Analysis receives all material needed for judgment in its prompt. The reader adds absolute `Read` and `Grep` permission rules for resolved cwd and its descendants. Paths with permission delimiters, glob syntax, or control characters are rejected. Filesystem-root cwd and overlapping attempt directories are rejected. The adapter also rejects session resumption and additional `allowed_tools`, including scoped Bash.

The reader is **not confined to reading cwd**. Inherited grants can permit broader reads, and Grok's `read-only` OS sandbox reads broadly. Reader dispatch is appropriate only when that read authority is acceptable for the host and assignment. The adapter preserves inherited rules and does not inspect or copy credentials, isolate HOME, or change security configuration.

Writer dispatch fails before any CLI invocation. Grok merges CLI permission rules with inherited native, Claude, managed, and requirements policies. A narrow `Edit(cwd/**)` allow rule does not remove broader inherited grants. Even `strict` permits writes to temporary and runtime directories, and managed policy can change the effective sandbox. The captured writer probes demonstrate particular permitted and denied operations on their inspected host. They do not prove an exclusive portable cwd boundary. Bash remains unsupported for the same inherited-grant problem. [Permission documentation](https://docs.x.ai/build/features/permissions), [sandbox documentation](https://docs.x.ai/build/features/sandbox).

Every dispatched profile requests `dontAsk`, disables subagents and web search, and excludes `search_tool` and `use_tool`. Analysis also excludes its seed `read_file` tool. This nonempty recognized seed is necessary because empty `--tools` restores default exposure, and unknown allowlist entries can also retain defaults. Wildcard deny names and invented `none` tools are not supported. [Pinned CLI parser](https://github.com/xai-org/grok-build/blob/a28ee2b2063426e8816e380ccea528b9de95e5da/crates/codegen/xai-grok-pager/src/headless/cli.rs#L133), [pinned tool selection](https://github.com/xai-org/grok-build/blob/a28ee2b2063426e8816e380ccea528b9de95e5da/crates/codegen/xai-grok-agent/src/builder.rs#L943).

## Invocation and compatibility

The JSON spec uses absolute paths and a new attempt directory outside cwd.

```json
{
  "backend": "grok",
  "model": "grok-4.6",
  "effort": "low",
  "profile": "analysis",
  "cwd": "/absolute/path/to/disposable-workspace",
  "prompt_file": "/absolute/path/to/prompt.txt",
  "run_dir": "/absolute/path/to/unique-attempt",
  "timeout_seconds": 120,
  "allowed_tools": []
}
```

The command is `python3 scripts/grok_worker.py --spec /absolute/path/to/spec.json`. Model and effort are independent arguments. The observed CLI inventory listed `grok-4.6` and `grok-4.5`; the upstream Cursor slug `grok-4.6-fast-xhigh` is not a CLI model ID. The adapter never substitutes another model or treats a usage-accounting identifier such as `grok-4.6-build` as substantive inference identity.

Before claiming the task attempt or dispatching its prompt, the adapter runs `grok --version` with no stdin. The check permits five seconds and at most 4096 output bytes, owns a process group, and confirms cleanup. The exact tested version, build, and channel must match. Unknown versions, unsupported platforms, failed checks, or unconfirmed cleanup stop dispatch. Compatibility evidence records the observed version and process identity, including failed checks. This is a compatibility gate for a trusted installed executable, not cryptographic binary attestation or protection against concurrent executable replacement.

The task prompt is decoded as UTF-8 and passed through the common supervisor's `stdin_text` to `--prompt-file /dev/stdin`. Prompt bytes are absent from argv, and no project copy is created. A Linux 1.0.34 capability probe verified this transport under `strict`, with matching prompt and stdin hashes and unchanged files. The original prompt remains available for supervisor hashing. The supplied `prompt_file` must remain unchanged for the attempt.

The analysis control arguments are:

```text
--cwd <resolved-directory> --prompt-file /dev/stdin
--model <exact-model> --reasoning-effort <effort>
--output-format streaming-messages-json
--tools read_file --disallowed-tools read_file,search_tool,use_tool
--permission-mode dontAsk --no-subagents --disable-web-search
--max-turns 1 --sandbox read-only
--deny Bash --deny Edit --deny Read --deny Grep
--deny MCPTool --deny WebFetch --deny WebSearch
```

Reader uses `--tools read_file,list_dir,grep`, `--disallowed-tools search_tool,use_tool`, and 16 turns. It retains the Bash, Edit, MCPTool, WebFetch, and WebSearch denies. It replaces the Read and Grep denies with explicit cwd and descendant allow rules. These rules preserve all inherited deny and ask restrictions. They are not exclusive read authority.

The child receives the inherited environment. Known `XAI_API_KEY` and `GROK_CLI_CHAT_PROXY_BASE_URL` overrides are rejected by name without displaying values. Existing CLI authentication remains in place, and the adapter does not independently attest its configured authentication route. There is no login, installation, configuration change, automatic retry, or permission fallback. [CLI reference](https://docs.x.ai/build/cli/reference), [headless scripting](https://docs.x.ai/build/cli/headless-scripting).

Grok also loads inherited user-level skills and slash commands. The adapter does not isolate that context. Receipts record the CLI-reported `apiKeySource` and the skill/command counts as observations, without treating them as independent authentication or isolation proof. A missing binary or rejected routing override reports `unsupported_profile`; malformed specifications report `invalid_spec`.

## Receipts and completion

`worker_common.run_process` owns task execution, timeout, signal handling, process-group cleanup, restricted artifacts, and the receipt. Its cancellation and recovery contract is described in [the Claude worker lifecycle](claude.md#cancellation-and-recovery). An unconfirmed stop retains resource ownership and prevents a verified success.

Success requires exactly one effective init event with the expected tool inventory, `dontAsk`, matching cwd and model, and explicitly empty MCP inventory. It also requires matching substantive response-model attribution, a complete final assistant turn, a successful terminal result, result text, and no protocol or provider error. Missing metadata is not proof. Requested sandbox flags are recorded separately because the stream does not attest effective sandbox roots. No filesystem-containment claim is made from argv or cwd alone.

The parser preserves each attempted tool and its input, including denied attempts. Multi-turn `tool_use` stops are intermediate, and streamed call duplicates are counted once. A `message_stop` without a final `result` is incomplete. Truncation, an unexpected tool, missing tool results, missing model identity, or a changed inventory makes the receipt unverified. Partial text remains available for diagnosis.

Delivery success is distinct from task completion and acceptance of a negative boundary test. The captured sibling-write attempt ended with `error_during_execution` and cancellation. Its receipt has a terminal event and a provider error. The symlink-write attempt produced an OS permission-denied tool result, then a successful response. That denial is preserved in `tool_errors`, `permission_denial_count`, and warnings without changing successful delivery. Public `tool_errors` contain classification, call identity, and a content hash. The payload stays in the private raw stream. Cancellation is counted separately because its text alone does not establish a permission denial. The caller must inspect the result, attempted calls, errors, file changes, and acceptance criteria.

## Evidence and remaining limits

The original Mac probe failed before inference because `/var/run/docker.sock` was a symlink that the read-only sandbox refused. No Docker change or sandbox downgrade was made. The adapter now rejects non-Linux dispatch before starting the CLI. Linux evidence does not establish Mac support or authorize transferring private projects to another host.

The supervised Linux capability probes used the official 1.0.34 sidecar with existing authentication. The global 1.0.5 installation remained unchanged. Analysis proved empty inventory and zero calls. Reader proved real read, list, and grep calls with an exact file nonce and unchanged fixture. Writer demonstrated an inside change and two unchanged outside targets, but remains gated for inherited-grant safety. [Capability evidence](../evidence/grok-capability-probes.json), [selected denial events](../evidence/grok-capability-events.json).

The final production checks invoked `scripts/grok_worker.py --spec` on Linux. Analysis returned the exact public sentinel with no tools. Reader performed one read, one directory listing and one search, recovered a nonce supplied only in a file, and left the fixture unchanged. Both verified exact Grok 4.6 attribution, matching prompt and stdin hashes, and process cleanup. The actual older global CLI was also rejected before the task attempt was claimed. The sidecar and source hashes stayed unchanged. [Production acceptance evidence](../evidence/grok-adapter-acceptance.json).

CI compares the current worker source hashes with the recorded production acceptance. An edit to either worker invalidates that claim until its evidence is refreshed. The separate experimental capability captures remain historical records.

`python3 -m unittest discover -s tests -p test_grok_worker.py` checks the captured complete streams, labeled synthetic negative mutations, stdin transport through the real common supervisor, version refusal, bounded compatibility cleanup, and error receipts. [Fixture provenance](../tests/fixtures/grok/provenance.json) records source hashes and sanitization. Fake CLI executions verify adapter behavior only. Fable 5.1 approved the exact reviewed implementation within its documented scope. See the [review record](integration-review.md). This adapter does not claim all-platform support or full Grok coding-workflow parity.
