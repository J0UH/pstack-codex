#!/usr/bin/env python3
"""Read-only prerequisite diagnosis for pstack-codex.

Components: the Codex CLI (coordinator), the Claude Code CLI (core worker), the
optional Grok Build CLI (optional worker) and the optional Grok Bot desktop app.
A missing or blocked optional component never changes the core verdict.

The report keeps separate questions separate, because none implies the next:

* installed      - a binary answered ``--version`` or an app bundle has metadata;
* auth           - ``needs_login`` when a read-only command printed a negative
                   marker, otherwise ``unknown``.  Exit code 0, a printed model
                   list and the presence of credential files are never proof;
* sandbox_probe  - classified only from a receipt the caller supplies, never run;
* inference      - ``verified_by_supplied_receipt`` only when a supplied worker
                   receipt is internally consistent (schema, backend, status,
                   completion, model match, clean exit).  That is user-supplied
                   evidence, not a live measurement by this tool.

By default the doctor runs exactly ``codex --version``, ``claude --version``,
``grok --version`` and the read-only ``grok models`` listing, each with stdin
closed under a bounded timeout.  It performs no login, install, settings change,
inference or network call of its own.  It reads no credential files.  From
supplied evidence it opens only the receipt file itself and a ``stderr.txt``
beside it, never a path named inside the receipt.  It prints no environment
values, account identifiers or absolute home paths.

Exit codes: 0 report produced; 1 the core Claude Code CLI is missing, failed its
version check, or the doctor itself failed; 2 a supplied receipt or argument was
invalid.  Failures are reported as JSON, never as tracebacks.

Standard library only.  Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from worker_common import ARTIFACTS as WORKER_ARTIFACTS  # noqa: E402
from worker_common import RECEIPT_SCHEMA as WORKER_RECEIPT_SCHEMA  # noqa: E402

REPORT_SCHEMA = "pstack-codex/doctor/1"

DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_TIMEOUT_SECONDS = 120.0
MAX_OUTPUT_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_REPORTED_ERRORS = 10
MAX_TEXT_CHARS = 300

# Every command this tool may execute.  Each is a read-only version or list call.
ALLOWED_COMMANDS: dict[str, tuple[str, ...]] = {
    "codex_version": ("codex", "--version"),
    "claude_version": ("claude", "--version"),
    "grok_version": ("grok", "--version"),
    "grok_models": ("grok", "models"),
}

# Names whose PRESENCE is reported.  Values are never copied into the report.
OVERRIDE_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_VERTEX",
    "GROK_CLI_CHAT_PROXY_BASE_URL",
    "OPENAI_API_KEY",
    "XAI_API_KEY",
)
# Values of these names, when set, are additionally erased from every string in the report.
SECRET_ENV_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "XAI_API_KEY")

GROK_BOT_APP_CANDIDATES = ("/Applications/Grok Bot.app", "~/Applications/Grok Bot.app")

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
VERSION_RE = re.compile(r"\b(\d+\.\d+(?:\.\d+)*)(\s*\([0-9A-Fa-f]{6,40}\))?")
NOT_AUTH_RE = re.compile(
    r"not\s+(?:yet\s+)?(?:authenticated|logged\s+in|signed\s+in)"
    r"|unauthenticated|authentication\s+required|login\s+required"
    r"|please\s+(?:log|sign)\s*in|run\s+`?grok\s+login",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TOKEN_RE = re.compile(r"\b(?:sk|xai|ghp|gho)-[A-Za-z0-9_\-]{8,}|\bbearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE)
ASSIGNMENT_RE = re.compile(r"\b(api[_-]?key|auth[_-]?token|token|secret|password)\s*[=:]\s*\S+", re.IGNORECASE)

SOCKET_SYMLINK_RE = re.compile(r"could not resolve runtime-socket deny path (?P<path>\S+?): endpoint is a symlink", re.IGNORECASE)
SANDBOX_REFUSED_RE = re.compile(r"could not apply the '(?P<profile>[^']+)' sandbox profile|sandbox could not be applied", re.IGNORECASE)
UNKNOWN_OPTION_RE = re.compile(r"unknown option|unrecognized (?:option|argument)|unexpected argument", re.IGNORECASE)

FAILURE_CLASSES: dict[str, dict[str, Any]] = {
    "sandbox_socket_symlink": {
        "summary": "Grok Build refused to start its read-only sandbox because a runtime-socket deny path is a symlink.",
        "prerequisites": [
            "Environment prerequisite, not a plugin setting: the sandbox resolves its runtime-socket deny list, refuses a symlinked endpoint and exits rather than start with protections missing.",
            "Repeat the disposable probe only where that socket path is a real socket or absent. Changing the socket, Docker or Grok configuration is an operator decision outside this plugin.",
            "Do not rerun with a downgraded or disabled sandbox and do not drop the deny rules.",
        ],
    },
    "sandbox_profile_refused": {
        "summary": "Grok Build refused to start because a sandbox profile could not be applied.",
        "prerequisites": [
            "Read the warning line before the refusal in the attempt's stderr.txt for the exact cause.",
            "Repair the environment so the requested profile applies; do not weaken or disable the sandbox.",
        ],
    },
    "not_authenticated": {
        "summary": "Grok Build reported that it is not authenticated.",
        "prerequisites": [
            "Complete the ordinary interactive `grok login` as the operator (this tool never runs it), then rerun the doctor.",
        ],
    },
    "unknown_option": {
        "summary": "The installed CLI rejected a control argument.",
        "prerequisites": ["Compare the installed `grok --help` with the adapter's documented argument list before changing anything."],
    },
    "unclassified": {
        "summary": "The receipt's failure matches no known pattern.",
        "prerequisites": ["Read the attempt's stderr.txt and receipt errors directly; do not infer a cause."],
    },
}
BLOCKING_CLASSES = frozenset({"sandbox_socket_symlink", "sandbox_profile_refused", "not_authenticated"})

INSTALL_ROLLUP = {"installed": "installed", "not_found": "not_installed", "check_failed": "check_failed"}

Runner = Callable[[list[str], float], dict[str, Any]]
Which = Callable[[str], "str | None"]


class DoctorError(ValueError):
    """Invalid caller input (unreadable receipt, bad JSON)."""


# --------------------------------------------------------------------------- text hygiene


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _short(text: Any, limit: int = MAX_TEXT_CHARS) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def extract_version(text: str) -> str | None:
    match = VERSION_RE.search(ANSI_RE.sub("", text or ""))
    if not match:
        return None
    return (match.group(1) + (match.group(2) or "")).strip()


class Scrubber:
    """Erases secret values, token-shaped strings, e-mail addresses and the home directory from report text."""

    def __init__(self, home: str, environ: dict[str, str]):
        self.home = home.rstrip(os.sep)
        values = {environ[name] for name in SECRET_ENV_NAMES if len(environ.get(name, "")) >= 8}
        self.secrets = sorted(values, key=len, reverse=True)

    def text(self, value: Any, limit: int | None = MAX_TEXT_CHARS) -> str:
        text = ANSI_RE.sub("", str(value))
        for secret in self.secrets:
            text = text.replace(secret, "<redacted>")
        text = TOKEN_RE.sub("<redacted>", text)
        text = ASSIGNMENT_RE.sub(r"\1=<redacted>", text)
        text = EMAIL_RE.sub("<email>", text)
        if self.home:
            text = text.replace(self.home, "~")
        text = text.strip()
        return text if limit is None else _short(text, limit)

    def path(self, path: str | None) -> str | None:
        if path is None:
            return None
        if self.home and (path == self.home or path.startswith(self.home + os.sep)):
            return "~" + path[len(self.home):]
        return path

    def walk(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value, None)
        if isinstance(value, dict):
            return {key: self.walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.walk(item) for item in value]
        return value


# --------------------------------------------------------------------------- command execution


def default_runner(argv: list[str], timeout: float) -> dict[str, Any]:
    """Run one allow-listed read-only command with stdin closed and bounded captured output."""
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"returncode": None, "stdout": "", "stderr": "", "error": f"timeout after {timeout:g}s"}
    except OSError as exc:
        return {"returncode": None, "stdout": "", "stderr": "", "error": type(exc).__name__}
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", "replace"),
        "stderr": completed.stderr[:MAX_OUTPUT_BYTES].decode("utf-8", "replace"),
        "error": None,
    }


class CommandLog:
    """Executes only ALLOWED_COMMANDS through the supplied runner and records what ran."""

    def __init__(self, runner: Runner, timeout: float, which: Which):
        self.runner = runner
        self.timeout = timeout
        self.which = which
        self.executed: list[list[str]] = []

    def run(self, key: str, executable: str) -> dict[str, Any]:
        template = ALLOWED_COMMANDS[key]
        self.executed.append(list(template))
        try:
            result = self.runner([executable, *template[1:]], self.timeout)
        except Exception as exc:  # noqa: BLE001 - a broken runner must not lose the report
            return {"returncode": None, "stdout": "", "stderr": "", "error": f"runner {type(exc).__name__}"}
        if not isinstance(result, dict):
            return {"returncode": None, "stdout": "", "stderr": "", "error": "runner returned no result"}
        returncode = result.get("returncode")
        return {
            "returncode": returncode if isinstance(returncode, int) and not isinstance(returncode, bool) else None,
            "stdout": str(result.get("stdout") or "")[:MAX_OUTPUT_BYTES],
            "stderr": str(result.get("stderr") or "")[:MAX_OUTPUT_BYTES],
            "error": str(result["error"]) if result.get("error") else None,
        }


def resolve_executable(name: str, which: Which, home: str) -> str | None:
    found = which(name)
    if found:
        return os.path.abspath(found)
    if name == "grok":  # same fallback as grok_worker.build_command
        candidate = os.path.join(home, ".grok", "bin", "grok")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


# --------------------------------------------------------------------------- classification


def classify_grok_failure(text: str, scrubber: Scrubber) -> dict[str, Any]:
    """Classify a Grok Build pre-inference failure from stderr text and receipt errors.  Never echoes the text."""
    text = ANSI_RE.sub("", text or "")
    symlink = SOCKET_SYMLINK_RE.search(text)
    refused = SANDBOX_REFUSED_RE.search(text)
    if symlink:
        key = "sandbox_socket_symlink"
        detail = f"runtime-socket deny path {scrubber.text(symlink.group('path'), 120)} is a symlink; the sandbox refused to start"
    elif refused:
        key = "sandbox_profile_refused"
        profile = refused.group("profile")
        detail = f"sandbox profile {scrubber.text(profile, 40)!r} could not be applied" if profile else "sandbox could not be applied"
    elif NOT_AUTH_RE.search(text):
        key = "not_authenticated"
        detail = "the CLI reported it is not authenticated"
    elif UNKNOWN_OPTION_RE.search(text):
        key = "unknown_option"
        detail = "the CLI rejected an argument"
    else:
        key = "unclassified"
        detail = "no known failure pattern matched"
    info = FAILURE_CLASSES[key]
    return {
        "classification": key,
        "detail": detail,
        "summary": info["summary"],
        "prerequisites": list(info["prerequisites"]),
        "policy_weakened": False,
    }


def classify_grok_models(result: dict[str, Any]) -> dict[str, Any]:
    """Grade the read-only listing.  Returns ``needs_login`` or ``unknown``, never a positive."""
    if result["error"]:
        return {"status": "unknown", "evidence": f"grok models did not complete: {result['error']}; authentication cannot be judged"}
    text = ANSI_RE.sub("", result["stdout"] + "\n" + result["stderr"])
    code = result["returncode"]
    if NOT_AUTH_RE.search(text):
        return {
            "status": "needs_login",
            "evidence": f"grok models exit {code} printed a not-authenticated marker; any model names printed are fallbacks, not entitlements",
        }
    return {
        "status": "unknown",
        "evidence": f"grok models exit {code} printed no not-authenticated marker; exit code and a model list are not proof of authentication",
    }


# --------------------------------------------------------------------------- supplied receipts


def load_receipt(path: Any) -> tuple[dict[str, Any], str]:
    """Read one caller-supplied receipt (or the receipt.json inside a supplied run_dir)."""
    if not isinstance(path, str) or not path:
        raise DoctorError("receipt path must be a non-empty string")
    path = os.path.abspath(path)
    if os.path.isdir(path):
        path = os.path.join(path, WORKER_ARTIFACTS["receipt"])
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_RECEIPT_BYTES + 1)
    except OSError as exc:
        raise DoctorError(f"receipt cannot be read: {exc.strerror or type(exc).__name__}") from exc
    if len(data) > MAX_RECEIPT_BYTES:
        raise DoctorError("receipt is larger than 4 MiB")
    try:
        receipt = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DoctorError(f"receipt is not valid JSON: {type(exc).__name__}") from exc
    if not isinstance(receipt, dict):
        raise DoctorError("receipt must be a JSON object")
    return receipt, os.path.dirname(path)


def read_sibling_stderr(receipt_dir: str) -> str | None:
    """Read the worker's stderr.txt beside the receipt.  Paths named inside the receipt are never opened."""
    path = os.path.join(receipt_dir, WORKER_ARTIFACTS["stderr"])
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            return handle.read(MAX_OUTPUT_BYTES).decode("utf-8", "replace")
    except OSError:
        return None


def analyze_worker_receipt(path: str, expected_backend: str, scrubber: Scrubber) -> dict[str, Any]:
    """Summarize a worker receipt as user-supplied evidence, checking its fields agree with each other."""
    receipt, receipt_dir = load_receipt(path)

    def text_field(name: str) -> str | None:
        value = receipt.get(name)
        return value if isinstance(value, str) else None

    schema = text_field("schema")
    backend = text_field("backend")
    status = text_field("status")
    lifecycle = text_field("lifecycle")
    requested = text_field("requested_model")
    returncode = receipt.get("returncode")
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        returncode = None
    observed_raw = receipt.get("observed_models")
    observed = [m for m in observed_raw if isinstance(m, str)] if isinstance(observed_raw, list) else []
    errors_raw = receipt.get("errors")
    error_count = len(errors_raw) if isinstance(errors_raw, list) else None
    errors = [scrubber.text(item, 200) for item in errors_raw[:MAX_REPORTED_ERRORS]] if isinstance(errors_raw, list) else []

    reasons: list[str] = []
    if schema != WORKER_RECEIPT_SCHEMA:
        reasons.append(f"schema is {schema!r}, expected {WORKER_RECEIPT_SCHEMA!r}")
    if backend != expected_backend:
        reasons.append(f"backend is {backend!r}, expected {expected_backend!r}")
    if status != "success":
        reasons.append(f"status is {status!r}, not 'success'")
    if receipt.get("complete") is not True:
        reasons.append("complete is not true")
    if receipt.get("requested_model_verified") is not True:
        reasons.append("requested_model_verified is not true")
    if receipt.get("provider_is_error") is True:
        reasons.append("provider_is_error is true")
    if lifecycle != "exited":
        reasons.append(f"lifecycle is {lifecycle!r}, not 'exited'")
    if returncode != 0:
        reasons.append(f"returncode is {returncode!r}, not 0")
    if not requested:
        reasons.append("requested_model missing")
    if not observed:
        reasons.append("observed_models is empty")
    elif requested and any(model != requested for model in observed):
        reasons.append("observed_models do not all match requested_model")
    if error_count is None:
        reasons.append("errors list missing")
    elif error_count:
        reasons.append(f"errors present ({error_count})")
    verified = not reasons

    summary: dict[str, Any] = {
        "evidence_kind": "user_supplied_receipt",
        "live_measurement": False,
        "schema": schema,
        "backend": backend,
        "status": status,
        "lifecycle": lifecycle,
        "returncode": returncode,
        "requested_model": requested,
        "observed_models": observed[:10],
        "complete": receipt.get("complete") is True,
        "requested_model_verified_claimed": receipt.get("requested_model_verified") is True,
        "error_count": error_count,
        "errors": errors,
        "inference": {
            "status": "verified_by_supplied_receipt" if verified else "unverified",
            "evidence_kind": "user_supplied_receipt",
            "live_measurement": False,
            "reasons": reasons,
        },
    }
    if expected_backend == "grok":
        stderr_text = read_sibling_stderr(receipt_dir)
        summary["stderr_source"] = (
            f"{WORKER_ARTIFACTS['stderr']} beside the receipt" if stderr_text is not None else "unavailable (only the receipt's own directory is read)"
        )
        if verified:
            summary["sandbox_probe"] = {
                "status": "unverified",
                "classification": None,
                "detail": "a successful inference receipt does not independently establish sandbox enforcement",
                "prerequisites": ["Verify the intended protections with a separate boundary probe; launch arguments alone are not proof."],
                "policy_weakened": None,
                "evidence_kind": "user_supplied_receipt",
            }
        else:
            classification = classify_grok_failure((stderr_text or "") + "\n" + "\n".join(errors), scrubber)
            blocked = classification["classification"] in BLOCKING_CLASSES
            summary["sandbox_probe"] = {"status": "blocked" if blocked else "failed_unclassified", **classification, "evidence_kind": "user_supplied_receipt"}
    return summary


# --------------------------------------------------------------------------- components


def check_cli(log: CommandLog, key: str, name: str, home: str, scrubber: Scrubber) -> dict[str, Any]:
    executable = resolve_executable(name, log.which, home)
    if executable is None:
        return {"status": "not_found", "version": None, "path": None, "evidence": f"{name} not found on PATH"}
    result = log.run(key, executable)
    path = scrubber.path(executable)
    if result["error"]:
        return {"status": "check_failed", "version": None, "path": path, "evidence": f"{name} --version did not complete: {result['error']}"}
    version = extract_version(result["stdout"]) or extract_version(result["stderr"])
    if result["returncode"] != 0 or version is None:
        return {"status": "check_failed", "version": version, "path": path, "evidence": f"{name} --version exit {result['returncode']} without a recognizable version"}
    return {"status": "installed", "version": version, "path": path, "evidence": f"{name} --version exit 0"}


def _no_receipt(flag: str) -> dict[str, Any]:
    return {"status": "unverified", "evidence_kind": None, "live_measurement": False, "reasons": [f"no {flag} supplied"]}


def check_codex(log: CommandLog, home: str, scrubber: Scrubber) -> dict[str, Any]:
    installed = check_cli(log, "codex_version", "codex", home, scrubber)
    return {
        "role": "coordinator",
        "optional": False,
        "status": INSTALL_ROLLUP[installed["status"]],
        "installed": installed,
        "auth": {"status": "not_checked", "detail": "Codex session identity and hook trust are observed in the running Codex session, not by a shell command."},
        "note": "A running Codex session is itself the proof that Codex works; this check only reports whether the codex CLI answers --version.",
    }


def check_claude(log: CommandLog, home: str, scrubber: Scrubber, receipt: dict[str, Any] | None) -> dict[str, Any]:
    installed = check_cli(log, "claude_version", "claude", home, scrubber)
    inference = receipt["inference"] if receipt else _no_receipt("--claude-receipt")
    verified = inference["status"] == "verified_by_supplied_receipt"
    if installed["status"] != "installed":
        status = INSTALL_ROLLUP[installed["status"]]
    else:
        status = "verified_by_supplied_receipt" if verified else "installed_auth_unknown"
    return {
        "role": "core_worker",
        "optional": False,
        "status": status,
        "installed": installed,
        "auth": {
            "status": "verified_by_supplied_receipt" if verified else "unknown",
            "detail": "No read-only status command is used and no credential file is read; a consistent successful worker receipt is the only accepted proof.",
        },
        "inference": inference,
    }


def check_grok_build(log: CommandLog, home: str, scrubber: Scrubber, receipt: dict[str, Any] | None, run_models: bool) -> dict[str, Any]:
    installed = check_cli(log, "grok_version", "grok", home, scrubber)
    if installed["status"] != "installed":
        auth: dict[str, Any] = {"status": "not_checked", "evidence": "grok models not run: grok is not installed or failed its version check"}
    elif not run_models:
        auth = {"status": "not_checked", "evidence": "grok models skipped by --skip-grok-models"}
    else:
        executable = resolve_executable("grok", log.which, home) or "grok"
        auth = classify_grok_models(log.run("grok_models", executable))
    auth["note"] = "Never inferred from exit code 0, a model list or credential files; positive proof is a verified protected worker receipt."

    if receipt:
        sandbox = receipt["sandbox_probe"]
        inference = receipt["inference"]
    else:
        sandbox = {
            "status": "not_run",
            "classification": None,
            "prerequisites": [],
            "policy_weakened": False,
            "detail": "the doctor never launches Grok inference; supply --grok-receipt from a disposable grok_worker.py run",
        }
        inference = _no_receipt("--grok-receipt")

    blockers: list[str] = []
    if auth["status"] == "needs_login":
        blockers.append("needs_login")
    if sandbox["status"] == "blocked":
        blocker = "needs_login" if sandbox["classification"] == "not_authenticated" else sandbox["classification"]
        if blocker not in blockers:
            blockers.append(blocker)
    if installed["status"] != "installed":
        status = INSTALL_ROLLUP[installed["status"]]
    elif "needs_login" in blockers:
        status = "needs_login"
    elif blockers:
        status = "sandbox_blocked"
    elif inference["status"] == "verified_by_supplied_receipt":
        status = "verified_by_supplied_receipt"
    else:
        status = "installed_auth_unknown"
    return {
        "role": "optional_worker",
        "optional": True,
        "status": status,
        "blockers": blockers,
        "installed": installed,
        "auth": auth,
        "sandbox_probe": sandbox,
        "inference": inference,
        "note": "Optional. Core Codex and Claude work does not depend on it.",
    }


def discover_app_bundle(candidates: list[str] | tuple[str, ...], home: str, scrubber: Scrubber) -> dict[str, Any]:
    """Installed-only detection from macOS app bundle metadata.  Nothing is launched or inspected at runtime."""
    detection = "app bundle metadata only; no executable is run and no process is inspected"
    for candidate in candidates:
        path = os.path.join(home, candidate[2:]) if candidate.startswith("~/") else candidate
        if not os.path.isdir(path):
            continue
        version = identifier = None
        evidence = "app bundle directory present; Info.plist missing"
        plist_path = os.path.join(path, "Contents", "Info.plist")
        if os.path.isfile(plist_path):
            try:
                with open(plist_path, "rb") as handle:
                    info = plistlib.load(handle)
                if isinstance(info, dict):
                    if isinstance(info.get("CFBundleShortVersionString"), str):
                        version = info["CFBundleShortVersionString"]
                    if isinstance(info.get("CFBundleIdentifier"), str):
                        identifier = info["CFBundleIdentifier"]
                evidence = "Info.plist read"
            except (OSError, ValueError, plistlib.InvalidFileException):
                evidence = "app bundle directory present; Info.plist unreadable"
        return {"status": "installed", "version": version, "bundle_identifier": identifier, "path": scrubber.path(path), "evidence": evidence, "detection": detection}
    names = sorted({os.path.basename(candidate.rstrip(os.sep)) or candidate for candidate in candidates})
    return {
        "status": "not_found",
        "version": None,
        "bundle_identifier": None,
        "path": None,
        "evidence": f"no app bundle found among {len(candidates)} candidate location(s) for: " + ", ".join(names),
        "detection": detection,
    }


def check_grok_bot(candidates: list[str] | tuple[str, ...], home: str, scrubber: Scrubber) -> dict[str, Any]:
    installed = discover_app_bundle(candidates, home, scrubber)
    return {
        "role": "optional_app",
        "optional": True,
        "status": INSTALL_ROLLUP[installed["status"]],
        "installed": installed,
        "auth": {"status": "not_checked", "detail": "Sign-in state is visible only in the Grok Bot app UI; it is not inferred from the app bundle."},
        "runtime": {"status": "not_checked", "detail": "The doctor does not inspect processes or launch the app."},
        "webhook": {
            "status": "not_checked",
            "detail": "Validate a routine config offline with `python3 scripts/grok_bot.py check --config <file>` (no network call). A probe send is a separate explicit action.",
        },
        "note": "Optional. Core Codex and Claude work does not depend on it.",
    }


# --------------------------------------------------------------------------- report


def summarize(components: dict[str, Any]) -> list[str]:
    def installed_text(component: dict[str, Any], noun: str) -> str:
        installed = component["installed"]
        if installed["status"] == "installed":
            return f"{noun} {installed['version'] or 'version unknown'} present"
        return f"{noun} {installed['status'].replace('_', ' ')}"

    codex, claude, grok, bot = (components[name] for name in ("codex", "claude", "grok_build", "grok_bot"))
    lines = [
        f"codex (coordinator): {installed_text(codex, 'CLI')}; session auth not checked here",
        f"claude (core worker): {installed_text(claude, 'CLI')}; auth {claude['auth']['status'].replace('_', ' ')}; inference {claude['inference']['status'].replace('_', ' ')}",
    ]
    grok_line = f"grok_build (optional): {installed_text(grok, 'CLI')}; auth {grok['auth']['status'].replace('_', ' ')}"
    if grok["sandbox_probe"]["status"] in {"blocked", "failed_unclassified"}:
        grok_line += f"; sandbox probe {grok['sandbox_probe']['status'].replace('_', ' ')} per supplied receipt: {grok['sandbox_probe']['classification']}"
    elif grok["sandbox_probe"]["status"] == "unverified":
        grok_line += "; sandbox enforcement unverified"
    lines.append(grok_line + "; not required for core")
    lines.append(f"grok_bot (optional): {installed_text(bot, 'app bundle')}; sign-in and runtime not checked; not required for core")
    return lines


def build_report(
    *,
    runner: Runner | None = None,
    which: Which | None = None,
    environ: dict[str, str] | None = None,
    home: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    run_grok_models: bool = True,
    grok_receipt: str | None = None,
    claude_receipt: str | None = None,
    grok_bot_app: str | None = None,
) -> dict[str, Any]:
    runner = default_runner if runner is None else runner
    which = shutil.which if which is None else which
    environ = dict(os.environ) if environ is None else dict(environ)
    home = os.path.expanduser("~") if home is None else home
    scrubber = Scrubber(home, environ)
    log = CommandLog(runner, timeout, which)

    problems: list[str] = []
    supplied: dict[str, Any] = {}
    evidence: dict[str, dict[str, Any] | None] = {"grok": None, "claude": None}
    for label, backend, path in (("grok_receipt", "grok", grok_receipt), ("claude_receipt", "claude", claude_receipt)):
        if path is None:
            continue
        try:
            evidence[backend] = analyze_worker_receipt(path, backend, scrubber)
            supplied[label] = evidence[backend]
        except DoctorError as exc:
            problems.append(f"{label}: {exc}")
            supplied[label] = {"error": str(exc)}

    candidates = [grok_bot_app] if grok_bot_app else list(GROK_BOT_APP_CANDIDATES)
    components = {
        "codex": check_codex(log, home, scrubber),
        "claude": check_claude(log, home, scrubber, evidence["claude"]),
        "grok_build": check_grok_build(log, home, scrubber, evidence["grok"], run_grok_models),
        "grok_bot": check_grok_bot(candidates, home, scrubber),
    }

    claude_status = components["claude"]["status"]
    core_ready = claude_status not in {"not_installed", "check_failed"}
    core = {
        "components": ["codex", "claude"],
        "status": claude_status if core_ready else f"claude_{claude_status}",
        "detail": (
            "Claude Code CLI is installed; its authentication is unknown until a consistent successful worker receipt is supplied"
            if claude_status == "installed_auth_unknown"
            else "Claude Code CLI is installed and a supplied receipt is a consistent successful run (user-supplied evidence)"
            if claude_status == "verified_by_supplied_receipt"
            else "Claude Code CLI is missing or failed its version check; core worker dispatch is not possible on this PATH"
        ),
    }
    if components["codex"]["status"] != "installed":
        core["codex_note"] = "codex CLI did not answer --version on this PATH; a running Codex session is unaffected by this check"
    optional = {
        "components": ["grok_build", "grok_bot"],
        "statuses": {name: components[name]["status"] for name in ("grok_build", "grok_bot")},
        "detail": "Optional components. A missing, unauthenticated or blocked optional component does not affect the core verdict.",
    }

    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": utc_now(),
        "platform": {"system": platform.system(), "release": platform.release(), "python": platform.python_version()},
        "policy": {
            "commands_run": log.executed,
            "command_timeout_seconds": timeout,
            "read_only_commands_only": True,
            "login_performed": False,
            "inference_performed": False,
            "settings_modified": False,
            "credential_files_read": False,
            "doctor_network_calls": False,
            "supplied_evidence_files_read": ["the receipt file itself", f"{WORKER_ARTIFACTS['stderr']} beside it"],
            "note": "grok models is the installed CLI's own read-only listing; whether that CLI contacts its provider is outside this tool's control.",
        },
        "environment": {"override_variables_present": sorted(name for name in OVERRIDE_ENV_NAMES if name in environ), "values_shown": False},
        "core": core,
        "optional": optional,
        "components": components,
        "evidence_supplied": supplied,
        "problems": problems,
        "summary": summarize(components),
        "exit_code": 2 if problems else (0 if core_ready else 1),
    }
    return scrubber.walk(report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doctor.py", description="Read-only prerequisite diagnosis for Codex, Claude Code, optional Grok Build and optional Grok Bot.")
    parser.add_argument("--grok-receipt", help="receipt.json (or its run_dir) from a disposable grok_worker.py run; user-supplied evidence")
    parser.add_argument("--claude-receipt", help="receipt.json (or its run_dir) from a claude_worker.py run; user-supplied evidence")
    parser.add_argument("--grok-bot-app", help="explicit Grok Bot app bundle path instead of the default candidates")
    parser.add_argument("--skip-grok-models", action="store_true", help="do not run the read-only `grok models` listing")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help=f"per-command timeout in seconds (max {MAX_TIMEOUT_SECONDS:g})")
    return parser


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(
    argv: list[str] | None = None,
    *,
    runner: Runner | None = None,
    which: Which | None = None,
    environ: dict[str, str] | None = None,
    home: str | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if not (args.timeout == args.timeout and 0 < args.timeout <= MAX_TIMEOUT_SECONDS):
        _emit({"schema": REPORT_SCHEMA, "status": "error", "error": f"--timeout must be between 0 and {MAX_TIMEOUT_SECONDS:g} seconds"})
        return 2
    try:
        report = build_report(
            runner=runner,
            which=which,
            environ=environ,
            home=home,
            timeout=args.timeout,
            run_grok_models=not args.skip_grok_models,
            grok_receipt=args.grok_receipt,
            claude_receipt=args.claude_receipt,
            grok_bot_app=args.grok_bot_app,
        )
    except Exception as exc:  # noqa: BLE001 - never print a traceback; it could carry paths or output
        scrubber = Scrubber(os.path.expanduser("~") if home is None else home, dict(os.environ) if environ is None else environ)
        _emit({"schema": REPORT_SCHEMA, "status": "error", "error": f"{type(exc).__name__}: {scrubber.text(exc, 200)}"})
        return 1
    _emit(report)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
