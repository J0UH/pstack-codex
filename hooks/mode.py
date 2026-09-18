#!/usr/bin/env python3
"""Retain explicitly activated pstack context across supported Codex hooks."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pstack import change_state, mode_context, read_state

ACTIVATE = re.compile(r"^ {0,3}[$/](?:pstack-codex:)?poteto-mode(?=$|[ \t])", re.I)
DOLLAR_MENTION = re.compile(r"(?<![\w\\])\$(?:pstack-codex:)?poteto-mode(?=$|[^\w:-])", re.I)
EXIT = re.compile(r"^ {0,3}(?:exit|disable|leave|stop using)[ \t]+(?:[$/])?(?:pstack-codex:)?poteto(?:-mode| mode)?[.!]?[ \t]*$", re.I)
NEW_TASK = re.compile(r"^ {0,3}(?:[$/](?:pstack-codex:)?poteto-mode[ \t]+)?new task\b", re.I)


def activation_mention(line: str) -> re.Match | None:
    if line.startswith(("    ", "\t")) or line.lstrip(" ").startswith((">", "```", "~~~")):
        return None
    visible = re.sub(r"(`+).*?\1", lambda match: " " * len(match[0]), line)
    visible = re.sub(r'"(?:\\.|[^"\\])*"|(?<!\w)\'(?:\\.|[^\'\\])*\'|“[^”]*”|‘[^’]*’', lambda match: " " * len(match[0]), visible)
    return ACTIVATE.match(visible) or DOLLAR_MENTION.search(visible)


def handle(event: dict) -> dict:
    name = event.get("hook_event_name")
    if name not in {"SessionStart", "UserPromptSubmit"}:
        return {}
    session, project = event.get("session_id"), event.get("cwd")
    if not session or not project:
        return {"systemMessage": "pstack mode persistence unavailable: hook lacks session_id/cwd."}
    state = read_state(session, project)
    prompt = event.get("prompt", "") if name == "UserPromptSubmit" else ""
    if not isinstance(prompt, str):
        raise ValueError("Hook prompt must be a string")
    first_line = prompt.lstrip("\r\n").splitlines()[0] if prompt.strip("\r\n") else ""
    if EXIT.fullmatch(first_line):
        change_state("deactivate", session, project)
        return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": "The user explicitly exited poteto-mode. Stop applying its style and automatic skill routing; retain the user's remaining task instructions."}}
    mention = activation_mention(first_line)
    activated = mention is not None
    if activated:
        state = change_state("activate", session, project)
    new_task = NEW_TASK.match(first_line) or (mention is not None and NEW_TASK.match(first_line[mention.end():].lstrip()))
    if new_task and state["active"]:
        state = change_state("reset", session, project)
    context = mode_context(state, full=activated or name == "SessionStart")
    return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": context}} if context else {}


if __name__ == "__main__":
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            raise ValueError("Hook input must be an object")
        print(json.dumps(handle(data)))
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"systemMessage": f"pstack mode persistence failed: {exc}"}))
        raise SystemExit(1)
