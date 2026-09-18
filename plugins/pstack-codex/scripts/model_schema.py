#!/usr/bin/env python3
"""Generate the published model JSON Schema from the validator's constants.

`schemas/models.schema.json` is a generated artifact of `build_schema()`. The role
labels, backends, effort vocabularies, inheritance aliases, profiles and budget labels
come from `model_config`, so the schema cannot drift from `validate_model_config` on
those constants, and `--check` fails when the published file differs byte for byte.
The structural rules are cross-checked in `tests/test_model_config.py` by running one
accept/reject corpus through both `validate_model_config` and the standard `jsonschema`
Draft 2020-12 validator. That package is a development/test dependency only, listed in
`requirements-test.txt`; this module and the runtime validator stay standard library.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from model_config import BACKEND_EFFORTS, BUDGETS, INHERITANCE_ALIASES, PANEL_ROLES, PROFILES, SCHEMA_VERSION, SINGLE_ROLES

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas/models.schema.json"
# Mirrors model_config._token: nonempty, no whitespace, no C0 or DEL controls. U+0085 and
# U+FEFF are listed explicitly because Python's \s includes only the former and
# ECMAScript's \s only the latter. The end anchor is a negative lookahead rather than
# "$" because Python's "$" also matches before a final newline, which would let "x\n"
# through Python-based validators while ECMAScript validators and _token reject it.
TOKEN_PATTERN = "^[^\\s\\u0000-\\u001f\\u007f\\u0085\\ufeff]+(?![\\s\\S])"
TOKEN = {"type": "string", "minLength": 1, "pattern": TOKEN_PATTERN}


def _entry(backend: str, *, informational: bool) -> dict:
    model = dict(TOKEN)
    if not (informational and backend == "native"):
        model["not"] = {"enum": list(INHERITANCE_ALIASES)}
    properties = {
        "backend": {"const": backend},
        "model": model,
        "effort": {"enum": list(BACKEND_EFFORTS[backend])},
        "description": {"type": "string"},
    }
    if backend != "native":
        properties["profile"] = {"enum": list(PROFILES)}
    if informational:
        properties["status"] = {"type": "string"}
        properties["reason"] = {"type": "string"}
    schema = {
        "type": "object",
        "properties": properties,
        "required": [] if informational else ["backend", "model", "effort"],
        "additionalProperties": False,
    }
    if informational and backend == "native":
        schema["allOf"] = [{
            "if": {"properties": {"model": {"enum": list(INHERITANCE_ALIASES)}}, "required": ["model"]},
            "then": {"not": {"required": ["effort"]}},
        }]
    return schema


def build_schema() -> dict:
    roles = {role: {"$ref": "#/$defs/modelEntry"} for role in SINGLE_ROLES}
    roles.update({role: {"$ref": "#/$defs/panel"} for role in PANEL_ROLES})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "pstack-codex model role configuration",
        "description": "Validates configuration syntax only, not availability, authentication, model entitlement or profile enforcement. Missing roles remain unresolved partial overrides.",
        "type": "object",
        "required": ["schema_version", "roles"],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
            "description": {"type": "string"},
            "profile_note": {"type": "string"},
            "budget": {"enum": list(BUDGETS)},
            "roles": {"type": "object", "additionalProperties": False, "properties": roles},
            "optional_backends": {
                "description": "Informational capability notes only. Does not activate a backend or provide role fallbacks.",
                "type": "object",
                "additionalProperties": False,
                "properties": {backend: _entry(backend, informational=True) for backend in BACKEND_EFFORTS},
            },
        },
        "$defs": {
            **{backend: _entry(backend, informational=False) for backend in BACKEND_EFFORTS},
            "inheritance": {
                "type": "object",
                "required": ["backend", "model"],
                "properties": {
                    "backend": {"const": "native"},
                    "model": {"enum": list(INHERITANCE_ALIASES)},
                    "description": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "modelEntry": {"oneOf": [{"$ref": f"#/$defs/{name}"} for name in (*BACKEND_EFFORTS, "inheritance")]},
            "panel": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/modelEntry"}},
        },
    }


def render() -> str:
    return json.dumps(build_schema(), indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Exit 1 when the published schema differs from the generated one")
    parser.add_argument("--write", action="store_true", help="Regenerate the published schema file")
    args = parser.parse_args()
    expected = render()
    if args.write:
        SCHEMA_PATH.write_text(expected)
    if args.check or args.write:
        current = SCHEMA_PATH.read_text() if SCHEMA_PATH.is_file() else None
        status = "verified" if current == expected else "stale"
        print(json.dumps({"status": status, "path": str(SCHEMA_PATH)}))
        return 0 if status == "verified" else 1
    sys.stdout.write(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
