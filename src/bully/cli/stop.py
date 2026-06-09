"""`bully stop` / `bully subagent-stop`: session-rule evaluation for Reasonix.

Reasonix's Stop event cannot block (only PreToolUse and UserPromptSubmit
gate), so session-engine rules are enforced in two stages: Stop *notifies*
(exit 1) and keeps the changed-set while error rules are unsatisfied; the
next UserPromptSubmit *gates* the turn (exit 2, message fed to the agent)
until they are. Any non-gating Stop clears the changed-set so the next turn
starts fresh.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from bully.config.loader import parse_config
from bully.config.parser import ConfigError
from bully.config.scope import scope_glob_matches
from bully.state.telemetry import append_record, telemetry_path
from bully.state.trust import trust_status


def _read_changed(session_file: Path) -> list[str]:
    """De-duplicated list of files recorded in this session's changed-set."""
    if not session_file.exists():
        return []
    seen: set[str] = set()
    changed: list[str] = []
    try:
        with open(session_file) as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                fpath = rec.get("file") if isinstance(rec, dict) else None
                if isinstance(fpath, str) and fpath not in seen:
                    seen.add(fpath)
                    changed.append(fpath)
    except OSError:
        return []
    return changed


def _session_rule_violations(rules: list, changed: list[str]) -> list[tuple[str, str, str]]:
    """Fire each `engine: session` rule whose `when` matched but `require` did not."""
    out: list[tuple[str, str, str]] = []
    if not changed:
        return out

    def matches_any(globs: list[str]) -> bool:
        for c in changed:
            for pat in globs or []:
                if scope_glob_matches(pat, c):
                    return True
        return False

    for r in rules:
        if r.engine != "session":
            continue
        when_globs = (r.when or {}).get("changed_any", []) or []
        if not isinstance(when_globs, list):
            when_globs = []
        if not matches_any(when_globs):
            continue
        require_globs = (r.require or {}).get("changed_any", []) or []
        if not isinstance(require_globs, list):
            require_globs = []
        if matches_any(require_globs):
            continue
        out.append((r.id, r.severity, r.description))
    return out


def evaluate_session(config_path: str) -> tuple[list[tuple[str, str, str]], Path]:
    """(violations, session_file) for the accumulated changed-set.

    Empty when the config is missing/untrusted/invalid or nothing was
    recorded — callers treat that as a clean pass (fail-open).
    """
    cfg_abs = Path(config_path).resolve()
    session_file = cfg_abs.parent / ".bully" / "session.jsonl"
    if not cfg_abs.is_file():
        return [], session_file
    status, _ = trust_status(str(cfg_abs))
    if status != "trusted":
        return [], session_file
    try:
        rules = parse_config(str(cfg_abs))
    except ConfigError:
        return [], session_file
    return _session_rule_violations(rules, _read_changed(session_file)), session_file


def reasonix_stop(config_path: str) -> tuple[int, str]:
    """Stop: notify on session-rule violations (Stop can't block in Reasonix).

    Error violations keep the changed-set so the next UserPromptSubmit gates;
    a clean or warning-only Stop clears it so the next turn starts fresh.
    """
    violations, session_file = evaluate_session(config_path)
    msg = ""
    if violations:
        msg = "bully session check failed:\n" + "".join(
            f"- [{sev}] {rid}: {desc}\n" for rid, sev, desc in violations
        )
    if any(sev == "error" for _, sev, _ in violations):
        return 1, msg + "Unsatisfied error rules will gate the next prompt.\n"
    try:
        session_file.unlink()
    except FileNotFoundError:
        pass
    return (1, msg) if msg else (0, "")


def reasonix_prompt_gate(config_path: str) -> tuple[int, str]:
    """UserPromptSubmit: exit 2 while error session rules remain unsatisfied."""
    violations, _ = evaluate_session(config_path)
    blocking = [(rid, sev, desc) for rid, sev, desc in violations if sev == "error"]
    if not blocking:
        return 0, ""
    body = "".join(f"- [{sev}] {rid}: {desc}\n" for rid, sev, desc in blocking)
    return 2, (
        "AGENTIC LINT -- unsatisfied session rules gate this turn:\n"
        + body
        + "Make the required companion change(s), then continue with the user's request.\n"
    )


def cmd_stop_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bully stop")
    parser.add_argument("--config", default=".bully.yml")
    args = parser.parse_args(argv)
    code, msg = reasonix_stop(args.config)
    if msg:
        sys.stderr.write(msg)
    return code


def cmd_subagent_stop(config_path: str | None) -> int:
    """Append a subagent-completion telemetry record."""
    path = config_path or ".bully.yml"
    cfg_abs = Path(path).resolve()
    if not cfg_abs.is_file():
        return 0
    status, _ = trust_status(str(cfg_abs))
    if status != "trusted":
        return 0
    log_path = telemetry_path(path)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "type": "subagent_stop",
    }
    append_record(log_path, record)
    return 0


def cmd_subagent_stop_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bully subagent-stop")
    parser.add_argument("--config", default=".bully.yml")
    args = parser.parse_args(argv)
    return cmd_subagent_stop(args.config)
