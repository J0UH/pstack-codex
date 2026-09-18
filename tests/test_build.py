"""Source fidelity, integrity rejection, and relocatable build behavior."""

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pstack_build", ROOT / "scripts/build.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.output = self.base / "generated"

    def snapshot(self, directory):
        return {str(p.relative_to(directory)): (p.read_bytes(), p.stat().st_mode & 0o777)
                for p in directory.rglob("*") if p.is_file()}

    def source_copy(self):
        root = self.base / "relocated-source"
        shutil.copytree(ROOT / "upstream", root / "upstream")
        return root

    def test_complete_catalog_and_dormant_pack(self):
        ledger = builder.build(ROOT, self.output)
        source = ROOT / "upstream/pstack"
        registered = sorted(source.glob("skills/*/SKILL.md"))
        self.assertEqual(47, len(registered))
        self.assertEqual(23, len(list(source.glob("skills/principle-*/SKILL.md"))))
        self.assertEqual(23, len(list((self.output / "skills/poteto-mode/playbooks").glob("*.md"))))
        self.assertEqual(50, len(list(source.rglob("SKILL.md"))))
        for path in source.rglob("*"):
            if path.is_file() and path.relative_to(source).parts[0] in {"skills", "agents", "automations"}:
                self.assertTrue((self.output / path.relative_to(source)).is_file(), path)
        for path in (source / "automations").rglob("*"):
            if path.is_file():
                self.assertEqual(path.read_bytes(), (self.output / path.relative_to(source)).read_bytes())
        self.assertEqual([], list((self.output / "automations").rglob("openai.yaml")))
        self.assertEqual(3, len(list((self.output / "companion-skills").glob("*/SKILL.md"))))
        self.assertEqual([], list((self.output / "companion-skills").rglob("openai.yaml")))
        self.assertTrue((self.output / "companion-skills/LICENSE").is_file())
        self.assertEqual(46, ledger["catalog"]["explicit_only_registered_skills"])

    def test_body_and_helper_fidelity_against_original_source(self):
        ledger = builder.build(ROOT, self.output)
        for entry in ledger["files"]:
            actual = (self.output / entry["output"]).read_bytes()
            self.assertEqual(entry["sha256"], hashlib.sha256(actual).hexdigest())
            if entry["source"] is None:
                continue
            original = (ROOT / entry["source"]).read_bytes()
            self.assertEqual(entry["source_sha256"], hashlib.sha256(original).hexdigest())
            transformations = {op["id"] for op in entry["operations"]}
            if not transformations:
                self.assertEqual(original, actual, entry["output"])
            elif "codex-frontmatter" in transformations:
                source_body = original.split(b"\n---\n", 1)[1]
                generated_body = actual.split(b"<!-- /pstack-codex:host -->\n", 1)[1]
                self.assertEqual(source_body, generated_body, entry["output"])
                op = next(op for op in entry["operations"] if op["id"] == "codex-frontmatter")
                self.assertEqual(original.split(b"\n---\n", 1)[0][4:].decode(), op["upstream_frontmatter"])
            elif transformations == {"host-contract-notice"}:
                self.assertEqual(original, actual.split(b"<!-- /pstack-codex:host -->\n", 1)[1])
        check = "skills/poteto-mode/scripts/check-plan.mjs"
        self.assertEqual((ROOT / "upstream/pstack" / check).read_bytes(), (self.output / check).read_bytes())
        self.assertEqual(0o755, (self.output / check).stat().st_mode & 0o777)

    def test_explicit_discovery_and_normalized_skill_identity(self):
        builder.build(ROOT, self.output)
        explicit = []
        for source in (ROOT / "upstream/pstack/skills").glob("*/SKILL.md"):
            name = source.parent.name
            generated = self.output / "skills" / name
            fields, _, _ = builder.frontmatter((generated / "SKILL.md").read_bytes())
            self.assertEqual(name, fields["name"])
            self.assertEqual({"name", "description"}, set(fields))
            original, _, _ = builder.frontmatter(source.read_bytes())
            self.assertEqual(original["description"], fields["description"])
            policy = (generated / "agents/openai.yaml").read_text()
            if "allow_implicit_invocation: false" in policy:
                explicit.append(name)
            else:
                self.assertEqual("setup-pstack", name)
        self.assertEqual(46, len(explicit))
        self.assertIn("poteto-mode", explicit)

    def test_local_cross_skill_links_preserve_identity(self):
        builder.build(ROOT, self.output)
        references = {
            "skills/principle-attack-the-premise/SKILL.md": "../principle-build-the-lever/SKILL.md",
            "skills/architect/SKILL.md": "references/design-red-flags.md",
            "skills/architect/references/rationale-template.md": "../../arena/SKILL.md",
            "skills/why/references/source-playbook.md": "./sources/slack.md",
        }
        for source, target in references.items():
            path = self.output / source
            self.assertIn("](" + target + ")", path.read_text())
            resolved = (path.parent / target).resolve()
            self.assertTrue(resolved.is_file(), resolved)
            self.assertTrue(resolved.is_relative_to(self.output.resolve()))
        index = (self.output / "docs/routing-index.md").read_text()
        for path in (self.output / "skills").glob("*/SKILL.md"):
            self.assertIn(f"../skills/{path.parent.name}/SKILL.md", index)

    def test_relocation_and_repeated_build_are_byte_identical(self):
        builder.build(ROOT, self.output)
        first = self.snapshot(self.output)
        relocated_root = self.source_copy()
        other = self.base / "elsewhere"
        builder.build(relocated_root, other)
        self.assertEqual(first, self.snapshot(other))
        builder.build(ROOT, self.output)
        self.assertEqual(first, self.snapshot(self.output))
        builder.build(ROOT, self.output, check=True)
        for content, _ in first.values():
            self.assertNotIn(str(ROOT).encode(), content)
            self.assertNotIn(str(self.base).encode(), content)

    def test_tampering_fails_before_mutating_existing_outputs(self):
        builder.build(ROOT, self.output)
        first = self.snapshot(self.output)
        copied = self.source_copy()
        path = copied / "upstream/pstack/skills/how/SKILL.md"
        path.write_bytes(path.read_bytes() + b"\nUnreviewed edit\n")
        with self.assertRaisesRegex(builder.BuildError, "integrity mismatch"):
            builder.build(copied, self.output)
        self.assertEqual(first, self.snapshot(self.output))

    def test_companion_tampering_and_missing_sources_fail_closed(self):
        copied = self.source_copy()
        companion = copied / "upstream/cursor-team-kit/skills/deslop/SKILL.md"
        companion.write_bytes(companion.read_bytes() + b"\nchanged\n")
        with self.assertRaisesRegex(builder.BuildError, "integrity mismatch"):
            builder.build(copied, self.output)
        self.assertFalse(self.output.exists())
        shutil.copyfile(ROOT / "upstream/cursor-team-kit/skills/deslop/SKILL.md", companion)
        (copied / "upstream/pstack/skills/show-me-your-work/references/decision-log-template.tsv").unlink()
        with self.assertRaisesRegex(builder.BuildError, "Missing"):
            builder.build(copied, self.output)

    def test_unpinned_file_and_revision_are_rejected(self):
        copied = self.source_copy()
        extra = copied / "upstream/pstack/skills/new.md"
        extra.write_text("Unpinned instruction")
        with self.assertRaisesRegex(builder.BuildError, "Unmanifested"):
            builder.build(copied, self.output)
        extra.unlink()
        manifest = copied / "upstream/source-manifest.json"
        data = json.loads(manifest.read_text())
        data["revision"] = "unreviewed"
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(builder.BuildError, "Unreviewed revision"):
            builder.build(copied, self.output)

    def test_drift_check_reports_without_repairing(self):
        builder.build(ROOT, self.output)
        path = self.output / "skills/how/SKILL.md"
        path.write_bytes(path.read_bytes() + b"changed")
        before = self.snapshot(self.output)
        with self.assertRaisesRegex(builder.BuildError, "Generated output differs"):
            builder.build(ROOT, self.output, check=True)
        self.assertEqual(before, self.snapshot(self.output))

    def test_cli_operates_from_an_unrelated_directory(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/build.py"), "--output", str(self.output)],
                                cwd=self.base, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual("built", receipt["status"])
        self.assertEqual(47, receipt["registered_skills"])


if __name__ == "__main__":
    unittest.main()
