#!/usr/bin/env python3
"""Optional Grok Build worker with exact-version, file-only profiles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import worker_common as wc


@dataclass(frozen=True)
class Profile:
    tools: tuple[str, ...]
    deny: tuple[str, ...]
    allow: tuple[str, ...]
    sandbox: str
    max_turns: int
    unsupported: str | None = None


PROFILES = {
    "analysis": Profile((), ("Bash", "Edit", "Read", "Grep", "MCPTool", "WebFetch", "WebSearch"), (), "read-only", 1),
    "reader": Profile(("read_file", "list_dir", "grep"), ("Bash", "Edit", "MCPTool", "WebFetch", "WebSearch"),
                      ("Read", "Grep"), "read-only", 16),
    "writer": Profile(("read_file", "list_dir", "grep", "search_replace", "write"),
                      ("Bash", "MCPTool", "WebFetch", "WebSearch"), ("Read", "Grep", "Edit"), "strict", 16,
                      "Grok writer is unsupported: inherited Edit grants can permit writes outside cwd, "
                      "including sandbox-writable temp and runtime paths"),
}
TESTED_VERSION = "grok 1.0.34 (3736acbc8658) [alpha]"
VERSION_TIMEOUT_SECONDS = 5.0
VERSION_OUTPUT_LIMIT = 4096
PATH_RULE_UNSAFE = frozenset(",()*?[]{}\\")
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
EFFORT_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")


class UnsupportedProfile(ValueError):
    pass


class CompatibilityError(UnsupportedProfile):
    def __init__(self, message: str, evidence: dict, status: str = "unsupported_profile"):
        super().__init__(message)
        self.evidence = evidence
        self.status = status


def build_env(environ: dict | None = None) -> tuple[dict, dict]:
    env = wc.validate_env(dict(os.environ if environ is None else environ))
    rejected = [name for name in ("XAI_API_KEY", "GROK_CLI_CHAT_PROXY_BASE_URL") if name in env]
    if rejected:
        raise ValueError("inherited Grok auth/routing overrides present (values not shown): " + ", ".join(rejected))
    return env, {"auth_route": "installed-cli-auth", "adapter_configures_credentials": False,
                 "checked_override_names": ["XAI_API_KEY", "GROK_CLI_CHAT_PROXY_BASE_URL"],
                 "configuration_route_not_independently_verified": True}


def build_command(spec: dict, executable: str | None = None) -> list[str]:
    normalized, _ = wc.validate_spec(spec)
    if normalized["backend"] != "grok":
        raise ValueError("backend must be grok")
    profile = PROFILES[normalized["profile"]]
    if normalized["allowed_tools"]:
        raise UnsupportedProfile("Grok profiles do not accept additional allowed_tools; Bash remains unsupported")
    for key in ("resume", "session_id"):
        if key in normalized:
            raise UnsupportedProfile(f"Grok {key} is unverified; use a new reconciled attempt")
    if not MODEL_RE.fullmatch(normalized["model"]) or not EFFORT_RE.fullmatch(normalized["effort"]):
        raise ValueError("model and effort must be plain identifiers, supplied separately")
    cwd = str(Path(normalized["cwd"]).resolve())
    if cwd == os.sep:
        raise ValueError("cwd must not resolve to the filesystem root")
    if profile.allow and any(ch in PATH_RULE_UNSAFE or ord(ch) < 32 or ord(ch) == 127 for ch in cwd):
        raise ValueError("cwd contains characters that cannot be expressed safely in a Grok permission path rule")
    if profile.unsupported:
        raise UnsupportedProfile(profile.unsupported)
    binary = executable or shutil.which("grok")
    if binary is None:
        candidate = Path.home() / ".grok/bin/grok"
        if candidate.is_file():
            binary = str(candidate)
    if binary is None:
        raise FileNotFoundError("Grok Build is not installed or not on PATH")
    command = [
        os.path.abspath(binary), "--cwd", cwd, "--prompt-file", "/dev/stdin",
        "--model", normalized["model"], "--reasoning-effort", normalized["effort"],
        "--output-format", "streaming-messages-json",
        "--tools", ",".join(profile.tools) if profile.tools else "read_file",
        "--disallowed-tools", "search_tool,use_tool" if profile.tools else "read_file,search_tool,use_tool",
        "--permission-mode", "dontAsk", "--no-subagents", "--disable-web-search",
        "--max-turns", str(profile.max_turns), "--sandbox", profile.sandbox,
    ]
    for category in profile.deny:
        command.extend(["--deny", category])
    for category in profile.allow:
        command.extend(["--allow", f"{category}({cwd})", "--allow", f"{category}({cwd}/**)"])
    return command


def check_compatibility(executable: str, env: dict, cwd: str) -> dict:
    if not sys.platform.startswith("linux"):
        raise UnsupportedProfile("Grok profiles are verified only on Linux; no sandbox fallback is available")
    output = bytearray()
    problem = None
    proc = None
    confirmed = True
    termination: dict = {}
    guard = wc._SignalGuard()
    guard.install()
    try:
        if guard.requested_signal is None:
            proc = subprocess.Popen([executable, "--version"], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd, env=env,
                                    start_new_session=True, close_fds=True, shell=False)
            deadline = time.monotonic() + VERSION_TIMEOUT_SECONDS
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                output_closed = False
                while True:
                    if guard.requested_signal is not None:
                        problem = "Grok compatibility check interrupted before prompt dispatch"
                        break
                    if time.monotonic() >= deadline:
                        problem = "Grok --version compatibility check timed out"
                        break
                    ready = selector.select(min(0.05, max(0, deadline - time.monotonic())))
                    if ready:
                        chunk = os.read(proc.stdout.fileno(), VERSION_OUTPUT_LIMIT + 1 - len(output))
                        output.extend(chunk)
                        if len(output) > VERSION_OUTPUT_LIMIT:
                            problem = "Grok --version exceeded the bounded output limit"
                            break
                        if not chunk:
                            selector.unregister(proc.stdout)
                            output_closed = True
                    if output_closed and proc.poll() is not None:
                        break
    except OSError as error:
        problem = f"Grok --version failed: {error.strerror}"
    finally:
        if proc is not None:
            confirmed = wc.terminate_process_group(proc, proc.pid, 0.5, termination, kill_wait_seconds=2.0)
            if proc.stdout is not None:
                proc.stdout.close()
        guard.restore()
    version = output.decode("utf-8", errors="replace").strip()
    observed = version if re.fullmatch(r"grok [0-9.]+ \([a-f0-9]+\) \[[a-z]+\]", version) else None
    evidence = {"tested_version": TESTED_VERSION, "observed_version": observed,
                "version_output_sha256": hashlib.sha256(output).hexdigest(),
                "returncode": proc.returncode if proc is not None else None,
                "pid": proc.pid if proc is not None else None, "pgid": proc.pid if proc is not None else None,
                "confirmed_terminated": confirmed, "termination": termination,
                "interrupt_signal": guard.requested_signal}
    if not confirmed:
        raise CompatibilityError("Grok compatibility process termination is unconfirmed; retain resource ownership",
                                 evidence, "unverified")
    if guard.requested_signal is not None:
        raise CompatibilityError("Grok compatibility check interrupted before prompt dispatch", evidence, "interrupted")
    if termination.get("term_sent") and problem is None:
        problem = "Grok --version left running processes; cleanup was required"
    if problem or proc is None or proc.returncode != 0 or version != TESTED_VERSION:
        raise CompatibilityError(problem or "Grok CLI does not match the exact tested 1.0.34 build", evidence)
    return evidence


def parse_events(events: list[dict], spec: dict) -> dict:
    profile = PROFILES.get(spec.get("profile"))
    expected = set(profile.tools) if profile else set()
    errors: list[str] = []
    models: list[str] = []
    calls: dict[str, dict] = {}
    results: dict[str, dict] = {}
    init_events: list[dict] = []
    turns: list[dict] = []
    blocks: dict[int, dict] = {}
    stream_open = False
    streamed_model = None
    streamed_reason = None
    last_text = ""
    last_reason = None
    terminal: dict | None = None
    provider_error = False
    usage_models: set[str] = set()
    if profile is None:
        errors.append("unknown Grok profile")

    def add_call(block: dict) -> None:
        call_id = block.get("id")
        if not isinstance(call_id, str) or not call_id:
            errors.append("tool call lacks an id")
            call_id = f"<missing-{len(calls)}>"
        call = {"id": block.get("id"), "name": block.get("name"), "input": block.get("input")}
        if call_id in calls:
            previous = calls[call_id]
            if previous["name"] != call["name"] or (previous["input"] and call["input"] and previous["input"] != call["input"]):
                errors.append("conflicting duplicate tool call")
            if call["input"]:
                previous["input"] = call["input"]
        else:
            calls[call_id] = call
        if block.get("type") == "server_tool_use" or not isinstance(call["name"], str) or call["name"] not in expected:
            errors.append("attempted tool outside the profile")

    def read_blocks(content: object) -> str:
        parts = []
        if not isinstance(content, list):
            errors.append("malformed message content")
            return ""
        for block in content:
            if not isinstance(block, dict):
                errors.append("malformed content block")
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif block.get("type") in {"tool_use", "server_tool_use"}:
                add_call(block)
            elif block.get("type") == "tool_result":
                if type(block.get("is_error")) is not bool:
                    errors.append("tool result lacks an explicit error status")
                call_id = block.get("tool_use_id")
                if not isinstance(call_id, str) or not call_id:
                    errors.append("tool result lacks a tool_use_id")
                elif call_id in results and results[call_id] != block:
                    errors.append("conflicting duplicate tool result")
                else:
                    results[call_id] = block
        return "".join(parts)

    def add_turn(model: object, text: str, reason: object, substantive: bool) -> None:
        nonlocal last_text, last_reason
        if substantive:
            if not isinstance(model, str) or not model:
                errors.append("response message lacks model attribution")
            elif model not in models:
                models.append(model)
        last_text = text
        last_reason = reason
        turns.append({"model": model, "stop_reason": reason, "text_chars": len(text)})

    for envelope in events:
        event = envelope.get("event") if isinstance(envelope, dict) and envelope.get("type") == "stream_event" else envelope
        if not isinstance(event, dict):
            errors.append("malformed event")
            continue
        kind = event.get("type")
        if kind not in {"system", "error"} and len(init_events) != 1:
            errors.append("inference event without one preceding init event")
        if terminal is not None and kind not in {"error", "system"}:
            errors.append("event after terminal result")
        if kind in {"content_block_start", "content_block_delta", "message_delta", "message_stop"} and not stream_open:
            errors.append("stream event outside an open message")
        if kind == "error" or (kind != "user" and event.get("is_error") is True):
            provider_error = True
            errors.append("provider reported an error")
        if kind == "system" and event.get("subtype") == "init":
            init_events.append(event)
        elif kind == "message_start":
            if stream_open:
                errors.append("message started before the previous streamed message stopped")
            stream_open = True
            blocks = {}
            message = event.get("message")
            if not isinstance(message, dict):
                errors.append("malformed message_start")
                continue
            streamed_model = message.get("model")
            streamed_reason = message.get("stop_reason")
            for index, block in enumerate(message.get("content") or []):
                if isinstance(block, dict):
                    blocks[index] = dict(block)
        elif kind == "content_block_start":
            block, index = event.get("content_block"), event.get("index")
            if not isinstance(block, dict) or type(index) is not int:
                errors.append("malformed content_block_start")
            else:
                blocks[index] = dict(block)
                if block.get("type") in {"tool_use", "server_tool_use"}:
                    add_call(block)
        elif kind == "content_block_delta":
            delta, index = event.get("delta"), event.get("index")
            if not isinstance(delta, dict) or type(index) is not int or index not in blocks:
                errors.append("content delta lacks its block")
            elif delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                blocks[index]["text"] = blocks[index].get("text", "") + delta["text"]
            elif delta.get("type") == "input_json_delta" and isinstance(delta.get("partial_json"), str):
                blocks[index]["partial_json"] = blocks[index].get("partial_json", "") + delta["partial_json"]
        elif kind == "message_delta":
            delta = event.get("delta")
            if isinstance(delta, dict) and delta.get("stop_reason") is not None:
                streamed_reason = delta["stop_reason"]
        elif kind == "message_stop":
            for block in blocks.values():
                if "partial_json" in block:
                    try:
                        block["input"] = json.loads(block.pop("partial_json"))
                    except json.JSONDecodeError:
                        errors.append("malformed tool input JSON")
            content = [blocks[index] for index in sorted(blocks)]
            text = read_blocks(content)
            add_turn(streamed_model, text, streamed_reason, bool(content))
            blocks = {}
            stream_open = False
        elif kind in {"assistant", "message", "user"}:
            message = event.get("message", event)
            if not isinstance(message, dict):
                errors.append("malformed message")
                continue
            text = read_blocks(message.get("content"))
            if kind != "user":
                add_turn(message.get("model"), text, message.get("stop_reason"), bool(message.get("content")))
        elif kind == "result":
            if terminal is not None:
                errors.append("multiple terminal results")
            terminal = event
            if isinstance(event.get("modelUsage"), dict):
                usage_models.update(event["modelUsage"])
            if event.get("subtype") != "success" or event.get("is_error") is not False:
                provider_error = True
                errors.append("unsuccessful terminal result")

    inventory_verified = len(init_events) == 1
    for init in init_events:
        inventory = init.get("tools")
        valid_tools = isinstance(inventory, list) and all(isinstance(tool, str) for tool in inventory)
        if not valid_tools or len(inventory) != len(expected) or set(inventory) != expected:
            inventory_verified = False
            errors.append("effective tool inventory does not match the profile")
        if init.get("permissionMode") != "dontAsk":
            errors.append("effective permission mode missing or not dontAsk")
        cwd = init.get("cwd")
        if not isinstance(cwd, str) or not os.path.isabs(cwd) or os.path.realpath(cwd) != os.path.realpath(spec.get("cwd", "")):
            errors.append("effective cwd missing or does not match requested cwd")
        if init.get("mcp_servers") != []:
            errors.append("effective MCP inventory missing or nonempty")
        if init.get("model") != spec.get("model"):
            errors.append("init model missing or does not match requested model")
    if len(init_events) != 1:
        errors.append("expected exactly one effective init event")
    if spec.get("profile") == "analysis":
        if not inventory_verified:
            errors.append("tool-free capability is unverified")
        if calls:
            errors.append("analysis attempted tool use")
    if terminal is None:
        errors.append("missing terminal event")
    if stream_open:
        errors.append("unterminated streamed message")
        if terminal is None and blocks:
            partial = read_blocks([blocks[index] for index in sorted(blocks)])
            add_turn(streamed_model, partial, streamed_reason, bool(blocks))
    if not models:
        errors.append("missing observed inference model")
    elif models != [spec.get("model")]:
        errors.append("observed model does not match requested model")
    reason = terminal.get("stop_reason", last_reason) if terminal else last_reason
    if not provider_error:
        for turn in turns:
            if turn["stop_reason"] not in {"end_turn", "stop_sequence", "tool_use"}:
                errors.append(f"incomplete stop reason: {turn['stop_reason']}")
        if reason not in {"end_turn", "stop_sequence"}:
            errors.append(f"incomplete stop reason: {reason}")
        if last_reason not in {"end_turn", "stop_sequence"}:
            errors.append("missing completed final assistant turn")
    result_text = terminal.get("result") if terminal else None
    if not isinstance(result_text, str):
        result_text = last_text
    if not provider_error and not result_text.strip():
        errors.append("missing result text")
    tool_errors = []
    for call_id, result in results.items():
        if call_id not in calls:
            errors.append("tool result has no matching attempted call")
        if result.get("is_error") is True:
            content = result.get("content")
            detail = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            lowered = detail.lower()
            category = "permission_denied" if "permission denied" in lowered else "cancelled" if "cancelled" in lowered else "tool_error"
            tool_errors.append({"tool_use_id": call_id, "name": calls.get(call_id, {}).get("name"),
                                "kind": category, "content_sha256": hashlib.sha256(detail.encode("utf-8")).hexdigest(),
                                "content_chars": len(detail)})
    unresolved = sorted(set(calls) - set(results))
    if unresolved and not provider_error:
        errors.append("attempted tools lack terminal tool results")
    denials = [item for item in tool_errors if item["kind"] == "permission_denied"]
    return {
        "result_text": result_text, "observed_models": models, "is_error": provider_error,
        "complete": terminal is not None, "tool_calls": list(calls.values()),
        "errors": list(dict.fromkeys(errors)),
        "warnings": [f"{len(tool_errors)} tool error(s); inspect the private raw_path before accepting the task"] if tool_errors else [],
        "evidence": {
            "tool_inventory_verified": inventory_verified,
            "tool_inventory_verified_empty": inventory_verified and not expected,
            "init": [{key: init.get(key) for key in ("model", "cwd", "permissionMode", "tools", "mcp_servers")} for init in init_events],
            "turns": turns, "usage_models": sorted(usage_models),
            "terminal_subtype": terminal.get("subtype") if terminal else None,
            "terminal_errors": terminal.get("errors", []) if terminal else [],
            "tool_errors": tool_errors, "unresolved_tool_ids": unresolved,
            "permission_denial_count": len(denials),
            "permission_denied_tools": sorted({item["name"] for item in denials if isinstance(item["name"], str)}),
            "tool_cancellation_count": sum(item["kind"] == "cancelled" for item in tool_errors),
            "partial_unterminated": terminal is None and bool(result_text),
            "sandbox_effective_not_reported": True,
        },
    }


def run(spec: dict) -> dict:
    command = build_command(spec)
    env, policy = build_env()
    compatibility = check_compatibility(command[0], env, command[command.index("--cwd") + 1])
    prompt = Path(spec["prompt_file"]).read_bytes().decode("utf-8")
    return wc.run_process(spec, command, parse_events, stdin_text=prompt, env=env,
                          adapter_evidence={"backend": "grok", "auth_policy": policy,
                                            "compatibility": compatibility, "prompt_transport": "/dev/stdin",
                                            "sandbox_requested": PROFILES[spec["profile"]].sandbox,
                                            "filesystem_containment_claimed": False})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    args = parser.parse_args(argv)
    spec = {"backend": "grok"}
    try:
        spec = json.loads(args.spec.read_text())
        receipt = run(spec)
    except CompatibilityError as error:
        receipt = wc.make_error_receipt(spec, error.status, [str(error)])
        receipt.update(confirmed_terminated=error.evidence["confirmed_terminated"],
                       adapter={"compatibility": error.evidence})
    except (ValueError, OSError) as error:
        receipt = wc.make_error_receipt(spec, "unsupported_profile" if isinstance(error, UnsupportedProfile) else "invalid_spec", [str(error)])
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        receipt = wc.make_error_receipt(spec, "internal_error", [f"{type(error).__name__}: {str(error)[:200]}"])
    print(json.dumps(receipt, ensure_ascii=False))
    return receipt["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
