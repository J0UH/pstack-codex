#!/usr/bin/env python3
"""Retain explicitly activated pstack context across supported Codex hooks."""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pstack import change_state, mode_context, read_state

# A mention ends at whitespace or end of line, optionally after closing punctuation.
# Word characters, hyphens and colons glued to the name are a different token.
DELIMITER = r"(?=[.,;:!?)\]}]*(?:[ \t]|$))"
ACTIVATE = re.compile(r"^ {0,3}[$/](?:pstack-codex:)?poteto-mode" + DELIMITER, re.I)
DOLLAR_MENTION = re.compile(r"(?<![\w\\])\$(?:pstack-codex:)?poteto-mode" + DELIMITER, re.I)
EXIT = re.compile(r"^ {0,3}(?:exit|disable|leave|stop using)[ \t]+(?:[$/])?(?:pstack-codex:)?poteto(?:-mode| mode)?[.!]?[ \t]*$", re.I)
NEW_TASK = re.compile(r"^ {0,3}(?:[$/](?:pstack-codex:)?poteto-mode[ \t]+)?new task\b", re.I)
AFTER_MENTION = re.compile(r"[.,;:!?)\]}]*[ \t]*")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
INLINE_CODE = re.compile(r"(`+).*?\1")
SINGLE_QUOTED = re.compile(r"(?<!\w)'(?:\\.|[^'\\])*'|‘[^’]*’")
DOUBLE_QUOTE = re.compile(r'(?<!\\)["“”]')


def _blank(match: re.Match) -> str:
    return " " * len(match[0])


def prose_lines(lines: list[str]) -> Iterator[tuple[int, str]]:
    """Yield (index, visible text) for the lines where an explicit mention counts.

    Fenced code (tracked across lines), indented code and blockquotes are skipped.
    Inline code and single-quoted spans are blanked. Double quotes alternate across
    lines, so text inside a quote that opened on an earlier line stays hidden until
    the quote closes; positions are preserved so a match maps back to the raw line.
    """
    fence = None
    quoted = False
    for index, line in enumerate(lines):
        marker = FENCE.match(line)
        if fence is not None:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not line[marker.end():].strip():
                fence = None
            continue
        if marker:
            fence = (marker[1][0], len(marker[1]))
            continue
        if line.startswith(("    ", "\t")) or line.lstrip(" ").startswith(">"):
            continue
        masked = SINGLE_QUOTED.sub(_blank, INLINE_CODE.sub(_blank, line))
        visible = list(masked)
        start = 0
        for quote in DOUBLE_QUOTE.finditer(masked):
            at = quote.start()
            if not quoted and quote[0] == '"' and at and masked[at - 1].isdigit():
                continue
            if quoted:
                visible[start:at] = " " * (at - start)
            visible[at] = " "
            quoted = not quoted
            start = at + 1
        if quoted:
            visible[start:] = " " * (len(visible) - start)
        yield index, "".join(visible)


def activation_mention(lines: list[str]) -> tuple[int, re.Match] | None:
    """Return the first explicit mention: slash or dollar form on the first line, dollar form on later prose lines."""
    for index, visible in prose_lines(lines):
        match = (ACTIVATE.match(visible) if index == 0 else None) or DOLLAR_MENTION.search(visible)
        if match:
            return index, match
    return None


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
    lines = prompt.lstrip("\r\n").splitlines()
    first_line = lines[0] if lines else ""
    if EXIT.fullmatch(first_line):
        change_state("deactivate", session, project)
        return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": "The user explicitly exited poteto-mode. Stop applying its style and automatic skill routing; retain the user's remaining task instructions."}}
    mention = activation_mention(lines)
    activated = mention is not None
    if activated:
        state = change_state("activate", session, project)
    new_task = NEW_TASK.match(first_line)
    if mention is not None:
        index, match = mention
        new_task = new_task or NEW_TASK.match(lines[index][AFTER_MENTION.match(lines[index], match.end()).end():])
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
