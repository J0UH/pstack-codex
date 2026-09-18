import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from model_config import validate_model_config


def config(entry=None, role="bug-fix", **metadata):
    if entry is None:
        entry = {"backend": "claude", "model": "exact-model", "effort": "xhigh"}
    return {"schema_version": 1, "roles": {role: entry}, **metadata}


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

    def test_schema_documents_role_and_backend_constraints(self):
        schema = json.loads((ROOT / "schemas/models.schema.json").read_text())
        roles = schema["properties"]["roles"]
        self.assertFalse(roles["additionalProperties"])
        self.assertEqual(18, len(roles["properties"]))
        self.assertEqual("#/$defs/panel", roles["properties"]["arena cross-judge pool"]["$ref"])
        self.assertEqual(["low", "medium", "high", "xhigh", "max"], schema["$defs"]["claude"]["properties"]["effort"]["enum"])
        self.assertNotIn("profile", schema["$defs"]["native"]["properties"])


if __name__ == "__main__":
    unittest.main()
