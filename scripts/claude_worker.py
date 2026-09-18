#!/usr/bin/env python3
"""Claude Code CLI worker adapter for pstack-codex.

Runs the locally installed, standalone ``claude`` binary in print mode with
``--output-format stream-json`` under one of three profiles, using the common
lifecycle in :mod:`worker_common`.  The adapter validates every input first,
then builds argv, then spawns.  It never adds API keys, gateways, ``--bare`` or
permission-bypass flags: authentication is whatever the installed CLI already
has (normally the user's subscription login).

Usage::

    python3 scripts/claude_worker.py --spec SPEC.json
    python3 scripts/claude_worker.py --spec SPEC.json --dry-run   # validate + show argv, no spawn

Exit code 0 only for a delivered, complete, error-free result attributed to
the requested model.  Semantic acceptance of the answer stays with the caller.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import traceback
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import worker_common as wc  # noqa: E402

BACKEND = "claude"
CLAUDE_EXECUTABLE = "claude"
PERMISSION_MODE = "dontAsk"
OUTPUT_FORMAT = "stream-json"

PROFILE_TOOLS: dict[str, tuple[str, ...]] = {
    "analysis": (),
    "reader": ("Read", "Glob", "Grep"),
    "writer": ("Read", "Write", "Edit", "Glob", "Grep"),
}

# Characters that would change the meaning of a permission path pattern: rule
# delimiters, the allow-list separator, glob metacharacters and the glob escape.
EDIT_RULE_UNSAFE_CHARS = frozenset(",()*?[]{}\\")

# Inherited variables that would change the auth route or API endpoint.  Presence is an error;
# values are never read into messages or receipts.
REJECTED_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "AWS_BEARER_TOKEN_BEDROCK",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
    "CLAUDE_CODE_SKIP_VERTEX_AUTH",
)

# Inherited model-selection overrides.  They are removed from the child environment (the spec's
# ``--model`` is authoritative) and their NAMES are recorded in the receipt.
STRIPPED_ENV = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
EFFORT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
BASH_RULE_RE = re.compile(r"^Bash\((?P<body>.*)\)$")

SpecError = wc.SpecError


# --------------------------------------------------------------------------- validation


def is_scoped_bash_rule(rule: str) -> bool:
    """True for ``Bash(<command prefix>:*)`` or ``Bash(<exact command>)`` with a real command token."""
    match = BASH_RULE_RE.match(rule)
    if not match:
        return False
    body = match.group("body").strip()
    if not body or body in ("*", ":*") or body.startswith("*") or body.startswith(":"):
        return False
    if any(ch in body for ch in (",", "\n", "\r", "\x00", "(", ")")):
        return False
    command = body.split()[0].removesuffix(":*")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", command):
        return False
    return True


def edit_scope_rule(cwd: str) -> str:
    """Return the writer's file-tool rule anchored at the resolved absolute cwd, or raise SpecError.

    A ``//`` prefix anchors a permission path pattern at the filesystem root, so the
    rule does not depend on how the CLI derives its project root from the launch
    directory. Under Claude Code's documented semantics one Edit rule governs the
    built-in file-editing tools, Write included, and ``Write(path)`` rules are not
    enforced, so no such rule is emitted. This is a permission rule, not an OS
    boundary: separately allowed shell commands are not contained by it.
    """
    resolved = os.path.realpath(cwd)
    if resolved == os.sep:
        raise SpecError("writer cwd must not resolve to the filesystem root")
    unsafe = sorted({ch for ch in resolved if ch in EDIT_RULE_UNSAFE_CHARS or ord(ch) < 32 or ch == "\x7f"})
    if unsafe:
        raise SpecError(
            "writer cwd resolves to a path containing characters that cannot be expressed safely in a "
            "permission path rule: " + " ".join(repr(ch) for ch in unsafe)
        )
    return f"Edit(/{resolved}/**)"


def resolve_tools(spec: dict) -> tuple[list[str], list[str]]:
    """Return ``(tools, allowed_tools)`` for the profile or raise SpecError.

    ``tools`` feeds ``--tools`` (the built-in tool set exposed to the model);
    ``allowed_tools`` feeds ``--allowedTools`` (permission rules, no prompting under dontAsk).
    """
    profile = spec["profile"]
    requested = list(spec.get("allowed_tools") or [])
    base = list(PROFILE_TOOLS[profile])
    if profile == "analysis":
        if requested:
            raise SpecError("analysis profile does not accept allowed_tools")
        return [], []
    bash_rules: list[str] = []
    for rule in requested:
        if rule in base:
            continue
        if is_scoped_bash_rule(rule):
            if rule not in bash_rules:
                bash_rules.append(rule)
            continue
        raise SpecError(
            f"{profile} profile cannot allow tool rule {rule!r}; only its named file tools "
            "and scoped Bash(<command>:*) rules are accepted"
        )
    tools = base + (["Bash"] if bash_rules else [])
    allowed = ["Read", "Glob", "Grep", edit_scope_rule(spec["cwd"])] if profile == "writer" else base
    return tools, allowed + bash_rules


def validate_claude_spec(spec: Any) -> tuple[dict, list[str]]:
    normalized, warnings = wc.validate_spec(spec)
    if normalized["backend"] != BACKEND:
        raise SpecError("backend must be 'claude' for this adapter")
    if not MODEL_RE.match(normalized["model"]):
        raise SpecError("model must be a plain model identifier (letters, digits, '.', '_', ':', '-')")
    if not EFFORT_RE.match(normalized["effort"]):
        raise SpecError("effort must be a lowercase token such as low, medium, high, xhigh")
    for key in ("resume", "session_id"):
        if key in normalized:
            raise SpecError(f"{key} is not supported by the claude adapter (session resume is untested)")
    resolve_tools(normalized)
    return normalized, warnings


def build_env(environ: Any) -> tuple[dict[str, str], dict[str, Any]]:
    """Copy the inherited environment, rejecting auth/route overrides and stripping model overrides."""
    environ = wc.validate_env(dict(environ))
    rejected = sorted(name for name in REJECTED_ENV if name in environ)
    if rejected:
        raise SpecError(
            "inherited auth/routing overrides present (values not shown): " + ", ".join(rejected)
        )
    stripped = sorted(name for name in STRIPPED_ENV if name in environ)
    env = {key: value for key, value in environ.items() if key not in STRIPPED_ENV}
    policy = {
        "auth_route": "installed-cli-login",
        "rejected_if_present": list(REJECTED_ENV),
        "stripped_model_overrides": stripped,
    }
    return env, policy


def resolve_claude_binary(path: str | None) -> str:
    found = shutil.which(CLAUDE_EXECUTABLE, path=path)
    if not found:
        raise SpecError("claude CLI not found on PATH")
    return os.path.abspath(found)


def read_prompt(prompt_file: str) -> str:
    with open(prompt_file, "rb") as handle:
        data = handle.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SpecError("prompt_file must be UTF-8 text") from exc
    if not text.strip():
        raise SpecError("prompt_file is empty")
    return text


def build_argv(claude_bin: str, spec: dict) -> list[str]:
    tools, allowed = resolve_tools(spec)
    argv = [
        claude_bin,
        "-p",
        "--model",
        spec["model"],
        "--effort",
        spec["effort"],
        "--safe-mode",
        "--permission-mode",
        PERMISSION_MODE,
        "--output-format",
        OUTPUT_FORMAT,
        "--verbose",
        "--no-session-persistence",
        "--tools",
        ",".join(tools),
    ]
    if allowed:
        argv.append("--allowedTools")
        argv.extend(allowed)
    return argv


# --------------------------------------------------------------------------- stream parsing


def _init_tool_names(raw: Any) -> tuple[list[str] | None, str | None]:
    """Tolerated init ``tools`` representations: list of names, list of {"name": ...}, or one comma/space string."""
    if isinstance(raw, str):
        return [name for name in re.split(r"[,\s]+", raw) if name], None
    if isinstance(raw, list):
        names: list[str] = []
        for item in raw:
            if isinstance(item, str):
                if item:
                    names.append(item)
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append(item["name"])
            else:
                return None, "init tools list contains an unrecognized entry"
        return names, None
    return None, "init tools field is neither a list nor a string"


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def parse_claude_events(events: list[dict], spec: dict) -> dict:
    """Turn the stream-json events into the parser contract expected by worker_common."""
    requested = spec["model"]
    expected_tools = set(resolve_tools(spec)[0])
    errors: list[str] = []
    warnings: list[str] = []

    init: dict | None = None
    assistant_models: list[str] = []
    assistant_count = 0
    missing_model = 0
    subagent_messages = 0
    tool_calls: list[dict] = []
    last_text: str | None = None
    result: dict | None = None
    result_count = 0
    type_counts: dict[str, int] = {}

    for event in events:
        kind = event.get("type")
        key = kind if isinstance(kind, str) else "<missing>"
        type_counts[key] = type_counts.get(key, 0) + 1
        if kind == "system" and event.get("subtype") == "init":
            if init is None:
                init = event
            else:
                warnings.append("multiple init events observed")
        elif kind == "assistant":
            assistant_count += 1
            message = event.get("message")
            if not isinstance(message, dict):
                errors.append("assistant event without a message object")
                missing_model += 1
                continue
            model = message.get("model")
            if isinstance(model, str) and model:
                if model not in assistant_models:
                    assistant_models.append(model)
            else:
                missing_model += 1
            if event.get("parent_tool_use_id"):
                subagent_messages += 1
            content = message.get("content")
            if isinstance(content, list):
                texts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        tool_calls.append(
                            {
                                "name": block.get("name") if isinstance(block.get("name"), str) else None,
                                "id": block.get("id") if isinstance(block.get("id"), str) else None,
                                "parent_tool_use_id": event.get("parent_tool_use_id"),
                            }
                        )
                    elif block.get("type") == "text" and isinstance(block.get("text"), str):
                        texts.append(block["text"])
                if texts:
                    last_text = "".join(texts)
        elif kind == "result":
            result = event
            result_count += 1

    evidence: dict[str, Any] = {
        "event_type_counts": type_counts,
        "assistant_message_count": assistant_count,
        "assistant_messages_missing_model": missing_model,
        "subagent_message_count": subagent_messages,
        "result_event_count": result_count,
    }

    if init is None:
        errors.append("init event missing; effective tools and permission mode are unknown")
    else:
        init_model = init.get("model")
        evidence["init_model"] = init_model if isinstance(init_model, str) else None
        if init_model != requested:
            errors.append(f"init model {init_model!r} differs from requested {requested!r}")
        permission_mode = init.get("permissionMode")
        evidence["init_permission_mode"] = permission_mode if isinstance(permission_mode, str) else None
        if permission_mode != PERMISSION_MODE:
            errors.append(f"init permission mode {permission_mode!r} is not {PERMISSION_MODE!r}")
        names, problem = _init_tool_names(init.get("tools"))
        if names is None:
            errors.append(f"init tools unparseable: {problem}")
            evidence["init_tools"] = None
        else:
            evidence["init_tools"] = names
            observed = set(names)
            missing = sorted(expected_tools - observed)
            unexpected = sorted(observed - expected_tools)
            if missing:
                errors.append("init tools missing expected: " + ", ".join(missing))
            if unexpected:
                errors.append("init tools include tools outside this profile: " + ", ".join(unexpected))
        mcp_servers = init.get("mcp_servers")
        evidence["init_mcp_server_count"] = len(mcp_servers) if isinstance(mcp_servers, list) else None
        if isinstance(mcp_servers, list) and mcp_servers:
            errors.append("init reports MCP servers; safe-mode should have disabled them")
        api_key_source = init.get("apiKeySource")
        evidence["api_key_source"] = api_key_source if isinstance(api_key_source, str) else None
        if api_key_source is None:
            warnings.append("init did not report apiKeySource")
        elif api_key_source != "none":
            errors.append(f"auth route unexpected: apiKeySource={api_key_source!r} (expected 'none' for CLI login)")
        for key, name in (("session_id", "session_id"), ("claude_code_version", "claude_code_version"), ("cwd", "init_cwd")):
            value = init.get(key)
            evidence[name] = value if isinstance(value, str) else None
        init_cwd = init.get("cwd")
        if isinstance(init_cwd, str) and os.path.realpath(init_cwd) != os.path.realpath(spec["cwd"]):
            errors.append("init cwd differs from spec cwd")

    complete = result is not None
    is_error = False
    result_text = ""
    auxiliary: list[str] = []
    if result is not None:
        if result_count > 1:
            warnings.append(f"{result_count} result events observed; using the last")
        subtype = result.get("subtype")
        evidence["result_subtype"] = subtype if isinstance(subtype, str) else None
        is_error = bool(result.get("is_error")) or subtype != "success"
        raw_text = result.get("result")
        if isinstance(raw_text, str):
            result_text = raw_text
            evidence["result_text_source"] = "result.result"
        elif last_text is not None:
            result_text = last_text
            evidence["result_text_source"] = "last_assistant_text"
        else:
            evidence["result_text_source"] = None
        usage = result.get("modelUsage")
        providers: dict[str, Any] = {}
        if isinstance(usage, dict):
            for model_name, detail in usage.items():
                provider = detail.get("provider") if isinstance(detail, dict) else None
                providers[str(model_name)] = provider if isinstance(provider, str) else None
                if str(model_name) not in assistant_models:
                    auxiliary.append(str(model_name))
            odd = sorted(name for name, provider in providers.items() if provider not in (None, "firstParty"))
            if odd:
                errors.append("model usage reports a non-firstParty provider for: " + ", ".join(odd))
        evidence["model_usage_providers"] = providers
        denials = result.get("permission_denials")
        if isinstance(denials, list):
            evidence["permission_denial_count"] = len(denials)
            evidence["permission_denied_tools"] = _unique(
                [d.get("tool_name") for d in denials if isinstance(d, dict) and isinstance(d.get("tool_name"), str)]
            )[:20]
        else:
            evidence["permission_denial_count"] = 0
        num_turns = result.get("num_turns")
        evidence["num_turns"] = num_turns if isinstance(num_turns, int) else None
    if missing_model:
        errors.append(f"{missing_model} assistant message(s) carried no model attribution")
    if result is None and last_text is not None:
        result_text = last_text
        evidence["result_text_source"] = "partial_assistant_text"
        evidence["partial_chars"] = len(last_text)
        evidence["partial_unterminated"] = True
    unexpected_calls = sorted({str(call.get("name")) for call in tool_calls if call.get("name") not in expected_tools})
    if unexpected_calls:
        errors.append("assistant invoked tools outside this profile: " + ", ".join(unexpected_calls))

    return {
        "result_text": result_text,
        "observed_models": assistant_models,
        "is_error": is_error,
        "complete": complete,
        "auxiliary_models": _unique(auxiliary),
        "tool_calls": tool_calls,
        "errors": errors,
        "warnings": warnings,
        "evidence": evidence,
        "partial_text": last_text if result is None else None,
    }


# --------------------------------------------------------------------------- orchestration


def plan_claude(spec: Any, environ: Any = None) -> dict:
    """Validate everything and return the launch plan without claiming a run_dir or spawning."""
    environ = os.environ if environ is None else environ
    normalized, warnings = validate_claude_spec(spec)
    env, policy = build_env(environ)
    claude_bin = resolve_claude_binary(env.get("PATH"))
    tools, allowed = resolve_tools(normalized)
    prompt_text = read_prompt(normalized["prompt_file"])
    argv = build_argv(claude_bin, normalized)
    edit_scope = None
    if normalized["profile"] == "writer":
        edit_scope = {
            "rule": edit_scope_rule(normalized["cwd"]),
            "resolved_cwd": os.path.realpath(normalized["cwd"]),
            "anchor": "filesystem-root",
        }
    return {
        "argv": argv,
        "env": env,
        "prompt_text": prompt_text,
        "spec": normalized,
        "warnings": warnings,
        "adapter": {
            "backend": BACKEND,
            "claude_bin": claude_bin,
            "profile": normalized["profile"],
            "tools": tools,
            "allowed_tools": allowed,
            "edit_scope": edit_scope,
            "permission_mode": PERMISSION_MODE,
            "safe_mode": True,
            "session_persistence": False,
            "prompt_delivery": "stdin",
            "env_policy": policy,
        },
    }


def run_claude(spec: Any, environ: Any = None) -> dict:
    plan = plan_claude(spec, environ)
    return wc.run_process(
        plan["spec"],
        plan["argv"],
        parse_claude_events,
        stdin_text=plan["prompt_text"],
        env=plan["env"],
        adapter_evidence=plan["adapter"],
    )


def load_spec(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            spec = json.load(handle)
    except OSError as exc:
        raise SpecError(f"cannot read spec file: {exc.strerror}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpecError(f"spec file is not valid JSON: {type(exc).__name__}") from exc
    if not isinstance(spec, dict):
        raise SpecError("spec must be a JSON object")
    return spec


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one Claude Code CLI worker attempt and print a receipt.")
    parser.add_argument("--spec", required=True, help="path to the JSON spec file")
    parser.add_argument("--dry-run", action="store_true", help="validate and print the plan without spawning")
    args = parser.parse_args(argv)

    try:
        spec = load_spec(args.spec)
    except SpecError as exc:
        receipt = wc.make_error_receipt({}, "invalid_spec", [str(exc)])
        _emit(receipt)
        return receipt["exit_code"]

    if args.dry_run:
        try:
            plan = plan_claude(spec)
        except SpecError as exc:
            receipt = wc.make_error_receipt(spec, "invalid_spec", [str(exc)])
            _emit(receipt)
            return receipt["exit_code"]
        _emit(
            {
                "schema": wc.RECEIPT_SCHEMA,
                "status": "dry_run",
                "exit_code": 0,
                "argv": plan["argv"],
                "adapter": plan["adapter"],
                "prompt_sha256": wc.sha256_file(plan["spec"]["prompt_file"]),
                "warnings": plan["warnings"],
            }
        )
        return 0

    try:
        receipt = run_claude(spec)
    except SpecError as exc:
        receipt = wc.make_error_receipt(spec, "invalid_spec", [str(exc)])
    except Exception as exc:  # keep the receipt contract even on adapter bugs
        traceback.print_exc(file=sys.stderr)
        receipt = wc.make_error_receipt(spec, "internal_error", [f"{type(exc).__name__}: {str(exc)[:200]}"])
    _emit(receipt)
    return receipt["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
