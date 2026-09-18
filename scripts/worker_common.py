#!/usr/bin/env python3
"""Common subprocess lifecycle for pstack-codex CLI workers.

Backend adapters (for example ``claude_worker.py``) validate their inputs,
build an argv, and call :func:`run_process`.  Everything backend-independent
lives here:

* spec validation (types, absolute paths, finite timeout, run_dir disjoint from cwd)
* exclusive claim of a NEW run_dir per attempt (atomic ``mkdir``)
* launch intent persisted BEFORE the child is spawned
* spawning with ``shell=False`` and its own process group / session
* timeout enforcement: SIGTERM to the group, grace period, SIGKILL, wait
* parent SIGINT / SIGTERM / SIGHUP handling so the child is not orphaned; a
  signal the launcher inherited as ignored stays ignored
* raw stdout (JSONL) and stderr captured to files with 0600 permissions
* JSONL parsing after confirmed exit, with parse errors preserved
* a bounded receipt that never contains the raw transcript; permission
  denials are surfaced as warnings without changing the delivery status

The parser supplied by the adapter is called as
``parse_events(events, spec) -> dict`` and must return at least
``result_text`` (str), ``observed_models`` (list of str, models attributed to
actual response messages, never usage aggregates), ``is_error`` (bool) and
``complete`` (bool, a terminal result event was seen).  It may add
``auxiliary_models``, ``tool_calls``, ``errors``, ``warnings`` and
``evidence``.

Delivery status is not semantic acceptance: ``success`` means the requested
model produced a complete, error-free result through a cleanly exited
process.  Whether the answer is correct remains the caller's judgement.

Standard library only.  Python 3.10+.  POSIX only (process groups).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

RECEIPT_SCHEMA = "pstack-codex/worker-receipt/1"
BACKENDS = ("claude", "grok")
PROFILES = ("analysis", "reader", "writer")

MAX_TIMEOUT_SECONDS = 24 * 60 * 60
MAX_TERM_GRACE_SECONDS = 600.0
DEFAULT_TERM_GRACE_SECONDS = 5.0
KILL_WAIT_SECONDS = 10.0
MAX_ERRORS = 50
MAX_ERROR_CHARS = 300

SPEC_REQUIRED_KEYS = (
    "backend",
    "model",
    "effort",
    "profile",
    "cwd",
    "prompt_file",
    "run_dir",
    "timeout_seconds",
)
SPEC_OPTIONAL_KEYS = ("allowed_tools", "resume", "session_id", "term_grace_seconds")

STATUSES = (
    "success",
    "invalid_spec",
    "unsupported_profile",
    "spawn_failed",
    "timeout",
    "interrupted",
    "process_failed",
    "provider_error",
    "incomplete",
    "model_mismatch",
    "unverified",
    "internal_error",
)
STATUS_EXIT_CODES = {"success": 0, "invalid_spec": 2, "unsupported_profile": 2, "timeout": 124, "interrupted": 130}
DEFAULT_FAILURE_EXIT_CODE = 1

LIFECYCLES = ("spawn_failed", "exited", "timeout", "interrupted")

ARTIFACTS = {
    "launch": "launch.json",
    "process": "process.json",
    "raw": "stdout.jsonl",
    "stderr": "stderr.txt",
    "result": "result.txt",
    "parsed": "parsed.json",
    "receipt": "receipt.json",
}

PARSER_REQUIRED = {"result_text": str, "observed_models": list, "is_error": bool, "complete": bool}


class SpecError(ValueError):
    """A spec, command, or environment was rejected before anything was spawned."""


class ParentSignal(BaseException):
    """Raised in the supervising (main) thread when the parent receives a stop signal."""

    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum
        try:
            self.name = signal.Signals(signum).name
        except ValueError:
            self.name = f"signal {signum}"


# --------------------------------------------------------------------------- helpers


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def exit_code_for(status: str) -> int:
    return STATUS_EXIT_CODES.get(status, DEFAULT_FAILURE_EXIT_CODE)


def artifact_paths(run_dir: str) -> dict[str, str]:
    return {key: os.path.join(run_dir, name) for key, name in ARTIFACTS.items()}


def _short(text: Any, limit: int = MAX_ERROR_CHARS) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def bound_value(value: Any, *, max_items: int = 50, max_chars: int = 500, depth: int = 5) -> Any:
    """Return a size-bounded copy of ``value`` suitable for the public receipt."""
    if depth <= 0:
        return "<truncated: depth>"
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[: max_chars - 1] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                out["<truncated>"] = f"{len(value) - max_items} more entries"
                break
            out[_short(key, max_chars)] = bound_value(item, max_items=max_items, max_chars=max_chars, depth=depth - 1)
        return out
    if isinstance(value, (list, tuple)):
        out_list = [
            bound_value(item, max_items=max_items, max_chars=max_chars, depth=depth - 1)
            for item in list(value)[:max_items]
        ]
        if len(value) > max_items:
            out_list.append(f"<truncated: {len(value) - max_items} more entries>")
        return out_list
    return f"<{type(value).__name__}>"


# --------------------------------------------------------------------------- validation


def _require_str(spec: dict, key: str) -> str:
    value = spec.get(key)
    if not isinstance(value, str):
        raise SpecError(f"{key} must be a string")
    if not value.strip():
        raise SpecError(f"{key} must not be empty")
    if any(ch in value for ch in ("\x00", "\n", "\r")):
        raise SpecError(f"{key} contains control characters")
    return value


def _require_abs_path(spec: dict, key: str) -> str:
    value = _require_str(spec, key)
    if not os.path.isabs(value):
        raise SpecError(f"{key} must be an absolute path")
    return value


def _is_within(path: str, ancestor: str) -> bool:
    return path == ancestor or path.startswith(ancestor.rstrip(os.sep) + os.sep)


def _require_disjoint_run_dir(cwd: str, run_dir: str) -> None:
    real_cwd = os.path.realpath(cwd)
    real_run_dir = os.path.realpath(run_dir)
    if _is_within(real_run_dir, real_cwd) or _is_within(real_cwd, real_run_dir):
        raise SpecError(
            "run_dir must not be inside cwd or contain it (resolved paths overlap); "
            "keep attempt evidence outside the worker's working directory"
        )


def _require_positive_number(spec: dict, key: str, default: float | None, maximum: float) -> float:
    value = spec.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecError(f"{key} must be a number")
    if not math.isfinite(value) or value <= 0:
        raise SpecError(f"{key} must be positive and finite")
    if value > maximum:
        raise SpecError(f"{key} must be at most {maximum}")
    return float(value)


def validate_spec(spec: Any) -> tuple[dict, list[str]]:
    """Validate a worker spec.  Returns ``(normalized_spec, warnings)`` or raises SpecError."""
    if not isinstance(spec, dict):
        raise SpecError("spec must be a JSON object")
    if any(not isinstance(key, str) for key in spec):
        raise SpecError("spec keys must be strings")
    missing = [key for key in SPEC_REQUIRED_KEYS if key not in spec]
    if missing:
        raise SpecError("spec missing required keys: " + ", ".join(missing))

    backend = _require_str(spec, "backend")
    if backend not in BACKENDS:
        raise SpecError(f"backend must be one of {', '.join(BACKENDS)}")
    model = _require_str(spec, "model")
    effort = _require_str(spec, "effort")
    for key, value in (("model", model), ("effort", effort)):
        if value.startswith("-"):
            raise SpecError(f"{key} must not start with '-'")
    profile = _require_str(spec, "profile")
    if profile not in PROFILES:
        raise SpecError(f"profile must be one of {', '.join(PROFILES)}")

    cwd = _require_abs_path(spec, "cwd")
    if not os.path.isdir(cwd):
        raise SpecError("cwd is not an existing directory")
    prompt_file = _require_abs_path(spec, "prompt_file")
    if not os.path.isfile(prompt_file):
        raise SpecError("prompt_file is not an existing file")
    try:
        with open(prompt_file, "rb") as handle:
            handle.read(1)
    except OSError as exc:
        raise SpecError(f"prompt_file is not readable: {exc.strerror}") from exc
    run_dir = _require_abs_path(spec, "run_dir")
    if os.path.lexists(run_dir):
        raise SpecError("run_dir already exists; every attempt needs a new unique run_dir")
    _require_disjoint_run_dir(cwd, run_dir)

    timeout = _require_positive_number(spec, "timeout_seconds", None, MAX_TIMEOUT_SECONDS)
    grace = _require_positive_number(spec, "term_grace_seconds", DEFAULT_TERM_GRACE_SECONDS, MAX_TERM_GRACE_SECONDS)

    allowed_tools = spec.get("allowed_tools", [])
    if allowed_tools is None:
        allowed_tools = []
    if not isinstance(allowed_tools, list):
        raise SpecError("allowed_tools must be a list of strings")
    for item in allowed_tools:
        if not isinstance(item, str) or not item.strip():
            raise SpecError("allowed_tools entries must be non-empty strings")
        if any(ch in item for ch in ("\x00", "\n", "\r")):
            raise SpecError("allowed_tools entries contain control characters")

    normalized: dict[str, Any] = {
        "backend": backend,
        "model": model,
        "effort": effort,
        "profile": profile,
        "cwd": cwd,
        "prompt_file": prompt_file,
        "run_dir": run_dir,
        "timeout_seconds": timeout,
        "term_grace_seconds": grace,
        "allowed_tools": list(allowed_tools),
    }
    for key in ("resume", "session_id"):
        if key in spec:
            normalized[key] = _require_str(spec, key)

    known = set(SPEC_REQUIRED_KEYS) | set(SPEC_OPTIONAL_KEYS)
    unknown = sorted(set(spec) - known)
    warnings = [f"unknown spec keys ignored: {', '.join(unknown)}"] if unknown else []
    return normalized, warnings


def validate_command(command: Any) -> list[str]:
    if not isinstance(command, (list, tuple)) or not command:
        raise SpecError("command must be a non-empty list of strings")
    for arg in command:
        if not isinstance(arg, str):
            raise SpecError("command arguments must be strings")
        if "\x00" in arg:
            raise SpecError("command arguments must not contain NUL")
    if not os.path.isabs(command[0]):
        raise SpecError("command[0] must be an absolute executable path (resolve it before calling)")
    return list(command)


def validate_env(env: Any) -> dict[str, str]:
    if not isinstance(env, dict):
        raise SpecError("env must be a dict of strings or None")
    for key, value in env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise SpecError("env keys and values must be strings")
        if "\x00" in key or "\x00" in value or "=" in key:
            raise SpecError("env contains an invalid key or value")
    return dict(env)


# --------------------------------------------------------------------------- filesystem


def claim_run_dir(run_dir: str) -> None:
    """Atomically claim ``run_dir``.  Fails if it already exists in any form."""
    parent = os.path.dirname(run_dir.rstrip(os.sep)) or os.sep
    os.makedirs(parent, exist_ok=True)
    try:
        os.mkdir(run_dir, 0o700)
    except FileExistsError as exc:
        raise SpecError("run_dir already exists; every attempt needs a new unique run_dir") from exc
    os.chmod(run_dir, 0o700)


def create_restricted(path: str) -> int:
    """Create a new 0600 file and return its descriptor.  Fails if it exists."""
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)


def write_restricted_text(path: str, text: str) -> None:
    fd = create_restricted(path)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def atomic_write_json(path: str, payload: Any) -> None:
    """Write JSON to a 0600 temp file in the same directory, fsync, then rename over ``path``."""
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_jsonl(path: str) -> dict[str, Any]:
    """Parse newline-delimited JSON, keeping parse errors (without content) and truncation evidence."""
    with open(path, "rb") as handle:
        data = handle.read()
    events: list[dict] = []
    parse_errors: list[dict] = []
    ends_with_newline = data.endswith(b"\n")
    lines = data.split(b"\n")
    if ends_with_newline:
        lines = lines[:-1]
    truncated_final_line = False
    blank_lines = 0
    for number, raw in enumerate(lines, start=1):
        if not raw.strip():
            blank_lines += 1
            continue
        is_last = number == len(lines)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            parse_errors.append({"line": number, "bytes": len(raw), "error": f"{type(exc).__name__}: {_short(exc, 120)}"})
            if is_last and not ends_with_newline:
                truncated_final_line = True
            continue
        if not isinstance(value, dict):
            parse_errors.append({"line": number, "bytes": len(raw), "error": "line is not a JSON object"})
            continue
        events.append(value)
    return {
        "events": events,
        "parse_errors": parse_errors,
        "truncated_final_line": truncated_final_line,
        "ends_with_newline": ends_with_newline,
        "line_count": len(lines),
        "blank_lines": blank_lines,
        "bytes": len(data),
    }


# --------------------------------------------------------------------------- signals and termination

_HANDLED_SIGNAL_NAMES = tuple(name for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name))


class _SignalGuard:
    """Defer handled signals until the launcher reaches an ownership checkpoint.

    Raising inside Popen can lose the returned child before its PID is assigned.
    Recording instead also protects directory claims and receipt writes. Supervision
    checks the pending signal regularly. Handlers can only be installed on the main
    thread; SIGKILL and indefinitely blocked system calls cannot be made recoverable.

    A signal whose disposition was inherited as SIG_IGN (for example SIGHUP under a
    nohup-style launch, or SIGINT for a shell background job) is left ignored, per
    POSIX convention, and its name is recorded for the receipt. ``reported_signal``
    is the first signal the receipt has accounted for; anything recorded after that
    point is folded in by :func:`run_process` once the handlers are removed.
    """

    def __init__(self) -> None:
        self.previous: dict[int, Any] = {}
        self.installed = False
        self.terminating = False
        self.requested_signal: int | None = None
        self.reported_signal: int | None = None
        self.late_signals: list[str] = []
        self.ignored: list[str] = []

    def _handler(self, signum: int, _frame: Any) -> None:
        if self.requested_signal is not None:
            self.late_signals.append(_signal_name(signum))
            return
        self.requested_signal = signum

    def install(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        for name in _HANDLED_SIGNAL_NAMES:
            signum = getattr(signal, name)
            if signal.getsignal(signum) == signal.SIG_IGN:
                self.ignored.append(name)
                continue
            self.previous[signum] = signal.signal(signum, self._handler)
        self.installed = True

    def unreported_signals(self, already_listed: int) -> list[str]:
        names: list[str] = []
        if self.requested_signal is not None and self.reported_signal != self.requested_signal:
            names.append(_signal_name(self.requested_signal))
        names.extend(self.late_signals[already_listed:])
        return names

    def restore(self) -> None:
        for signum, previous in self.previous.items():
            signal.signal(signum, previous if previous is not None else signal.SIG_DFL)
        self.previous.clear()
        self.installed = False


def _signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return f"signal {signum}"


def _signal_group(pgid: int | None, pid: int, signum: int) -> None:
    """Signal the child's process group; fall back to the child alone if the group is gone."""
    try:
        if pgid is not None:
            os.killpg(pgid, signum)
            return
    except ProcessLookupError:
        return
    except PermissionError:
        pass
    try:
        os.kill(pid, signum)
    except ProcessLookupError:
        pass


def _group_alive(proc: subprocess.Popen, pgid: int | None) -> bool:
    proc.poll()
    if pgid is None:
        return proc.returncode is None
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _wait_group_exit(proc: subprocess.Popen, pgid: int | None, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while _group_alive(proc, pgid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    return proc.poll() is not None


def terminate_process_group(
    proc: subprocess.Popen,
    pgid: int | None,
    grace_seconds: float,
    record: dict | None = None,
    kill_wait_seconds: float = KILL_WAIT_SECONDS,
) -> bool:
    """SIGTERM the group, wait ``grace_seconds``, SIGKILL, wait again.

    Returns True only when the direct child is reaped and its owned process group
    no longer exists. Processes that deliberately escape that group are outside
    this lifecycle boundary; this is not an OS sandbox.
    """
    record = record if record is not None else {}
    if not _group_alive(proc, pgid):
        return True
    _signal_group(pgid, proc.pid, signal.SIGTERM)
    record["term_sent"] = True
    if _wait_group_exit(proc, pgid, grace_seconds):
        return True
    _signal_group(pgid, proc.pid, signal.SIGKILL)
    record["kill_sent"] = True
    return _wait_group_exit(proc, pgid, kill_wait_seconds)


def _feed_stdin(proc: subprocess.Popen, data: bytes) -> None:
    try:
        proc.stdin.write(data)
        proc.stdin.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass


def _supervise(
    proc: subprocess.Popen,
    pgid: int | None,
    timeout_seconds: float,
    grace_seconds: float,
    guard: _SignalGuard,
) -> tuple[str, bool, dict]:
    """Wait for the child, enforcing the timeout and reacting to parent signals."""
    record: dict[str, Any] = {
        "term_sent": False, "kill_sent": False, "interrupt_signal": None, "stop_signal_after_termination": None,
    }
    try:
        deadline = time.monotonic() + timeout_seconds
        while True:
            if guard.requested_signal is not None:
                raise ParentSignal(guard.requested_signal)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(proc.args, timeout_seconds)
            try:
                proc.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                continue
            if guard.requested_signal is not None:
                raise ParentSignal(guard.requested_signal)
            guard.terminating = True
            if _group_alive(proc, pgid):
                record["descendants_remained_after_leader_exit"] = True
                confirmed = terminate_process_group(proc, pgid, grace_seconds, record)
                return "exited", confirmed, record
            return "exited", True, record
    except subprocess.TimeoutExpired:
        lifecycle = "timeout"
    except KeyboardInterrupt:
        lifecycle = "interrupted"
        record["interrupt_signal"] = "SIGINT"
    except ParentSignal as sig:
        lifecycle = "interrupted"
        record["interrupt_signal"] = sig.name
    guard.terminating = True
    confirmed = terminate_process_group(proc, pgid, grace_seconds, record)
    return lifecycle, confirmed, record


# --------------------------------------------------------------------------- parsing and evaluation


def _run_parser(parse_events: Callable, events: list[dict], spec: dict) -> tuple[dict, list[str]]:
    """Call the adapter parser and enforce its contract.  Never raises."""
    problems: list[str] = []
    fallback = {"result_text": "", "observed_models": [], "is_error": False, "complete": False}
    try:
        parsed = parse_events(events, dict(spec))
    except Exception as exc:  # a parser bug must not lose the receipt
        problems.append(f"parser_exception: {type(exc).__name__}: {_short(exc, 200)}")
        return fallback, problems
    if not isinstance(parsed, dict):
        problems.append("parser_contract: parser did not return a dict")
        return fallback, problems
    parsed = dict(parsed)
    for key, expected in PARSER_REQUIRED.items():
        value = parsed.get(key)
        if not isinstance(value, expected) or (expected is bool and value is not True and value is not False):
            problems.append(f"parser_contract: {key} missing or not {expected.__name__}")
            parsed[key] = fallback[key]
    if any(not isinstance(item, str) for item in parsed["observed_models"]):
        problems.append("parser_contract: observed_models must contain strings")
        parsed["observed_models"] = [str(item) for item in parsed["observed_models"]]
    for key in ("errors", "warnings", "auxiliary_models"):
        value = parsed.get(key, [])
        if not isinstance(value, list):
            problems.append(f"parser_contract: {key} must be a list")
            value = []
        parsed[key] = [_short(item) for item in value]
    return parsed, problems


def evaluate(
    spec: dict,
    lifecycle: str,
    returncode: int | None,
    stream: dict,
    parsed: dict,
    parser_problems: list[str],
) -> tuple[str, list[str], bool]:
    """Decide the delivery status.  Returns ``(status, errors, requested_model_verified)``.

    Every detected problem is listed in ``errors``; ``status`` is the highest-precedence one.
    """
    requested = spec["model"]
    observed = list(parsed.get("observed_models") or [])
    complete = bool(parsed.get("complete"))
    is_error = bool(parsed.get("is_error"))
    mismatched = sorted({model for model in observed if model != requested})
    parser_errors = list(parsed.get("errors") or []) + list(parser_problems)

    findings: list[tuple[str, str]] = []
    if lifecycle == "spawn_failed":
        findings.append(("spawn_failed", "spawn_failed: child process could not be started"))
    elif lifecycle == "timeout":
        findings.append(("timeout", f"timeout: child exceeded {spec['timeout_seconds']}s and was terminated"))
    elif lifecycle == "interrupted":
        findings.append(("interrupted", "interrupted: parent received a stop signal and terminated the child"))
    if lifecycle == "exited" and returncode != 0:
        findings.append(("process_failed", f"process_failed: child exited with returncode {returncode}"))
    if is_error:
        findings.append(("provider_error", "provider_error: terminal result reported an error"))
    if not complete:
        findings.append(("incomplete", "incomplete: no terminal result event in the stream"))
    if stream.get("truncated_final_line"):
        findings.append(("incomplete", "incomplete: final stream line is truncated"))
    if mismatched:
        findings.append(
            (
                "model_mismatch",
                f"model_mismatch: response messages attributed to {', '.join(mismatched)}; requested {requested}",
            )
        )
    if complete and not observed:
        findings.append(("unverified", "unverified: no response message carried a model attribution"))
    if stream.get("parse_errors"):
        findings.append(("unverified", f"unverified: {len(stream['parse_errors'])} malformed stream line(s)"))
    for problem in parser_errors:
        findings.append(("unverified", f"unverified: {problem}"))

    status = "success"
    for candidate in STATUSES:
        if any(found == candidate for found, _ in findings):
            status = candidate
            break
    errors = [_short(message) for _, message in findings][:MAX_ERRORS]
    verified = (
        lifecycle == "exited"
        and returncode == 0
        and complete
        and not is_error
        and not stream.get("truncated_final_line")
        and not stream.get("parse_errors")
        and bool(observed)
        and not mismatched
        and not parser_errors
    )
    return status, errors, verified


def make_error_receipt(spec: Any, status: str, errors: list[str], warnings: list[str] | None = None) -> dict:
    """A receipt for failures that happen before a run_dir was claimed (or for internal errors)."""
    spec = spec if isinstance(spec, dict) else {}

    def safe(key: str) -> str | None:
        value = spec.get(key)
        return value if isinstance(value, str) else None

    now = utc_now()
    return {
        "schema": RECEIPT_SCHEMA,
        "status": status,
        "exit_code": exit_code_for(status),
        "lifecycle": None,
        "confirmed_terminated": True,
        "backend": safe("backend"),
        "requested_model": safe("model"),
        "requested_effort": safe("effort"),
        "profile": safe("profile"),
        "cwd": safe("cwd"),
        "run_dir": safe("run_dir"),
        "observed_models": [],
        "auxiliary_models": [],
        "requested_model_verified": False,
        "complete": False,
        "errors": [_short(item) for item in errors][:MAX_ERRORS],
        "warnings": [_short(item) for item in (warnings or [])][:MAX_ERRORS],
        "returncode": None,
        "elapsed_seconds": 0.0,
        "started_at": now,
        "ended_at": now,
    }


# --------------------------------------------------------------------------- main entry point


def run_process(
    spec: dict,
    command: list[str],
    parse_events: Callable[[list[dict], dict], dict],
    stdin_text: str | None = None,
    env: dict | None = None,
    *,
    adapter_evidence: dict | None = None,
) -> dict:
    """Run one bounded worker attempt and return its receipt (also written to run_dir).

    Raises SpecError before anything is spawned when inputs are invalid, the run_dir
    already exists, or the run_dir overlaps cwd. After claim, handled stop signals
    finalize an interrupted receipt at a safe ownership checkpoint, provided the
    artifact storage remains writable. A stop signal that arrives after the attempt
    already ended for another cause (timeout, spawn failure) or after the receipt is
    final is recorded in the receipt rather than relabeling or dropping it. SIGKILL,
    process crashes and indefinitely blocked calls cannot carry that guarantee, and a
    signal that trips between handler removal and process exit follows the restored
    disposition instead of being recorded.
    """
    if os.name != "posix":
        raise RuntimeError("run_process requires a POSIX platform (process-group lifecycle)")
    normalized, warnings = validate_spec(spec)
    command = validate_command(command)
    if stdin_text is not None and not isinstance(stdin_text, str):
        raise SpecError("stdin_text must be a string or None")
    if env is not None:
        env = validate_env(env)
    if not callable(parse_events):
        raise SpecError("parse_events must be callable")

    guard = _SignalGuard()
    guard.install()
    try:
        claim_run_dir(normalized["run_dir"])
        receipt = _run_claimed_process(normalized, command, parse_events, stdin_text, env,
                                       guard, warnings, adapter_evidence)
    finally:
        guard.restore()
    _record_late_signals(receipt, guard)
    return receipt


def _termination_view(termination: dict, guard: _SignalGuard) -> dict:
    return {
        **termination,
        "late_parent_signals": list(guard.late_signals),
        "ignored_parent_signals": list(guard.ignored),
    }


def _ended_by_own_cause(lifecycle: str) -> bool:
    return lifecycle in ("timeout", "spawn_failed")


def _stop_after_cause_error(signal_name: str, lifecycle: str) -> str:
    return (
        f"stop_signal: parent received {signal_name} after the attempt had already ended by "
        f"{lifecycle}; {lifecycle} cause retained"
    )


def _record_late_signals(receipt: dict, guard: _SignalGuard) -> None:
    termination = receipt["termination"]
    listed = list(termination.get("late_parent_signals") or [])
    unreported = guard.unreported_signals(len(listed))
    if not unreported:
        return
    termination["late_parent_signals"] = listed + unreported
    receipt["warnings"] = (
        list(receipt["warnings"])
        + [
            "late_parent_signals: " + ", ".join(unreported)
            + " arrived after the receipt was finalized; the child had already ended and the status is unchanged"
        ]
    )[:MAX_ERRORS]
    try:
        atomic_write_json(receipt["receipt_path"], receipt)
    except OSError as exc:
        receipt["warnings"] = (receipt["warnings"] + [
            f"receipt_rewrite_failed: durable receipt lacks the late signal note: {_short(exc, 120)}"
        ])[:MAX_ERRORS]


def _run_claimed_process(
    normalized: dict,
    command: list[str],
    parse_events: Callable[[list[dict], dict], dict],
    stdin_text: str | None,
    env: dict | None,
    guard: _SignalGuard,
    warnings: list[str],
    adapter_evidence: dict | None,
) -> dict:
    run_dir = normalized["run_dir"]
    paths = artifact_paths(run_dir)
    prompt_sha256 = sha256_file(normalized["prompt_file"])
    stdin_bytes = stdin_text.encode("utf-8") if stdin_text is not None else None
    started_at = utc_now()
    clock_start = time.monotonic()

    launch = {
        "schema": RECEIPT_SCHEMA,
        "stage": "launch_intent",
        "written_at": started_at,
        "backend": normalized["backend"],
        "requested_model": normalized["model"],
        "requested_effort": normalized["effort"],
        "profile": normalized["profile"],
        "cwd": normalized["cwd"],
        "prompt_file": normalized["prompt_file"],
        "prompt_sha256": prompt_sha256,
        "stdin_sha256": sha256_bytes(stdin_bytes) if stdin_bytes is not None else None,
        "stdin_bytes": len(stdin_bytes) if stdin_bytes is not None else 0,
        "timeout_seconds": normalized["timeout_seconds"],
        "term_grace_seconds": normalized["term_grace_seconds"],
        "command_argv": command,
        "env_mode": "inherited" if env is None else "explicit",
        "env_var_count": len(os.environ) if env is None else len(env),
        "parent_pid": os.getpid(),
        "spec_warnings": warnings,
    }
    atomic_write_json(paths["launch"], launch)

    proc: subprocess.Popen | None = None
    pgid: int | None = None
    lifecycle = "interrupted"
    confirmed = True
    termination: dict[str, Any] = {
        "term_sent": False, "kill_sent": False, "interrupt_signal": None, "stop_signal_after_termination": None,
    }
    problems: list[str] = []
    process_record: dict[str, Any] = {}
    try:
        out_fd = create_restricted(paths["raw"])
        err_fd = create_restricted(paths["stderr"])
        try:
            try:
                if guard.requested_signal is None:
                    proc = subprocess.Popen(
                        command,
                        cwd=normalized["cwd"],
                        stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                        stdout=out_fd,
                        stderr=err_fd,
                        env=env,
                        start_new_session=True,
                        close_fds=True,
                        shell=False,
                    )
                    # start_new_session makes the returned PID its process-group ID,
                    # even when a short-lived leader exits before getpgid can run.
                    pgid = proc.pid
            except (OSError, ValueError) as exc:
                lifecycle = "spawn_failed"
                problems.append(f"spawn_failed: {type(exc).__name__}: {_short(exc, 200)}")
        finally:
            os.close(out_fd)
            os.close(err_fd)

        if proc is not None:
            process_record = {
                "stage": "spawned",
                "pid": proc.pid,
                "pgid": pgid,
                "parent_pid": os.getpid(),
                "spawned_at": utc_now(),
                "spawn_monotonic_ns": time.monotonic_ns(),
            }
            atomic_write_json(paths["process"], process_record)
            if stdin_bytes is not None:
                threading.Thread(target=_feed_stdin, args=(proc, stdin_bytes), daemon=True).start()
            lifecycle, confirmed, termination = _supervise(
                proc, pgid, normalized["timeout_seconds"], normalized["term_grace_seconds"], guard
            )
        stop_after_cause: str | None = None
        if guard.requested_signal is not None:
            signal_name = _signal_name(guard.requested_signal)
            if _ended_by_own_cause(lifecycle):
                stop_after_cause = signal_name
                termination["stop_signal_after_termination"] = signal_name
            else:
                lifecycle = "interrupted"
                termination["interrupt_signal"] = signal_name
            guard.reported_signal = guard.requested_signal
        guard.terminating = True
        ended_at = utc_now()
        elapsed = round(time.monotonic() - clock_start, 3)
        returncode = proc.returncode if proc is not None else None

        if proc is not None:
            process_record.update(
                {
                    "stage": "finished",
                    "lifecycle": lifecycle,
                    "returncode": returncode,
                    "confirmed_terminated": confirmed,
                    "ended_at": ended_at,
                    "termination": termination,
                }
            )
            atomic_write_json(paths["process"], process_record)

        stream = read_jsonl(paths["raw"])
        parsed, parser_problems = _run_parser(parse_events, stream["events"], normalized)
        status, errors, verified = evaluate(
            normalized, lifecycle, returncode, stream, parsed, problems + parser_problems
        )
        if not confirmed:
            errors.insert(0, "termination_unconfirmed: owned process group may still exist; retain resource ownership")
            verified = False
            if status == "success":
                status = "unverified"
        if termination.get("descendants_remained_after_leader_exit"):
            errors.insert(0, "orphaned_descendants: worker exited with a live process group; cleanup was required")
            verified = False
            if status == "success":
                status = "unverified"
        if stop_after_cause is not None:
            errors.append(_stop_after_cause_error(stop_after_cause, lifecycle))

        result_text = parsed.get("result_text") or ""
        write_restricted_text(paths["result"], result_text)
        atomic_write_json(paths["parsed"], {key: value for key, value in parsed.items() if key != "result_text"})

        tool_calls = parsed.get("tool_calls") if isinstance(parsed.get("tool_calls"), list) else []
        tool_histogram: dict[str, int] = {}
        for call in tool_calls:
            name = call.get("name") if isinstance(call, dict) else None
            key = name if isinstance(name, str) else "<unknown>"
            tool_histogram[key] = tool_histogram.get(key, 0) + 1
        evidence = parsed.get("evidence") if isinstance(parsed.get("evidence"), dict) else {}
        denial_count = evidence.get("permission_denial_count")
        denial_warnings: list[str] = []
        if isinstance(denial_count, int) and not isinstance(denial_count, bool) and denial_count > 0:
            denied = evidence.get("permission_denied_tools")
            denied_names = ", ".join(str(name) for name in denied) if isinstance(denied, list) and denied else "unknown"
            denial_warnings.append(
                f"permission_denials: {denial_count} (tools: {denied_names}); delivery status is unchanged, "
                "inspect the denials before accepting the task"
            )

        receipt = {
            "schema": RECEIPT_SCHEMA,
            "status": status,
            "exit_code": exit_code_for(status),
            "lifecycle": lifecycle,
            "confirmed_terminated": confirmed,
            "backend": normalized["backend"],
            "requested_model": normalized["model"],
            "observed_models": list(parsed.get("observed_models") or []),
            "auxiliary_models": list(parsed.get("auxiliary_models") or []),
            "requested_model_verified": verified,
            "complete": bool(parsed.get("complete")),
            "provider_is_error": bool(parsed.get("is_error")),
            "requested_effort": normalized["effort"],
            "profile": normalized["profile"],
            "cwd": normalized["cwd"],
            "prompt_sha256": prompt_sha256,
            "stdin_sha256": launch["stdin_sha256"],
            "run_dir": run_dir,
            "result_path": paths["result"],
            "raw_path": paths["raw"],
            "stderr_path": paths["stderr"],
            "receipt_path": paths["receipt"],
            "launch_path": paths["launch"],
            "process_path": paths["process"],
            "parsed_path": paths["parsed"],
            "errors": errors[:MAX_ERRORS],
            "warnings": (warnings + list(parsed.get("warnings") or []) + denial_warnings)[:MAX_ERRORS],
            "returncode": returncode,
            "pid": proc.pid if proc is not None else None,
            "pgid": pgid,
            "elapsed_seconds": elapsed,
            "started_at": started_at,
            "ended_at": ended_at,
            "timeout_seconds": normalized["timeout_seconds"],
            "termination": _termination_view(termination, guard),
            "stream": {
                "event_count": len(stream["events"]),
                "parse_error_count": len(stream["parse_errors"]),
                "parse_errors": stream["parse_errors"][:MAX_ERRORS],
                "truncated_final_line": stream["truncated_final_line"],
                "ends_with_newline": stream["ends_with_newline"],
                "stdout_bytes": stream["bytes"],
                "stderr_bytes": os.path.getsize(paths["stderr"]),
            },
            "result_sha256": hashlib.sha256(result_text.encode("utf-8")).hexdigest(),
            "result_chars": len(result_text),
            "tool_call_count": len(tool_calls),
            "tool_calls_by_name": tool_histogram,
            "permission_denial_count": denial_count if isinstance(denial_count, int) else None,
            "evidence": bound_value(evidence),
            "adapter": bound_value(adapter_evidence) if adapter_evidence else {},
            "command_argv": command,
        }
        atomic_write_json(paths["receipt"], receipt)
        if guard.requested_signal is not None and guard.reported_signal is None:
            # A first stop request can arrive during parsing or the receipt write.
            guard.reported_signal = guard.requested_signal
            signal_name = _signal_name(guard.requested_signal)
            if _ended_by_own_cause(lifecycle):
                termination["stop_signal_after_termination"] = signal_name
                receipt["errors"] = (receipt["errors"] + [_stop_after_cause_error(signal_name, lifecycle)])[:MAX_ERRORS]
            else:
                lifecycle = "interrupted"
                termination["interrupt_signal"] = signal_name
                receipt.update(status="interrupted", exit_code=exit_code_for("interrupted"),
                               lifecycle="interrupted", requested_model_verified=False)
                receipt["errors"].insert(0, "interrupted: parent received a stop signal while finalizing the attempt")
            receipt["termination"] = _termination_view(termination, guard)
            if proc is not None:
                process_record.update(lifecycle=lifecycle, termination=termination)
                atomic_write_json(paths["process"], process_record)
            atomic_write_json(paths["receipt"], receipt)
        return receipt
    finally:
        if proc is not None and _group_alive(proc, pgid):
            # Safety net for any unexpected exception path: never leave the child running.
            guard.terminating = True
            terminate_process_group(proc, pgid, normalized["term_grace_seconds"], termination)
