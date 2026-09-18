#!/usr/bin/env python3
"""Optional Grok Build worker. No model or permission fallback is attempted."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


class UnsupportedProfile(ValueError):
    pass


def build_env(environ: dict | None = None) -> tuple[dict, dict]:
    env = dict(os.environ if environ is None else environ)
    rejected = [name for name in ("XAI_API_KEY", "GROK_CLI_CHAT_PROXY_BASE_URL") if name in env]
    if rejected:
        raise ValueError("inherited Grok auth/routing overrides present (values not shown): " + ", ".join(rejected))
    return env, {"auth_route": "installed-cli-auth", "adapter_configures_credentials": False,
                 "checked_override_names": ["XAI_API_KEY", "GROK_CLI_CHAT_PROXY_BASE_URL"],
                 "configuration_route_not_independently_verified": True}


def build_command(spec: dict, executable: str | None = None) -> list[str]:
    if spec.get("backend") != "grok":
        raise ValueError("backend must be grok")
    if spec.get("profile") != "analysis":
        raise UnsupportedProfile("Grok reader/writer profiles have not been verified")
    if spec.get("allowed_tools", []) != []:
        raise UnsupportedProfile("Grok analysis requires an empty allowed_tools list")
    for key in ("model", "effort", "cwd", "prompt_file", "run_dir"):
        if not isinstance(spec.get(key), str) or not spec[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    for key in ("cwd", "prompt_file", "run_dir"):
        if not Path(spec[key]).is_absolute():
            raise ValueError(f"{key} must be an absolute path")
    if not Path(spec["cwd"]).is_dir():
        raise ValueError("cwd must be an existing directory")
    if not Path(spec["prompt_file"]).is_file():
        raise ValueError("prompt_file must be an existing file")
    timeout = spec.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 86400:
        raise ValueError("timeout_seconds must be greater than zero and at most 86400")
    binary = executable or shutil.which("grok")
    if binary is None:
        candidate = Path.home() / ".grok/bin/grok"
        if candidate.is_file():
            binary = str(candidate)
    if binary is None:
        raise FileNotFoundError("Grok Build is not installed or not on PATH")
    command = [
        binary,
        "--cwd", spec["cwd"],
        "--prompt-file", spec["prompt_file"],
        "--model", spec["model"],
        "--reasoning-effort", spec["effort"],
        "--output-format", "streaming-messages-json",
        "--tools", "",
        "--permission-mode", "dontAsk",
        "--no-subagents",
        "--disable-web-search",
        "--max-turns", "1",
        "--sandbox", "read-only",
    ]
    # Explicit deny rules still apply if empty --tools has broader semantics.
    for tool in ("Bash", "Edit", "Read", "Grep", "MCPTool", "WebFetch", "WebSearch"):
        command.extend(["--deny", tool])
    return command


def parse_events(events: list[dict], spec: dict) -> dict:
    """Accept complete Messages streams or envelopes; fail on missing receipts.

    These success shapes are defensive synthetic fixtures, not a claim that a
    successful Grok 1.0.34 run was observed on the development host.
    """
    models: list[str] = []
    tool_calls: list[dict] = []
    errors: list[str] = []
    provider_error = False
    text_blocks: dict[int, str] = {}
    whole_text: str | None = None
    terminal = False
    reason: str | None = None
    inventory_seen = False
    inventory_empty = False
    message_seen = False

    def note_model(value: object) -> None:
        if isinstance(value, str) and value and value not in models:
            models.append(value)

    def blocks(content: object) -> str:
        parts = []
        if not isinstance(content, list):
            return ""
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            if block.get("type") in {"tool_use", "server_tool_use"}:
                tool_calls.append(block)
        return "".join(parts)

    for envelope in events:
        if not isinstance(envelope, dict):
            errors.append("malformed event")
            continue
        event = envelope.get("event") if envelope.get("type") == "stream_event" else envelope
        if not isinstance(event, dict):
            errors.append("malformed stream event")
            continue
        kind = event.get("type")
        if kind == "error" or event.get("is_error") is True:
            provider_error = True
            errors.append("provider reported an error")
        if kind == "system" and event.get("subtype") == "init":
            # A requested model in init is configuration, not observed inference.
            if isinstance(event.get("tools"), list):
                inventory_seen = True
                inventory_empty = event["tools"] == []
                if not inventory_empty:
                    errors.append("analysis exposed a nonempty tool inventory")
        if kind == "message_start":
            message = event.get("message", {})
            if isinstance(message, dict):
                message_seen = True
                note_model(message.get("model"))
                initial = blocks(message.get("content"))
                if initial:
                    text_blocks[-1] = initial
        elif kind == "content_block_start":
            block = event.get("content_block", {})
            index = event.get("index")
            if isinstance(block, dict) and isinstance(index, int):
                if block.get("type") == "text":
                    text_blocks[index] = block.get("text", "") if isinstance(block.get("text", ""), str) else ""
                elif block.get("type") in {"tool_use", "server_tool_use"}:
                    tool_calls.append(block)
        elif kind == "content_block_delta":
            delta = event.get("delta", {})
            index = event.get("index")
            if isinstance(delta, dict) and delta.get("type") == "text_delta" and isinstance(index, int):
                value = delta.get("text")
                if isinstance(value, str):
                    text_blocks[index] = text_blocks.get(index, "") + value
        elif kind == "message_delta":
            delta = event.get("delta", {})
            if isinstance(delta, dict) and isinstance(delta.get("stop_reason"), str):
                reason = delta["stop_reason"]
        elif kind == "message_stop":
            terminal = True
        elif kind in {"assistant", "message"}:
            message = event.get("message", event)
            if isinstance(message, dict):
                message_seen = True
                note_model(message.get("model"))
                whole_text = blocks(message.get("content"))
                if isinstance(message.get("stop_reason"), str):
                    reason = message["stop_reason"]
        elif kind == "result":
            terminal = True
            if event.get("subtype") not in {None, "success"}:
                provider_error = True
                errors.append("unsuccessful terminal result")
            value = event.get("result")
            if isinstance(value, str):
                whole_text = value

    result_text = whole_text if whole_text is not None else "".join(text_blocks[i] for i in sorted(text_blocks))
    if not terminal:
        errors.append("missing terminal event")
    if reason is not None and reason not in {"end_turn", "stop_sequence"}:
        errors.append(f"incomplete stop reason: {reason}")
    if not message_seen or not models:
        errors.append("missing observed inference model")
    elif models != [spec.get("model")]:
        errors.append("observed model does not match requested model")
    if tool_calls:
        errors.append("analysis attempted tool use")
    if not inventory_seen or not inventory_empty:
        errors.append("tool-free capability is unverified")
    if not result_text.strip():
        errors.append("missing result text")
    return {
        "result_text": result_text,
        "observed_models": models,
        "is_error": provider_error,
        "complete": terminal,
        "tool_calls": tool_calls,
        "errors": list(dict.fromkeys(errors)),
        "evidence": {"tool_inventory_verified_empty": inventory_seen and inventory_empty},
    }


def run(spec: dict) -> dict:
    command = build_command(spec)
    env, policy = build_env()
    from worker_common import run_process
    return run_process(spec, command, parse_events, env=env,
                       adapter_evidence={"backend": "grok", "auth_policy": policy, "sandbox_requested": "read-only"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    args = parser.parse_args(argv)
    spec = {"backend": "grok"}
    try:
        spec = json.loads(args.spec.read_text())
        if not isinstance(spec, dict):
            raise ValueError("spec must be a JSON object")
        receipt = run(spec)
    except (ValueError, OSError) as error:
        from worker_common import make_error_receipt
        receipt = make_error_receipt(spec, "unsupported_profile" if isinstance(error, UnsupportedProfile) else "invalid_spec", [str(error)])
    print(json.dumps(receipt, ensure_ascii=False))
    if isinstance(receipt.get("exit_code"), int):
        return receipt["exit_code"]
    return 1 if receipt.get("is_error", False) or receipt.get("status") not in {None, "success"} else 0


if __name__ == "__main__":
    sys.exit(main())
