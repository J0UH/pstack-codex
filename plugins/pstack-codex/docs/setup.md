# Configure pstack's model roles in Codex

This is the Codex setup flow for the preserved [setup-pstack skill](../skills/setup-pstack/SKILL.md). Run the commands below from this plugin's directory. Setup configures future role dispatch. A JSON entry cannot change the model or reasoning effort of an already-running coordinator.

## 1. Inventory what the current host can actually run

Keep three observations separate: a model is listed, the provider is authenticated, and the requested model/profile has completed a verified invocation. None implies the next.

- **Native Codex:** inspect the current collaboration tool's advertised model identifiers, supported reasoning efforts and parameter rules. Use those exact identifiers. A model name in documentation or this repository's example is not an entitlement check. Record whether true parent inheritance is supported and which capabilities the worker receives.
- **Claude:** inspect the installed `claude --help` and local authentication status. Keep account identifiers private. Preserve the existing direct CLI login; do not add keys or a gateway. Check the desired model/effort with a harmless source-free analysis request through `scripts/claude_worker.py --spec <absolute-spec-path>`. Use a new run directory and an explicit exact model. Confirm a successful receipt, matching substantive response model, effective tool profile and route evidence. `--dry-run` checks launch arguments only. It does not prove authentication, model access or successful execution.
- **Optional Grok:** inspect `grok --help` and the read-only `grok models` list. Listing a model does not prove that the sandbox or tool profile starts. The current adapter accepts only the candidate analysis profile; its authoring-host live probe was blocked before inference. See [Grok status](grok.md). Do not downgrade a sandbox or switch providers to make setup appear successful. Require a verified invocation before advertising this route as usable.

The validator accepts the native effort vocabulary `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `ultra`. Actual per-model native support must come from the current tool schema. Claude configuration accepts only `low`, `medium`, `high`, `xhigh`, `max`, matching the installed CLI's inspected vocabulary. Grok accepts those same five pstack budget tokens as configuration syntax only; provider/model support remains subject to discovery and a successful probe. Schema validation does not establish entitlement or availability for any backend.

If a required role has no supported route, name that role and request a specific choice. Do not silently substitute a different model, provider, effort or permission profile.

## 2. Load the existing choices

```sh
python3 scripts/pstack.py models path
python3 scripts/pstack.py models show
```

The path defaults to `$CODEX_HOME/pstack/models.json`, or `~/.codex/pstack/models.json` when `CODEX_HOME` is unset. An absolute `PSTACK_MODEL_CONFIG` override selects an exact file for a project or isolated test. It is not a merge layer. Set the same override on validation, installation and subsequent dispatch commands.

On a rerun, retain existing role choices, aliases, panel membership and descriptive fields unless the user changes them. If there is no file, use the original skill's role table as the proposed defaults, marking unsupported entries as needing a choice. Cursor model slugs are not interchangeable with native or provider CLI identifiers.

Partial role overrides are valid, including an empty `roles` object. A missing role means unresolved against the upstream default; it does not mean “use the coordinator” or “use the first optional backend.” Use that source default only after verifying its host mapping and support. Otherwise ask for that specific role. The validator never fills missing roles or substitutes a model.

## 3. Pick the budget and confirm the role table

Offer the original choices and identify the currently recorded budget:

- `unlimited — keep max`
- `large — xhigh reasoning`
- `medium — high reasoning`
- `small — medium reasoning`

`unlimited` preserves each role's existing/default effort. The other choices target `xhigh`, `high`, or `medium` for explicit models, including every panel entry. Inheritance aliases remain unchanged. Keep model identity separate from effort instead of editing provider model-name suffixes. If the exact model supports only a lower effort, present that supported choice explicitly before saving; do not silently change the model to satisfy the budget label.

Show every upstream role, its exact backend/model/effort, capability evidence, and any unresolved choice. Ask the user to accept the table or change specified roles. A panel's list length is its candidate/reviewer count. The arena cross-judge pool is a list of eligible choices from which the workflow selects one, preferably from another family. Same-model or inherited entries still count toward panel size.

`coordinator` is an optional additional entry that records intended coordination policy. Changing it does not mutate the active Codex task's model settings. Use a genuinely supported explicit runtime selection for future workers; an existing coordinator continues with its actual model and effort.

## 4. Prepare and validate the explicit JSON

The [JSON Schema](../schemas/models.schema.json) lists the exact role labels and permitted entry shapes. The runtime validator is `validate_model_config` in [model_config.py](../scripts/model_config.py). Both reject unknown role names, unknown backends, invalid effort tokens, empty panels and panel/single shape mismatches.

This minimal partial configuration illustrates a single role, a panel and true inheritance. These model names are examples to confirm through step 1, not a promise of account access.

```json
{
  "schema_version": 1,
  "budget": "large",
  "roles": {
    "bug-fix": {
      "backend": "claude",
      "model": "claude-fable-5-1",
      "effort": "xhigh"
    },
    "how explorer": {
      "backend": "native",
      "model": "inherit-parent"
    },
    "arena runners": [
      {"backend": "native", "model": "auto"},
      {"backend": "claude", "model": "claude-fable-5-1", "effort": "xhigh"}
    ]
  }
}
```

Save the approved draft as `models.proposed.json`. Explicit model entries require an effort. Native `inherit-parent` and `auto` entries omit both explicit model selection and effort at dispatch; their configuration must not contain an effort or a CLI profile. They inherit the actual parent, not an arbitrary backend default.

For Claude/Grok entries, optional `profile` is `analysis`, `reader` or `writer`. Choose it from the current task's capabilities, not from model identity. Omit it when the dispatcher should choose from the authorized task. A syntactically valid profile can still be unsupported by the adapter, especially Grok reader/writer. A native profile is rejected because a JSON label cannot fabricate native read-only or write containment.

`description`, `profile_note` and the optional budget label are metadata. `optional_backends` can record known models, effort tokens, profiles, status and reason, but it does not activate a role or provide fallback routing. Keep credentials out of every field.

```sh
python3 scripts/pstack.py models validate --file models.proposed.json
```

Resolve every validation failure before installation. This command validates configuration only and must not call a provider. Separately confirm each intended active route from the capability evidence in step 1.

## 5. Install the approved configuration atomically

After the role table is approved, the following local command validates again and replaces only the selected model-policy file. It uses the same path resolver as `models path` and writes through a temporary file followed by an atomic replacement. It does not change Codex's global model settings.

```sh
python3 - <<'PY'
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("scripts").resolve()))
from model_config import validate_model_config
from pstack import atomic_json, config_path

approved = validate_model_config(json.loads(Path("models.proposed.json").read_text()))
destination = config_path()
atomic_json(destination, approved)
print(destination)
PY
python3 scripts/pstack.py models show
```

Confirm the saved path and role choices. Re-running setup updates this file while preserving choices the user has not changed. New dispatches must resolve the current explicit policy; already-running workers keep the spec they received.

## 6. Offer project verification once

If the project has no reproducible way to drive its real app, offer the packaged `create-verification-skill`. Follow it only if the user accepts. A successful configuration check or model probe is not verification of the user's application.
