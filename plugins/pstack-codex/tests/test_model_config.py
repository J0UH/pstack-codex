import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import model_config
from model_config import validate_model_config
from model_schema import SCHEMA_PATH, TOKEN_PATTERN, build_schema, render

try:
    import jsonschema
except ImportError:
    jsonschema = None

# Schema parity runs against the standard Draft 2020-12 validator from requirements-test.txt.
# Locally the parity tests skip when it is absent. CI installs it, so a missing package
# there is a failure rather than a silent skip.
REQUIRE_JSONSCHEMA = bool(os.environ.get("CI") or os.environ.get("PSTACK_REQUIRE_JSONSCHEMA"))


def needs_jsonschema(test):
    if jsonschema is not None:
        return test
    if REQUIRE_JSONSCHEMA:
        def missing(self):
            self.fail("jsonschema is required in this environment: pip install -r requirements-test.txt")
        return missing
    return unittest.skip("jsonschema not installed; pip install -r requirements-test.txt to run schema parity checks")(test)


def config(entry=None, role="bug-fix", **metadata):
    if entry is None:
        entry = {"backend": "claude", "model": "exact-model", "effort": "xhigh"}
    return {"schema_version": 1, "roles": {role: entry}, **metadata}


def notes(**backends):
    return {"schema_version": 1, "roles": {}, "optional_backends": backends}


def model(token, backend="claude"):
    return config({"backend": backend, "model": token, "effort": "high"})


# Characters spelled out by code point so the source stays plain ASCII.
NUL, VT, FF, FS, US, DEL = (chr(code) for code in (0x00, 0x0B, 0x0C, 0x1C, 0x1F, 0x7F))
NEL, NBSP, OGHAM, EN_QUAD, LS, PS, NNBSP, MMSP, IDEO, BOM = (chr(code) for code in (0x85, 0xA0, 0x1680, 0x2000, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF))
NON_ASCII_TOKEN = "mod" + chr(0xE8) + "le-" + chr(0x4F8B)

NATIVE = {"backend": "native", "model": "gpt-example", "effort": "xhigh"}
CLAUDE = {"backend": "claude", "model": "claude-example", "effort": "max"}
GROK = {"backend": "grok", "model": "grok-example", "effort": "high", "profile": "analysis"}
INHERIT = {"backend": "native", "model": "inherit-parent"}

ACCEPTED = [
    {"schema_version": 1, "roles": {}}, {"schema_version": 1.0, "roles": {}},
    config(NATIVE), config(CLAUDE), config(GROK), config(INHERIT),
    config({"backend": "native", "model": "auto", "description": "runs on the parent"}),
    config({"backend": "native", "model": "gpt-example", "effort": "none"}),
    config({"backend": "native", "model": "gpt-example", "effort": "ultra", "description": "d"}),
    config({"backend": "claude", "model": "claude-example", "effort": "low", "profile": "writer"}),
    config({"backend": "grok", "model": "grok-example", "effort": "max", "profile": "reader"}),
    model("claude-fable-5.1_a/b:c"), model(NON_ASCII_TOKEN, "native"), model("x"),
    config(role="coordinator"), config(NATIVE, "reflect judgment, divergent, synthesizer"),
    config([INHERIT], "arena runners"), config([CLAUDE, CLAUDE], "arena cross-judge pool"),
    config([INHERIT], "architect runners"), config([NATIVE, CLAUDE, GROK, INHERIT], "interrogate reviewers"),
    config(description="d", profile_note="p", budget="small"), config(budget="unlimited"),
    notes(), notes(grok={}), notes(native={"model": "auto"}), notes(claude={"model": "claude-example"}),
    notes(native={"backend": "native", "effort": "ultra", "status": "listed", "reason": "r"}),
    notes(grok={"model": "grok-example", "effort": "xhigh", "profile": "analysis", "status": "unverified", "reason": "r", "description": "d"}),
]
REJECTED = [
    {}, {"roles": {}}, {"schema_version": 1}, {"schema_version": 2, "roles": {}}, {"schema_version": True, "roles": {}},
    {"schema_version": False, "roles": {}}, {"schema_version": "1", "roles": {}}, {"schema_version": 1.5, "roles": {}},
    {"schema_version": 2.0, "roles": {}}, {"schema_version": 0, "roles": {}}, {"schema_version": -1, "roles": {}},
    {"schema_version": 1.0000001, "roles": {}}, {"schema_version": None, "roles": {}}, {"schema_version": [1], "roles": {}},
    {"schema_version": 1, "roles": []}, {"schema_version": 1, "roles": None},
    {"schema_version": 1, "roles": {}, "unexpected": True}, config(description=5), config(budget="huge"), config(budget=1),
    config(profile_note=[]), config(role="bug_fix"), config(role="Bug-fix"), config(role="arena runner"),
    {"schema_version": 1, "roles": {"bug-fix": None}}, {"schema_version": 1, "roles": {"bug-fix": "claude"}},
    {"schema_version": 1, "roles": {"bug-fix": []}}, config([CLAUDE]), config(CLAUDE, "arena runners"),
    config([], "arena runners"), config([None], "architect runners"), config([[CLAUDE]], "interrogate reviewers"),
    config([CLAUDE, {"backend": "claude", "model": "x"}], "arena cross-judge pool"),
    config({"backend": "unknown", "model": "m", "effort": "high"}), config({"backend": 1, "model": "m", "effort": "high"}),
    config({"model": "m", "effort": "high"}), config({"backend": "claude", "effort": "high"}),
    model(""), model("a b"), model(" x"), model("x\t"), model(3), model(None),
    model("x\n"), model("x\r"), model("x\r\n"), model("\nx"), model("x\n", "native"), model("x\n", "grok"),
    model("x" + NBSP), model("x" + LS), model("x" + PS), model("x" + IDEO), model("x" + NEL), model("x" + BOM), model(BOM),
    model("x" + NUL), model("x" + US), model("x" + DEL),
    config({"backend": "native", "model": "gpt-example"}), config({"backend": "claude", "model": "claude-example", "effort": "ultra"}),
    config({"backend": "claude", "model": "claude-example", "effort": "none"}), config({"backend": "grok", "model": "grok-example", "effort": "minimal"}),
    config({"backend": "native", "model": "gpt-example", "effort": "HIGH"}), config({"backend": "native", "model": "gpt-example", "effort": 3}),
    config({"backend": "native", "model": "gpt-example", "effort": None}), config({"backend": "native", "model": "auto", "effort": "high"}),
    config({"backend": "native", "model": "inherit-parent", "effort": "none"}), config({"backend": "claude", "model": "auto", "effort": "high"}),
    config({"backend": "grok", "model": "inherit-parent"}), config({"backend": "claude", "model": "inherit-parent"}),
    config({"backend": "native", "model": "auto\n"}), config({"backend": "native", "model": "auto" + BOM}),
    config({"backend": "native", "model": "auto", "profile": "reader"}), config({"backend": "native", "model": "auto", "status": "s"}),
    config({"backend": "native", "model": "gpt-example", "effort": "high", "profile": "analysis"}),
    config({"backend": "claude", "model": "claude-example", "effort": "high", "profile": "bypass"}),
    config({"backend": "claude", "model": "claude-example", "effort": "high", "profile": None}),
    config({"backend": "grok", "model": "grok-example", "effort": "high", "profile": ["analysis"]}),
    config({**CLAUDE, "unexpected": True}), config({**CLAUDE, "status": "ok"}), config({**CLAUDE, "reason": "r"}),
    config({**CLAUDE, "description": 1}), config({**GROK, "profile": "Analysis"}),
    {"schema_version": 1, "roles": {}, "optional_backends": []}, notes(unknown={}), notes(grok=None), notes(grok={"backend": "claude"}),
    notes(grok={"effort": "ultra"}), notes(native={"model": "auto", "effort": "high"}), notes(native={"profile": "reader"}),
    notes(claude={"model": "inherit-parent"}), notes(grok={"model": ""}), notes(grok={"status": 1}), notes(grok={"unexpected": True}),
    notes(claude={"profile": "bypass"}), notes(native={"model": "gpt example"}), notes(native={"model": "auto\n"}), notes(claude={"model": "x\r"}),
]
CORPUS = [(value, True) for value in ACCEPTED] + [(value, False) for value in REJECTED]


def python_accepts(value):
    try:
        validate_model_config(value)
    except ValueError:
        return False
    return True


def token_accepted(token):
    try:
        model_config._token(token, "model")
    except ValueError:
        return False
    return True


def flipped_fixtures(schema):
    validator = jsonschema.Draft202012Validator(schema)
    return [value for value in ACCEPTED if not validator.is_valid(value)] + [value for value in REJECTED if validator.is_valid(value)]


class ModelConfigTests(unittest.TestCase):
    def test_published_example_passes_without_rewriting(self):
        example = json.loads((ROOT / "examples/models.astra-claude.json").read_text())
        self.assertEqual(example, validate_model_config(example))

    def test_partial_configuration_does_not_invent_defaults(self):
        self.assertEqual({"schema_version": 1, "roles": {}}, validate_model_config({"schema_version": 1, "roles": {}}))
        result = validate_model_config(config())
        self.assertEqual(["bug-fix"], list(result["roles"]))
        self.assertNotIn("profile", result["roles"]["bug-fix"])

    def test_returned_configuration_is_detached(self):
        original = config([{"backend": "native", "model": "auto"}], "arena runners")
        validated = validate_model_config(original)
        validated["roles"]["arena runners"][0]["model"] = "changed"
        self.assertEqual("auto", original["roles"]["arena runners"][0]["model"])

    def test_unknown_role_backend_or_effort_is_rejected(self):
        values = [config(role="bug_fix"), config({"backend": "unknown", "model": "exact", "effort": "high"}),
                  config({"backend": "claude", "model": "exact", "effort": "ultra"}),
                  config({"backend": "grok", "model": "exact", "effort": "minimal"})]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_model_config(value)

    def test_each_panel_requires_a_nonempty_list(self):
        for role in ("arena runners", "arena cross-judge pool", "architect runners", "interrogate reviewers"):
            for entry in ({"backend": "native", "model": "auto"}, [], [None]):
                with self.subTest(role=role, entry=entry), self.assertRaises(ValueError):
                    validate_model_config(config(entry, role))
            good = [{"backend": "native", "model": "auto"}, {"backend": "claude", "model": "exact", "effort": "max"}]
            self.assertEqual(2, len(validate_model_config(config(good, role))["roles"][role]))

    def test_single_role_rejects_panel_shape(self):
        with self.assertRaises(ValueError):
            validate_model_config(config([{"backend": "native", "model": "auto"}]))

    def test_aliases_mean_true_native_inheritance_only(self):
        for alias in ("inherit-parent", "auto"):
            self.assertEqual(alias, validate_model_config(config({"backend": "native", "model": alias}))["roles"]["bug-fix"]["model"])
            for backend in ("claude", "grok"):
                with self.subTest(alias=alias, backend=backend), self.assertRaises(ValueError):
                    validate_model_config(config({"backend": backend, "model": alias, "effort": "high"}))
            with self.assertRaises(ValueError):
                validate_model_config(config({"backend": "native", "model": alias, "effort": "high"}))

    def test_explicit_model_requires_effort_and_exact_identifier(self):
        for entry in ({"backend": "native", "model": "exact"}, {"backend": "claude", "effort": "high"},
                      {"backend": "claude", "model": "", "effort": "high"},
                      {"backend": "claude", "model": " exact ", "effort": "high"}):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                validate_model_config(config(entry))

    def test_profiles_are_optional_cli_capability_labels_only(self):
        for backend in ("claude", "grok"):
            for profile in ("analysis", "reader", "writer"):
                value = config({"backend": backend, "model": "exact", "effort": "high", "profile": profile})
                self.assertEqual(value, validate_model_config(value))
        for backend, profile in (("native", "reader"), ("claude", "bypass")):
            with self.subTest(backend=backend), self.assertRaises(ValueError):
                validate_model_config(config({"backend": backend, "model": "exact", "effort": "high", "profile": profile}))

    def test_optional_backend_notes_do_not_activate_roles(self):
        value = {"schema_version": 1, "roles": {}, "optional_backends": {"grok": {
            "model": "grok-example", "effort": "xhigh", "profile": "analysis",
            "status": "unverified", "reason": "No successful invocation yet"}}}
        self.assertEqual(value, validate_model_config(value))
        self.assertEqual({}, validate_model_config(value)["roles"])
        for info in ({"unknown": {}}, {"grok": {"backend": "claude"}}, {"grok": {"effort": "ultra"}}):
            with self.subTest(info=info), self.assertRaises(ValueError):
                validate_model_config({"schema_version": 1, "roles": {}, "optional_backends": info})

    def test_descriptive_fields_and_budget_are_retained(self):
        value = config(description="Example", profile_note="Choose by task", budget="large")
        self.assertEqual(value, validate_model_config(value))
        for changes in ({"budget": "huge"}, {"description": 5}, {"unexpected": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_model_config(config(**changes))

    def test_malformed_types_fail_as_value_errors(self):
        values = [None, [], {"schema_version": True, "roles": {}}, {"schema_version": 2, "roles": {}},
                  {"schema_version": 1, "roles": []}, {"schema_version": 1, "roles": {1: {}}},
                  config({"backend": [], "model": "m", "effort": "high"}),
                  config({"backend": "native", "model": "m", "effort": []})]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_model_config(value)

    def test_schema_version_is_a_mathematical_integer(self):
        # JSON Schema "integer" is numeric, not lexical: 1.0 is the integer 1 and satisfies
        # {"type": "integer", "const": 1}. The runtime validator accepts the same spellings
        # and returns them unchanged; booleans are not JSON numbers and stay rejected.
        for version in (1, 1.0):
            with self.subTest(version=version):
                validated = validate_model_config({"schema_version": version, "roles": {}})
                self.assertIs(type(version), type(validated["schema_version"]))
                self.assertEqual(version, validated["schema_version"])
        for version in (True, False, 1.5, 0.999, 1.0000001, 2, 2.0, 0, -1, "1", "1.0", None, [1], {}):
            with self.subTest(version=version), self.assertRaises(ValueError):
                validate_model_config({"schema_version": version, "roles": {}})

    def test_token_pattern_anchors_to_the_true_end_of_string(self):
        # Python's "$" also matches before a trailing newline, so a "$"-anchored pattern lets
        # "x\n" through Python-based validators while ECMAScript ones reject it. The negative
        # lookahead means end of input in both engines, and the runtime rule rejects it too.
        self.assertNotIn("$", TOKEN_PATTERN)
        self.assertTrue(TOKEN_PATTERN.endswith("(?![\\s\\S])"))
        for token in ("x", "claude-fable-5.1", "a/b:c_d", NON_ASCII_TOKEN):
            self.assertIsNotNone(re.search(TOKEN_PATTERN, token), repr(token))
            self.assertTrue(token_accepted(token), repr(token))
        rejected = ("", " ", "x\n", "x\r", "x\r\n", "\nx", "x y", "x\t", "x" + VT, "x" + FF, "x" + NBSP, "x" + OGHAM,
                    "x" + EN_QUAD, "x" + LS, "x" + PS, "x" + NNBSP, "x" + MMSP, "x" + IDEO, "x" + NEL, "x" + BOM, BOM,
                    "x" + NUL, "x" + FS, "x" + US, "x" + DEL)
        for token in rejected:
            self.assertIsNone(re.search(TOKEN_PATTERN, token), repr(token))
            self.assertFalse(token_accepted(token), repr(token))
        schema = json.loads(SCHEMA_PATH.read_text())
        entries = [schema["$defs"][backend] for backend in model_config.BACKEND_EFFORTS]
        entries += [schema["properties"]["optional_backends"]["properties"][backend] for backend in model_config.BACKEND_EFFORTS]
        self.assertEqual({TOKEN_PATTERN}, {entry["properties"]["model"]["pattern"] for entry in entries})

    def test_runtime_token_rule_matches_the_pattern_for_every_bmp_code_point(self):
        # The parity corpus samples; this checks every Basic Multilingual Plane code point so
        # model_config._token and the schema pattern, as Python's re evaluates it, cannot diverge.
        pattern = re.compile(TOKEN_PATTERN)
        for code in range(0x10000):
            token = "a" + chr(code)
            self.assertEqual(pattern.search(token) is not None, token_accepted(token), f"U+{code:04X}")

    def test_schema_documents_role_and_backend_constraints(self):
        schema = json.loads((ROOT / "schemas/models.schema.json").read_text())
        roles = schema["properties"]["roles"]
        self.assertFalse(roles["additionalProperties"])
        self.assertEqual(18, len(roles["properties"]))
        self.assertEqual("#/$defs/panel", roles["properties"]["arena cross-judge pool"]["$ref"])
        self.assertEqual(["low", "medium", "high", "xhigh", "max"], schema["$defs"]["claude"]["properties"]["effort"]["enum"])
        self.assertNotIn("profile", schema["$defs"]["native"]["properties"])

    def test_published_schema_is_generated_from_the_validator_constants(self):
        self.assertEqual(ROOT / "schemas/models.schema.json", SCHEMA_PATH)
        self.assertEqual(render(), SCHEMA_PATH.read_text())
        self.assertEqual(build_schema(), json.loads(SCHEMA_PATH.read_text()))
        check = subprocess.run([sys.executable, str(ROOT / "scripts/model_schema.py"), "--check"], capture_output=True, text=True)
        self.assertEqual((0, "verified"), (check.returncode, json.loads(check.stdout)["status"]))

    def test_constant_drift_changes_the_rendered_schema_and_the_validator_together(self):
        ultra = config({"backend": "claude", "model": "claude-example", "effort": "ultra"})
        with patch.dict(model_config.BACKEND_EFFORTS, {"claude": model_config.BACKEND_EFFORTS["claude"] + ("ultra",)}):
            self.assertNotEqual(render(), SCHEMA_PATH.read_text())
            self.assertTrue(python_accepts(ultra))
        self.assertFalse(python_accepts(ultra))

    def test_validator_decides_every_parity_fixture_as_labelled(self):
        self.assertEqual(len(CORPUS), len({json.dumps(value, sort_keys=True) for value, _ in CORPUS}))
        for value, expected in CORPUS:
            with self.subTest(value=value):
                self.assertEqual(value, json.loads(json.dumps(value)))
                self.assertEqual(expected, python_accepts(value))

    @needs_jsonschema
    def test_published_schema_is_a_valid_draft_2020_12_schema(self):
        jsonschema.Draft202012Validator.check_schema(json.loads(SCHEMA_PATH.read_text()))

    @needs_jsonschema
    def test_schema_and_validator_agree_on_every_parity_fixture(self):
        validator = jsonschema.Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))
        for value, expected in CORPUS:
            with self.subTest(value=value):
                self.assertEqual(expected, python_accepts(value))
                self.assertEqual(expected, validator.is_valid(value))
        example = json.loads((ROOT / "examples/models.astra-claude.json").read_text())
        self.assertTrue(validator.is_valid(example))

    @needs_jsonschema
    def test_parity_corpus_detects_schema_drift(self):
        dollar_anchored = TOKEN_PATTERN.replace("(?![\\s\\S])", "$")
        without_bom = TOKEN_PATTERN.replace("\\ufeff", "")
        self.assertNotEqual(TOKEN_PATTERN, dollar_anchored)
        self.assertNotEqual(TOKEN_PATTERN, without_bom)
        mutations = {
            "claude effort vocabulary": lambda s: s["$defs"]["claude"]["properties"]["effort"]["enum"].append("ultra"),
            "claude alias exclusion": lambda s: s["$defs"]["claude"]["properties"]["model"].pop("not"),
            "native profile": lambda s: s["$defs"]["native"]["properties"].update(profile={"enum": ["analysis", "reader", "writer"]}),
            "panel minimum": lambda s: s["$defs"]["panel"].pop("minItems"),
            "role labels": lambda s: s["properties"]["roles"].update(additionalProperties=True),
            "inheritance effort": lambda s: s["$defs"]["inheritance"]["properties"].update(effort={"enum": ["high"]}),
            "informational inheritance effort": lambda s: s["properties"]["optional_backends"]["properties"]["native"].pop("allOf"),
            "entry fields": lambda s: s["$defs"]["claude"].update(additionalProperties=True),
            "top-level fields": lambda s: s.update(additionalProperties=True),
            "schema version value": lambda s: s["properties"]["schema_version"].pop("const"),
            "schema version type": lambda s: s["properties"]["schema_version"].update(type="string"),
            "token end anchor": lambda s: s["$defs"]["claude"]["properties"]["model"].update(pattern=dollar_anchored),
            "token whitespace set": lambda s: s["$defs"]["claude"]["properties"]["model"].update(pattern=without_bom),
        }
        self.assertEqual([], flipped_fixtures(build_schema()))
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                schema = build_schema()
                mutate(schema)
                self.assertTrue(flipped_fixtures(schema))


if __name__ == "__main__":
    unittest.main()
