# Optional Grok Build worker

Grok is an external CLI worker supervised by the parent agent. It is not a native Codex Grok subagent, and it is not the default backend. No API key, gateway, authentication or global configuration change is required by this adapter.

The local capability probe on 2026-09-18 found Grok Build `1.0.34 (3736acbc8658)` with existing grok.com authentication. `grok models` listed `grok-4.6` and `grok-4.5`. The upstream Cursor slug `grok-4.6-fast-xhigh` is not one of those CLI model IDs. The adapter passes the requested model and reasoning effort separately and never substitutes another model.

## Current capability

Live inference is **blocked on this host**. A synthetic headless request in a disposable directory failed before any inference event with exit code 1:

```text
warning: sandbox could not be applied: socket deny resolution failed: could not resolve runtime-socket deny path /var/run/docker.sock: endpoint is a symlink
error: could not apply the 'read-only' sandbox profile; see the warning above for the cause. Refusing to start with its protections missing.
```

No sandbox downgrade, Docker change or retry without protections was performed. There is no observed response-model identity or successful result to report. The private evidence is under `.local/grok-probe/` and is not a portable test fixture.

`analysis` is the only implemented candidate profile. It requests an empty built-in tool list, `dontAsk`, disabled subagents/web search, one turn and the read-only sandbox. Explicit deny rules cover the seven tool filters documented by Grok. These supplement the empty list; the live probe itself used only the MCP deny. **Empty `--tools ""` semantics remain unverified** because startup failed first. The parser will not report success without an explicit empty runtime tool inventory, an observed inference model matching the request, a terminal event, answer text and no tool call or provider error. If a future real stream uses another event shape, adapt against a captured sanitized stream; do not silently infer success from exit code zero.

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

The exact control arguments are:

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

The installed help, rather than a guessed flag, confirmed `streaming-messages-json` for Messages-format NDJSON and `streaming-json` for ACP updates. The adapter requests the former only. Official CLI guidance recommends checking installed help for the complete flag set. [CLI reference](https://docs.x.ai/build/cli/reference). Headless sessions persist through the installed Grok CLI and use its existing authentication; this wrapper does not manage either. [Headless scripting](https://docs.x.ai/build/cli/headless-scripting).

## Verification and limitations

Run `python3 -m unittest discover -s tests -p test_grok_worker.py`. Tests cover the real empty-event startup failure, clearly labeled synthetic stream reconstruction, model mismatch, missing terminal/model/tool-inventory evidence, provider errors, truncation, tool calls, supported controls and unsupported profiles. Synthetic success cases validate parser logic only. They are not evidence that the selected Grok version emits that protocol or honors an empty tool list.

Before enabling this backend as a regular role choice, repair the environment without weakening its restrictions and repeat the disposable probe. Capture the actual response identity and event schema, prove tool-free semantics, update sanitized fixtures, and verify the receipt through the common runner. Until then, report the live capability as blocked. CLI permission gates and OS sandbox restrictions are distinct controls. [Permission documentation](https://docs.x.ai/build/features/permissions).
