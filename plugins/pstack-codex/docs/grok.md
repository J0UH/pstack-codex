# Optional Grok Build worker

Grok is an external CLI worker supervised by the parent agent. It is not a native Codex Grok subagent, and it is not the default backend. No API key, gateway, authentication or global configuration change is required by this adapter.

The local capability probe on 2026-09-18 found Grok Build `1.0.34 (3736acbc8658)` with existing grok.com authentication. `grok models` listed `grok-4.6` and `grok-4.5`. The upstream Cursor slug `grok-4.6-fast-xhigh` is not one of those CLI model IDs. The adapter passes the requested model and reasoning effort separately and never substitutes another model.

## Current capability

The production adapter remains an **unready candidate**. The Mac sandbox startup failure and the separate Linux CLI capability proofs must not be conflated. On the Mac, a synthetic headless request in a disposable directory failed before any inference event with exit code 1:

```text
warning: sandbox could not be applied: socket deny resolution failed: could not resolve runtime-socket deny path /var/run/docker.sock: endpoint is a symlink
error: could not apply the 'read-only' sandbox profile; see the warning above for the cause. Refusing to start with its protections missing.
```

No sandbox downgrade, Docker change or retry without protections was performed. That Mac attempt has no observed response-model identity or successful result. The private evidence is under `.local/grok-probe/` and is not a portable test fixture.

`analysis` is the only implemented candidate profile. It currently passes `--tools ""`, `dontAsk`, disabled subagents/web search, one turn and the read-only sandbox, plus seven deny rules. **A real Linux run proved that the empty `--tools` argument restores the default tool inventory.** The parser correctly rejects that result; zero calls and a correct answer do not establish tool freedom. The production launch controls therefore need correction before this backend is ready. The parser requires an explicit empty runtime tool inventory, an observed inference model matching the request, a terminal event, answer text and no tool call or provider error.

Separate, supervised Linux probes used the official Grok Build 1.0.34 binary as a temporary sidecar and the existing CLI login. The pre-existing global 1.0.5 installation was not replaced. The corrected analysis launch used `--tools read_file --disallowed-tools read_file,search_tool,use_tool`, keeping all seven deny rules and the read-only sandbox. It returned the expected public sentinel, exact `grok-4.6` attribution, an empty tool inventory, zero calls and a complete receipt with confirmed process cleanup. A file-reader capability probe also passed with exactly `read_file,list_dir,grep`. These are capability proofs with experimental launch controls, not acceptance of the unchanged production adapter. [Probe evidence](../evidence/grok-capability-probes.json).

The observed behavior agrees with the pinned public implementation: an empty list becomes no override, unknown allowlist entries can retain default tools, and `search_tool`/`use_tool` need explicit exclusion. Do not guess a `none` tool name or wildcard deny list. [CLI parsing](https://github.com/xai-org/grok-build/blob/a28ee2b2063426e8816e380ccea528b9de95e5da/crates/codegen/xai-grok-pager/src/headless/cli.rs#L133), [tool selection](https://github.com/xai-org/grok-build/blob/a28ee2b2063426e8816e380ccea528b9de95e5da/crates/codegen/xai-grok-agent/src/builder.rs#L943).

A separate writer capability probe completed an inside edit with the exact five file tools and `strict` sandbox. Two negative attempts left both the sibling file and an outside symlink target unchanged. The sibling attempt ended with a provider cancellation; the symlink attempt returned an OS permission-denied tool result before a successful terminal response. These are different outcomes, both retained in the [selected actual event shapes](../evidence/grok-capability-events.json). Writer prompt transport also needs care: `strict` refused an external prompt file, so the synthetic probe used an unchanged copy inside its disposable cwd while retaining the original in disjoint evidence. This does not establish a general production prompt-transport solution.

`reader` and `writer`, and any nonempty `allowed_tools`, return `unsupported_profile` before launch. They need separate live verification before being enabled. The implementation never claims that a task prompt, working directory, allowlist, or requested sandbox proves filesystem containment. Grok's documented read-only sandbox still permits reads outside the workspace and writes to its session storage and temporary directories; platform network limitations also apply. [Sandbox documentation](https://docs.x.ai/build/features/sandbox).

## Invocation

Create a JSON spec with absolute paths:

```json
{
  "backend": "grok",
  "model": "grok-4.6",
  "effort": "low",
  "profile": "analysis",
  "cwd": "/absolute/path/to/disposable-workspace",
  "prompt_file": "/absolute/path/to/prompt.txt",
  "run_dir": "/absolute/path/to/unique-run-directory",
  "timeout_seconds": 120,
  "allowed_tools": []
}
```

Run `python3 scripts/grok_worker.py --spec /absolute/path/to/spec.json` from this plugin directory. The shared `worker_common.run_process` owns bounded process execution, stderr/raw JSONL capture and the receipt in `run_dir`. There is no automatic retry, resume, install, login or permission fallback. Analysis prompts should contain the bounded material needed for judgment; do not dispatch private repository content merely to test connectivity.

The current, not-yet-corrected production control arguments are recorded below for diagnosis. They are not a working recipe:

```text
--prompt-file <file> --cwd <directory>
--model <model> --reasoning-effort <effort>
--output-format streaming-messages-json
--tools "" --permission-mode dontAsk
--no-subagents --disable-web-search --max-turns 1
--sandbox read-only
--deny Bash --deny Edit --deny Read --deny Grep
--deny MCPTool --deny WebFetch --deny WebSearch
```

The Fable review follow-up reran this exact argument list, including all seven denies, through the common launcher. It reached the same sandbox error rather than an unknown-option error. This confirms argument acceptance on this host, not successful inference or tool-free semantics. Pre-launch errors now use the same receipt schema and `errors` array as Claude; `unsupported_profile` exits with code 2.

The child receives an explicit inherited environment and an auth-policy record. Known `XAI_API_KEY` and `GROK_CLI_CHAT_PROXY_BASE_URL` overrides are rejected by name without exposing values. The adapter does not configure credentials, and it does not independently attest the route selected by the installed CLI's own configuration.

The installed help, rather than a guessed flag, confirmed `streaming-messages-json` for Messages-format NDJSON and `streaming-json` for ACP updates. The adapter requests the former only. Official CLI guidance recommends checking installed help for the complete flag set. [CLI reference](https://docs.x.ai/build/cli/reference). Headless sessions persist through the installed Grok CLI and use its existing authentication; this wrapper does not manage either. [Headless scripting](https://docs.x.ai/build/cli/headless-scripting).

## Verification and limitations

Run `python3 -m unittest discover -s tests -p test_grok_worker.py`. Tests cover the real empty-event startup failure, clearly labeled synthetic stream reconstruction, model mismatch, missing terminal/model/tool-inventory evidence, provider errors, truncation, tool calls, supported controls and unsupported profiles. Synthetic success cases validate parser logic only. They are not evidence that the selected Grok version emits that protocol or honors an empty tool list.

Before enabling this backend as a regular role choice, implement the verified launch controls with a version compatibility gate and real-stream regression tests, then repeat the production-adapter proof. Reader and writer each need their own tool, permission and filesystem-boundary acceptance. The Mac still needs a supported sandbox environment without weakening restrictions; the working Linux computer does not automatically authorize transferring a private project there. CLI permission gates and OS sandbox restrictions are distinct controls. [Permission documentation](https://docs.x.ai/build/features/permissions).

Fable's implementation attempt for these changes stopped at the Claude session limit before any tool call or edit. Its earlier scoped approval does not cover the newly proposed Grok profiles. The exact capability evidence and implementation brief are retained for the next Fable pass. Scoped shell execution remains unsupported: inherited permission grants can broaden CLI allow rules, so a narrow-looking rule alone is insufficient.
