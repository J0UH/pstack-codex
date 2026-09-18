"""Validate pstack role configuration without selecting or probing any model."""

from __future__ import annotations

from copy import deepcopy


SINGLE_ROLES = (
    "feature, refactoring", "bug-fix", "perf-issue", "hillclimb", "judgment and prose",
    "hardest tasks", "how explorer", "how explainer", "why investigators", "why synthesizer",
    "reflect tooling", "reflect judgment, divergent, synthesizer", "swarm workers", "coordinator",
)
PANEL_ROLES = ("arena runners", "arena cross-judge pool", "architect runners", "interrogate reviewers")
BACKEND_EFFORTS = {
    "native": ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"),
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "grok": ("low", "medium", "high", "xhigh", "max"),
}
INHERITANCE_ALIASES = ("inherit-parent", "auto")
PROFILES = ("analysis", "reader", "writer")
BUDGETS = ("unlimited", "large", "medium", "small")


def _object(value: object, location: str) -> dict:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{location} must be an object with string keys")
    return value


def _fields(value: dict, allowed: set[str], location: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{location} contains unsupported fields: {', '.join(unknown)}")


def _token(value: object, location: str) -> str:
    if not isinstance(value, str) or not value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{location} must be an exact nonempty token without whitespace or controls")
    return value


def _text_fields(value: dict, names: tuple[str, ...], location: str) -> None:
    for name in names:
        if name in value and not isinstance(value[name], str):
            raise ValueError(f"{location}.{name} must be a string")


def _entry(value: object, location: str, *, informational_backend: str | None = None) -> None:
    entry = _object(value, location)
    informational = informational_backend is not None
    allowed = {"backend", "model", "effort", "profile", "description"}
    if informational:
        allowed |= {"status", "reason"}
    _fields(entry, allowed, location)
    backend = entry.get("backend", informational_backend)
    if not isinstance(backend, str) or backend not in BACKEND_EFFORTS:
        raise ValueError(f"{location}.backend must be native, claude, or grok")
    if informational and backend != informational_backend:
        raise ValueError(f"{location}.backend must match its optional_backends key")
    _text_fields(entry, ("description", "status", "reason"), location)

    model = None
    if "model" in entry:
        model = _token(entry["model"], f"{location}.model")
    elif not informational:
        raise ValueError(f"{location}.model is required")

    if model in INHERITANCE_ALIASES:
        if backend != "native":
            raise ValueError(f"{location}: inherit-parent and auto are native-only aliases")
        if "effort" in entry:
            raise ValueError(f"{location}: inheritance omits effort; do not override the parent's effort")
    elif "effort" not in entry and not informational:
        raise ValueError(f"{location}.effort is required for an explicit model")

    if "effort" in entry:
        effort = entry["effort"]
        if not isinstance(effort, str) or effort not in BACKEND_EFFORTS[backend]:
            raise ValueError(f"{location}.effort is not a supported {backend} configuration token")
    if "profile" in entry:
        if backend == "native":
            raise ValueError(f"{location}.profile is CLI-only; it cannot provide native tool protection")
        profile = entry["profile"]
        if not isinstance(profile, str) or profile not in PROFILES:
            raise ValueError(f"{location}.profile must be analysis, reader, or writer")


def validate_model_config(value: object) -> dict:
    """Return a detached, unchanged config or raise ValueError with a field-level error.

    Partial overrides remain partial. This validates syntax and role shapes only;
    model entitlement, supported effort, tool capability, and authentication require
    separate runtime evidence. optional_backends never becomes an active role.
    """
    config = _object(value, "config")
    _fields(config, {"schema_version", "roles", "description", "profile_note", "budget", "optional_backends"}, "config")
    if type(config.get("schema_version")) is not int or config["schema_version"] != 1:
        raise ValueError("config.schema_version must be integer 1")
    _text_fields(config, ("description", "profile_note"), "config")
    if "budget" in config and (not isinstance(config["budget"], str) or config["budget"] not in BUDGETS):
        raise ValueError("config.budget must be unlimited, large, medium, or small")
    roles = _object(config.get("roles"), "config.roles")
    for role, entry in roles.items():
        if role in PANEL_ROLES:
            if not isinstance(entry, list) or not entry:
                raise ValueError(f"config.roles.{role} must be a nonempty list of model entries")
            for index, member in enumerate(entry):
                _entry(member, f"config.roles.{role}[{index}]")
        elif role in SINGLE_ROLES:
            _entry(entry, f"config.roles.{role}")
        else:
            raise ValueError(f"config.roles contains unknown role: {role}")
    if "optional_backends" in config:
        optional = _object(config["optional_backends"], "config.optional_backends")
        for backend, info in optional.items():
            if backend not in BACKEND_EFFORTS:
                raise ValueError(f"config.optional_backends contains unknown backend: {backend}")
            _entry(info, f"config.optional_backends.{backend}", informational_backend=backend)
    return deepcopy(config)
