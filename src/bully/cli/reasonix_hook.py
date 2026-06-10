# src/bully/cli/reasonix_hook.py
"""Reasonix hook driver: dispatch on payload.event; gate edits on PreToolUse.

Wired to every event in .reasonix/settings.json via `python3 -m bully
reasonix-hook`. PreToolUse gates pending edits (deterministic block + semantic
soft-gate) and records allowed edits to the session changed-set; Stop notifies
on session-rule violations; UserPromptSubmit gates the turn on unsatisfied
error session rules; SessionStart/SubagentStop stamp telemetry records.

Output contract (reasonix internal/hook): the block message is read from
STDERR; exit 2 blocks (gating events only), exit 1 = warn (notify), exit 0 =
pass (silent). The hook fails open — never block on an internal bug.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from bully.cli.session import cmd_session_record, cmd_session_start
from bully.cli.stop import cmd_subagent_stop, reasonix_prompt_gate, reasonix_stop
from bully.config.parser import ConfigError
from bully.diff.pending import build_pending_diff_from, compute_after
from bully.harness.reasonix import edit_event_from_payload
from bully.runtime.hook_io import format_blocked_stderr
from bully.runtime.runner import run_pipeline
from bully.state.telemetry import append_record, telemetry_path
from bully.state.trust import untrusted_stderr
from bully.state.verdict_cache import cached_verdict, diff_id


def find_config_upward(start: Path) -> Path | None:
    cur = start.resolve()
    if cur.is_file():
        cur = cur.parent
    for p in (cur, *cur.parents):
        candidate = p / ".bully.yml"
        if candidate.is_file():
            return candidate
    return None


def _log_fail_open(config: Path, event: str, file_path: str, exc: BaseException) -> None:
    """Best-effort record of a swallowed hook crash, so a systematically
    failing hook is visible to bully-review instead of silently passing."""
    try:
        append_record(
            telemetry_path(str(config)),
            {
                "ts": datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "type": "hook_fail_open",
                "event": event,
                "file": file_path,
                "error": f"{type(exc).__name__}: {exc}"[:300],
            },
        )
    except Exception:  # noqa: BLE001 — telemetry must never break fail-open
        pass


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _materialize(config_path: str, file_path: str, after: str) -> str:
    """Write pending `after` content to a temp file the engines can read."""
    tmpdir = Path(config_path).resolve().parent / ".bully" / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="pending-", suffix=Path(file_path).suffix, dir=str(tmpdir))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(after)
    return tmp


def _semantic_request_msg(result: dict, did: str) -> str:
    rules = ", ".join(r["id"] for r in result.get("evaluate", []))
    file_path = result.get("file", "")
    payload = result.get("_evaluator_input", "")
    return (
        "AGENTIC LINT SEMANTIC EVALUATION REQUIRED (edit paused).\n\n"
        f"Evaluate these rules against the diff: {rules}\n"
        "Judge inline only if it is a single rule over a short diff; otherwise invoke the "
        'evaluator subagent: run_skill(name="bully-evaluator", arguments=<the payload below>).\n'
        "Then record each rule's verdict:\n"
        f"  python3 -m bully --log-verdict --diff-id {did} --rule <id> --verdict <pass|violation> --file {file_path}\n"
        "If every rule passes, re-apply this exact edit and it will be allowed. "
        "If any rule is violated, fix it and apply the corrected edit.\n\n"
        f"{payload}"
    )


def _semantic_gate(result: dict, config: Path) -> tuple[int, str]:
    did = diff_id(result.get("file", ""), result.get("diff", ""))
    evaluate = result.get("evaluate", [])
    cfg = str(config)
    cached = {r["id"]: cached_verdict(cfg, did, r["id"]) for r in evaluate}

    recorded = [r for r in evaluate if cached.get(r["id"]) == "violation"]
    if recorded:
        body = "\n".join(f"- [{r['id']}] {r.get('description', '')}" for r in recorded)
        return (
            2,
            "AGENTIC LINT -- blocked (semantic, prior verdict). Fix before proceeding:\n"
            + body
            + "\n",
        )

    if evaluate and all(cached.get(r["id"]) == "pass" for r in evaluate):
        return 0, ""  # this exact edit was already evaluated clean -> allow

    return 2, _semantic_request_msg(result, did)


def _render(result: dict, config: Path) -> tuple[int, str]:
    status = result.get("status", "pass")
    if status == "untrusted":
        return 0, untrusted_stderr(
            result.get("config", str(config)),
            result.get("trust_status", "untrusted"),
            result.get("trust_detail", ""),
        )
    if status == "blocked":
        return 2, format_blocked_stderr(result)
    if status == "evaluate":
        return _semantic_gate(result, config)
    warnings = result.get("warnings")
    if warnings:
        body = "\n".join(f"- [{w.get('rule', '?')}] {w.get('description', '')}" for w in warnings)
        return 1, "AGENTIC LINT -- warnings:\n" + body + "\n"
    return 0, ""


def handle_payload(payload: dict) -> tuple[int, str]:
    """Core hook logic. Returns (exit_code, stderr_message)."""
    handlers = {
        "PreToolUse": _handle_pretooluse,
        "Stop": _handle_stop,
        "UserPromptSubmit": _handle_prompt_submit,
        "SessionStart": _handle_session_start,
        "SubagentStop": _handle_subagent_stop,
    }
    handler = handlers.get(payload.get("event", ""))
    if handler is None:
        return 0, ""
    return handler(payload)


def _config_from_cwd(payload: dict) -> Path | None:
    return find_config_upward(Path(payload.get("cwd") or "."))


def _handle_stop(payload: dict) -> tuple[int, str]:
    config = _config_from_cwd(payload)
    if config is None:
        return 0, ""
    return reasonix_stop(str(config))


def _handle_prompt_submit(payload: dict) -> tuple[int, str]:
    config = _config_from_cwd(payload)
    if config is None:
        return 0, ""
    return reasonix_prompt_gate(str(config))


def _handle_session_start(payload: dict) -> tuple[int, str]:
    config = _config_from_cwd(payload)
    if config is not None:
        # Stamps the session_init telemetry record that anchors the verdict
        # cache and semantic windows. Its stdout banner is discarded: exit 0
        # is a pass outcome and Reasonix skips those.
        cmd_session_start(str(config))
    return 0, ""


def _handle_subagent_stop(payload: dict) -> tuple[int, str]:
    config = _config_from_cwd(payload)
    if config is not None:
        cmd_subagent_stop(str(config))
    return 0, ""


def _handle_pretooluse(payload: dict) -> tuple[int, str]:
    ev = edit_event_from_payload(payload)
    if ev is None or not ev.file_path:
        return 0, ""
    config = find_config_upward(Path(ev.file_path))
    if config is None:
        return 0, ""
    try:
        before = _read_text(ev.file_path)
        after = compute_after(before, ev)
        diff = build_pending_diff_from(ev.file_path, before, after, ev.is_write)
        content_path = _materialize(str(config), ev.file_path, after)
        try:
            result = run_pipeline(str(config), ev.file_path, diff, content_path=content_path)
        finally:
            try:
                os.unlink(content_path)
            except OSError:
                pass
    except ConfigError as e:
        return 0, f"AGENTIC LINT -- config error: {e}\n"
    except Exception as e:  # noqa: BLE001 — fail open: never block on an internal bug
        _log_fail_open(config, "PreToolUse", ev.file_path, e)
        return 0, ""
    code, msg = _render(result, config)
    if code != 2 and result.get("status") != "untrusted":
        # Only exit 2 stops the tool, so this edit will land: add it to the
        # session changed-set for the Stop / UserPromptSubmit session rules.
        # Anchor on the cwd config — the one Stop/UserPromptSubmit read —
        # not the file's (a nested config would split the changed-set).
        session_config = _config_from_cwd(payload)
        if session_config is not None:
            try:
                cmd_session_record(str(session_config), ev.file_path)
            except Exception:  # noqa: BLE001 — recording must never break the gate
                pass
    return code, msg


def run_reasonix_hook() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    try:
        code, msg = handle_payload(payload if isinstance(payload, dict) else {})
    except Exception:  # noqa: BLE001 — fail open at the outermost boundary
        return 0
    if msg:
        sys.stderr.write(msg)
    return code
