# src/bully/cli/reasonix_hook.py
"""Reasonix hook driver: dispatch on payload.event; gate edits on PreToolUse.

Wired to every event in .reasonix/settings.json via `python3 -m bully
reasonix-hook`. M1 implements the PreToolUse deterministic path; other events
are no-ops here (M2/M3 add semantic + session handling).

Output contract (reasonix internal/hook): the block message is read from
STDERR; exit 2 blocks (gating events only), exit 1 = warn (notify), exit 0 =
pass (silent). The hook fails open — never block on an internal bug.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from bully.config.parser import ConfigError
from bully.diff.pending import build_pending_diff_from, compute_after
from bully.harness.reasonix import edit_event_from_payload
from bully.runtime.hook_io import format_blocked_stderr
from bully.runtime.runner import run_pipeline
from bully.state.trust import untrusted_stderr


def find_config_upward(start: Path) -> Path | None:
    cur = start.resolve()
    if cur.is_file():
        cur = cur.parent
    for p in (cur, *cur.parents):
        candidate = p / ".bully.yml"
        if candidate.is_file():
            return candidate
    return None


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
    warnings = result.get("warnings")
    if warnings:
        body = "\n".join(f"- [{w.get('rule', '?')}] {w.get('description', '')}" for w in warnings)
        return 1, "AGENTIC LINT -- warnings:\n" + body + "\n"
    return 0, ""  # pass / skipped / evaluate (semantic deferred to M2)


def handle_payload(payload: dict) -> tuple[int, str]:
    """Core hook logic. Returns (exit_code, stderr_message)."""
    if payload.get("event") != "PreToolUse":
        return 0, ""
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
    except Exception:  # noqa: BLE001 — fail open: never block on an internal bug
        # TODO(M3): best-effort telemetry record here so a systematically
        # crashing hook is visible. Telemetry of fail-opens lands with M3.
        return 0, ""
    return _render(result, config)


def run_reasonix_hook() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    code, msg = handle_payload(payload if isinstance(payload, dict) else {})
    if msg:
        sys.stderr.write(msg)
    return code
