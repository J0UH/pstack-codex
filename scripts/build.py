#!/usr/bin/env python3
"""Verify pinned sources and reproducibly generate the Codex-facing pstack tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile


REVISION = "5bf2b1544db739998121a306340631963c2ff3de"
VERSION = "0.15.2"
OWNED_DIRECTORIES = {"skills", "agents", "automations", "companion-skills"}
NOTICE_START = "<!-- pstack-codex:host -->\n"
NOTICE_END = "<!-- /pstack-codex:host -->\n"


class BuildError(Exception):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def safe_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or str(path) != value:
        raise BuildError(f"Unsafe manifest path: {value!r}")
    return path


def verify_manifest(root: Path, manifest_path: Path, package: str) -> tuple[dict, dict[str, bytes]]:
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        raise BuildError(f"Cannot read pinned manifest {manifest_path.name}: {exc}") from exc
    if manifest.get("revision") != REVISION:
        raise BuildError(f"Unreviewed revision in {manifest_path.name}")
    if package == "pstack" and manifest.get("version") != VERSION:
        raise BuildError("Unreviewed pstack version")
    contents = {}
    for entry in manifest.get("files", []):
        relative = str(safe_path(entry["path"]))
        if relative in contents:
            raise BuildError(f"Duplicate manifest entry: {relative}")
        expected_url = f"https://raw.githubusercontent.com/cursor/plugins/{REVISION}/{package}/{relative}"
        if entry.get("url") != expected_url:
            raise BuildError(f"Source URL does not match pinned revision: {relative}")
        path = root / relative
        if path.is_symlink() or not path.is_file() or any(p.is_symlink() for p in path.parents if p != root.parent):
            raise BuildError(f"Missing or symlinked source: {package}/{relative}")
        data = path.read_bytes()
        if len(data) != entry.get("bytes") or digest(data) != entry.get("sha256"):
            raise BuildError(f"Source integrity mismatch: {package}/{relative}")
        contents[relative] = data
    if not contents:
        raise BuildError(f"Empty manifest: {manifest_path.name}")
    actual = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
    if actual != set(contents):
        raise BuildError(f"Unmanifested or missing files in {package}: {sorted(actual ^ set(contents))}")
    if "all_paths" in manifest:
        declared = {p.removeprefix(package + "/") for p in manifest["all_paths"]}
        if declared != set(contents):
            raise BuildError(f"Incomplete source acquisition in {package}")
    return manifest, contents


def frontmatter(data: bytes) -> tuple[dict, str, str]:
    text = data.decode("utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise BuildError("Expected complete YAML frontmatter")
    header, body = text[4:].split("\n---\n", 1)
    fields = {}
    lines = header.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line or line.startswith("#"):
            index += 1
            continue
        match = re.fullmatch(r"([a-zA-Z][\w-]*):\s*(.*)", line)
        if not match:
            raise BuildError(f"Unsupported source frontmatter syntax: {line!r}")
        key, value = match.groups()
        if key in fields:
            raise BuildError(f"Duplicate frontmatter key: {key}")
        index += 1
        if value in (">", ">-", "|", "|-"):
            block = []
            while index < len(lines) and lines[index].startswith("  "):
                block.append(lines[index][2:])
                index += 1
            fields[key] = (" " if value.startswith(">") else "\n").join(block)
        elif value.startswith('"') or value.startswith("["):
            fields[key] = json.loads(value)
        elif value.startswith("'") and value.endswith("'"):
            fields[key] = value[1:-1].replace("''", "'")
        elif value in ("true", "false"):
            fields[key] = value == "true"
        else:
            fields[key] = value
    if not isinstance(fields.get("name"), str) or not isinstance(fields.get("description"), str):
        raise BuildError("Source name and description must be strings")
    return fields, header, body


def notice(output: str) -> str:
    relative = os.path.relpath("adapters/host.md", str(PurePosixPath(output).parent)).replace(os.sep, "/")
    setup_notice = ""
    if output == "skills/setup-pstack/SKILL.md":
        setup_path = os.path.relpath("docs/setup.md", str(PurePosixPath(output).parent)).replace(os.sep, "/")
        setup_notice = f" For model setup, follow the [Codex setup procedure]({setup_path}) and its schema instead of the Cursor rule-file steps below; preserve the original discovery, budget, and confirmation decisions."
    return (
        NOTICE_START +
        f"> **Codex host contract.** Read [{relative}]({relative}) before executing this workflow. "
        "It translates Cursor tools, paths, models, and activation without replacing the workflow below." + setup_notice + "\n"
        + NOTICE_END
    )


def adapt_entry(data: bytes, output: str, name: str) -> tuple[bytes, list[dict], dict]:
    fields, header, body = frontmatter(data)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) >= 64:
        raise BuildError(f"Invalid Codex skill name: {name}")
    adapted_header = f"name: {name}\ndescription: {json.dumps(fields['description'], ensure_ascii=False)}"
    result = ("---\n" + adapted_header + "\n---\n" + notice(output) + body).encode()
    operations = [
        {"id": "codex-frontmatter", "upstream_frontmatter": header, "name": name},
        {"id": "host-contract-notice"},
    ]
    return result, operations, fields


def owned_output(path: str) -> bool:
    p = safe_path(path)
    return p.parts[0] in OWNED_DIRECTORIES or path in {"docs/routing-index.md", "adaptations.json"}


def agent_metadata(name: str, display_name: str, implicit: bool) -> bytes:
    short = f"Use pstack's {name.replace('-', ' ')} workflow."
    if len(short) > 64:
        short = short[:61].rsplit(" ", 1)[0] + "..."
    return (
        "interface:\n"
        f"  display_name: {json.dumps(display_name)}\n"
        f"  short_description: {json.dumps(short)}\n"
        f"  default_prompt: {json.dumps('$pstack-codex:poteto-mode work through this task.' if name == 'poteto-mode' else 'Use $' + name + ' for this task.')}\n"
        "policy:\n"
        f"  allow_implicit_invocation: {str(implicit).lower()}\n"
    ).encode()


def render(root: Path) -> tuple[dict[str, tuple[bytes, int]], dict]:
    upstream = root / "upstream"
    manifest, sources = verify_manifest(upstream / "pstack", upstream / "source-manifest.json", "pstack")
    companion_manifest, companions = verify_manifest(
        upstream / "cursor-team-kit", upstream / "companion-manifest.json", "cursor-team-kit"
    )
    registered = sorted(p for p in sources if re.fullmatch(r"skills/[^/]+/SKILL\.md", p))
    playbooks = sorted(p for p in sources if p.startswith("skills/poteto-mode/playbooks/") and p.endswith(".md"))
    dormant = sorted(p for p in sources if p.startswith("automations/") and p.endswith("/SKILL.md"))
    if len(registered) != 47 or len(playbooks) != 23 or len(dormant) != 3:
        raise BuildError("Pinned catalog shape changed; review the new source before building")
    expected_companions = {"skills/deslop/SKILL.md", "skills/control-cli/SKILL.md", "skills/control-ui/SKILL.md"}
    if not expected_companions.issubset(companions):
        raise BuildError("Incomplete companion catalog")
    outputs: dict[str, tuple[bytes, int]] = {}
    records = []
    skills = []

    def add(path: str, data: bytes, *, source: str | None = None, original: bytes | None = None,
            operations: list[dict] | None = None, mode: int | None = None) -> None:
        if not owned_output(path) or path in outputs:
            raise BuildError(f"Unexpected or duplicate output: {path}")
        mode = mode if mode is not None else (0o755 if data.startswith(b"#!") else 0o644)
        outputs[path] = (data, mode)
        records.append({"output": path, "sha256": digest(data), "mode": oct(mode),
                        "source": source, "source_sha256": digest(original) if original is not None else None,
                        "operations": operations or []})

    for path, original in sorted(sources.items()):
        if PurePosixPath(path).parts[0] not in {"skills", "agents", "automations"}:
            continue
        data, operations = original, []
        if path in registered:
            name = PurePosixPath(path).parent.name
            data, operations, fields = adapt_entry(original, path, name)
            implicit = not fields.get("disable-model-invocation", False)
            skills.append({"name": name, "path": path, "implicit": implicit, "upstream": fields})
            policy_path = str(PurePosixPath(path).parent / "agents/openai.yaml")
            policy = agent_metadata(name, fields["name"], implicit)
            add(policy_path, policy, source="upstream/pstack/" + path, original=original,
                operations=[{"id": "invocation-policy", "allow_implicit_invocation": implicit}])
        elif path.startswith("agents/") and path.endswith(".md"):
            data, operations, _ = adapt_entry(original, path, PurePosixPath(path).stem)
        elif path in playbooks:
            data = notice(path).encode() + original
            operations = [{"id": "host-contract-notice"}]
        add(path, data, source="upstream/pstack/" + path, original=original, operations=operations)

    for path, original in sorted(companions.items()):
        output = "companion-skills/" + path.removeprefix("skills/")
        data, operations = original, []
        if path in expected_companions:
            name = PurePosixPath(path).parent.name
            data, operations, fields = adapt_entry(original, output, name)
        add(output, data, source="upstream/cursor-team-kit/" + path, original=original, operations=operations)

    if sum(not s["implicit"] for s in skills) != 46:
        raise BuildError("Expected 46 explicit-only pstack skills and implicit setup-pstack")
    index = [
        "# Generated pstack routing index", "",
        f"Source: pstack {VERSION}, `{REVISION}`. Regenerate with `python3 scripts/build.py`.", "",
        "Read [the host contract](../adapters/host.md). The 47 skills comprise 24 workflow/utility skills and 23 principles. "
        "They are not all invoked on every task. `poteto-mode` retains its automatic routing while active; "
        "explicit-only discovery does not forbid its explicit calls to another packaged skill.", "",
        "Resolve named pstack skills to the paths below before considering similarly named skills outside this package. "
        "The original descriptions and routing bodies remain in those files. "
        "TypeScript `.ts`/`.tsx` path activation is implemented by the host contract, not unsupported frontmatter.", "",
        "| Skill | Discovery | Source trigger |", "|---|---|---|",
    ]
    for skill in skills:
        description = skill["upstream"]["description"].replace("|", "\\|").replace("\n", " ")
        index.append(f"| [{skill['name']}](../{skill['path']}) | {'implicit allowed' if skill['implicit'] else 'explicit only'} | {description} |")
    index += ["", "## Playbooks", ""]
    index += [f"- [{PurePosixPath(p).stem}](../{p})" for p in playbooks]
    index += ["", "## Companion skills and agent roles", ""]
    index += ["The three companions are path-loaded dependencies, not registered Codex skills. Load the exact files linked below; never resolve these calls to a same-named external skill or slash command.", ""]
    index += [f"- [{PurePosixPath(p).parent.name}](../companion-skills/{p.removeprefix('skills/')})" for p in sorted(expected_companions)]
    index += ["- [poteto-agent](../agents/poteto-agent.md)", "- [Comment Sicko](../agents/comment-sicko.md)", "",
              "## Dormant Benny pack", "",
              "[Enter through FOR_AGENTS.md](../automations/benny/FOR_AGENTS.md) with the host contract loaded. "
              "Its three SKILL.md files are direct automation instructions, not registered slash skills. "
              "The pack is byte-preserved; Cursor-specific triggers and editor flows require a verified host adapter. "
              "No automation is enabled by this build.", ""]
    index += [f"- [{PurePosixPath(p).parent.name}](../{p})" for p in dormant]
    add("docs/routing-index.md", ("\n".join(index) + "\n").encode(), operations=[{"id": "routing-index"}])
    ledger = {
        "schema_version": 1, "generator_version": 1, "upstream_revision": REVISION, "upstream_version": VERSION,
        "source_manifest_sha256": digest((upstream / "source-manifest.json").read_bytes()),
        "companion_manifest_sha256": digest((upstream / "companion-manifest.json").read_bytes()),
        "verified_source_files": len(sources) + len(companions),
        "catalog": {"registered_skills": 47, "principles": 23, "playbooks": 23, "dormant_skills": 3,
                    "companion_skills": 3, "explicit_only_registered_skills": 46},
        "host_contract": "adapters/host.md", "adaptation_notes": "adapters/ADAPTATIONS.md",
        "unmodified_source_files": [p for p in sorted(sources) if PurePosixPath(p).parts[0] not in {"skills", "agents", "automations"}],
        "files": sorted(records, key=lambda record: record["output"]),
    }
    outputs["adaptations.json"] = (json_bytes(ledger), 0o644)
    return outputs, ledger


def build(root: Path, output: Path, *, check: bool = False) -> dict:
    root, output = root.resolve(), output.resolve()
    if output == root / "upstream" or root / "upstream" in output.parents:
        raise BuildError("Generated output cannot overwrite pinned upstream")
    expected, ledger = render(root)
    previous = output / "adaptations.json"
    old_paths = set()
    if previous.exists():
        try:
            old_paths = {item["output"] for item in json.loads(previous.read_text())["files"]}
        except (ValueError, KeyError, TypeError) as exc:
            raise BuildError("Cannot reconcile malformed prior adaptations.json") from exc
        if any(not owned_output(p) for p in old_paths):
            raise BuildError("Prior ledger claims files outside generated ownership")
    stale = old_paths - set(expected)
    differences = []
    for path, (data, mode) in expected.items():
        target = output / path
        if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != output.parent):
            raise BuildError(f"Symlink in generated output path: {path}")
        if not target.is_file() or target.read_bytes() != data or target.stat().st_mode & 0o777 != mode:
            differences.append(path)
    differences += sorted(stale)
    if check:
        if differences:
            raise BuildError("Generated output differs: " + ", ".join(sorted(differences)))
        return ledger
    for path in sorted(stale):
        target = output / path
        if target.is_symlink():
            raise BuildError(f"Refusing to remove symlinked stale output: {path}")
    for path, (data, mode) in expected.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        temporary.chmod(mode)
        temporary.replace(target)
    for path in sorted(stale):
        (output / path).unlink(missing_ok=True)
    return ledger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="Generate into an isolated directory instead of the package root")
    parser.add_argument("--check", action="store_true", help="Verify generated bytes and modes without writing")
    args = parser.parse_args()
    try:
        ledger = build(args.root, args.output or args.root, check=args.check)
    except (BuildError, OSError, ValueError) as exc:
        parser.exit(1, f"build: {exc}\n")
    print(json.dumps({"status": "verified" if args.check else "built", **ledger["catalog"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
