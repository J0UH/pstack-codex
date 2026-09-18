#!/usr/bin/env python3
"""Project-independent pstack configuration and conversation state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def config_path() -> Path:
    override = os.environ.get("PSTACK_MODEL_CONFIG")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("PSTACK_MODEL_CONFIG must be an absolute path")
        return path
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "pstack/models.json"


def state_root() -> Path:
    if os.environ.get("PSTACK_STATE_DIR"):
        return Path(os.environ["PSTACK_STATE_DIR"]).expanduser()
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "pstack/state"


def identity(session: str, project: str) -> tuple[str, str]:
    if not isinstance(session, str) or not session.strip() or len(session) > 512:
        raise ValueError("A nonempty session identity is required")
    path = Path(project).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("Project directory does not exist")
    return session, str(path)


def state_path(session: str, project: str) -> Path:
    session, project = identity(session, project)
    key = hashlib.sha256(json.dumps([session, project]).encode()).hexdigest()
    return state_root() / (key + ".json")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".pstack-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_state(session: str, project: str) -> dict:
    session, project = identity(session, project)
    path = state_path(session, project)
    if not path.exists():
        return {"schema_version": 1, "session": session, "project": project, "active": False, "generation": 0, "playbook": None}
    state = json.loads(path.read_text())
    if not isinstance(state, dict) or state.get("session") != session or state.get("project") != project or state.get("schema_version") != 1:
        raise ValueError("State identity/schema mismatch")
    if type(state.get("active")) is not bool or type(state.get("generation")) is not int:
        raise ValueError("Malformed mode state")
    return state


def change_state(action: str, session: str, project: str, playbook: str | None = None) -> dict:
    state = read_state(session, project)
    if action == "status":
        return state
    if action == "select":
        if not state["active"]:
            raise ValueError("Activate poteto-mode before selecting a playbook")
        allowed = {p.stem for p in (ROOT / "skills/poteto-mode/playbooks").glob("*.md")} | {"figure-it-out"}
        if playbook not in allowed:
            raise ValueError("Unknown playbook")
        state["playbook"] = playbook
    elif action == "activate":
        if state["active"]:
            return state
        state.update(active=True, playbook=None)
    elif action == "deactivate":
        state.update(active=False, playbook=None)
    elif action == "reset":
        if not state["active"]:
            return state
        state["playbook"] = None
    else:
        raise ValueError("Unknown mode action")
    state["generation"] += 1
    atomic_json(state_path(session, project), state)
    return state


def mode_context(state: dict, full: bool = True) -> str:
    if not state["active"]:
        return ""
    router = ROOT / "skills/poteto-mode/SKILL.md"
    host = ROOT / "adapters/host.md"
    if not router.is_file() or not host.is_file():
        raise ValueError("Plugin is not built: router or host adapter missing")
    context = [
        "pstack-codex: poteto-mode remains active in this conversation and project.",
        "This retains the chosen style, not permission for new external actions. Honor the user's current scope, explicit opt-out and host policies.",
        f"Plugin root: {ROOT}",
        f"Mode generation: {state['generation']}; current playbook: {state['playbook'] or 'rematch the current request'}.",
        "A casual turn need not run a playbook. New task rematches; it does not create a visible Codex task automatically.",
        "Read referenced pstack leaves from this installed package, not same-named unrelated skills.",
    ]
    if full:
        context += ["--- Codex host adapter ---", host.read_text(), "--- Active poteto-mode source ---", router.read_text()]
    else:
        context += [f"Retain the active workflow. If its instructions are missing from context, read {host} and {router} in full before proceeding."]
    return "\n".join(context)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    mode = sub.add_parser("mode")
    mode.add_argument("action", choices=["activate", "deactivate", "status", "reset", "select", "context"])
    mode.add_argument("--session", default=os.environ.get("CODEX_THREAD_ID"))
    mode.add_argument("--project", default=os.getcwd())
    mode.add_argument("--playbook")
    models = sub.add_parser("models")
    models.add_argument("action", choices=["path", "show"])
    args = parser.parse_args()
    try:
        if args.command == "models":
            path = config_path()
            if args.action == "path":
                print(path)
            elif not path.is_file():
                print(json.dumps({"status": "needs_setup", "path": str(path), "next": "Use the package's setup-pstack skill to confirm available role mappings."}))
                return 2
            else:
                value = json.loads(path.read_text())
                if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("roles"), dict):
                    raise ValueError("Invalid model configuration schema")
                print(json.dumps(value, indent=2))
        elif args.action == "context":
            print(mode_context(read_state(args.session, args.project)))
        else:
            print(json.dumps(change_state(args.action, args.session, args.project, args.playbook), indent=2))
        return 0
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
