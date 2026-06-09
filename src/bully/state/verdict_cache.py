# src/bully/state/verdict_cache.py
"""Verdict cache for the semantic soft-gate (M2).

The PreToolUse gate blocks an edit to *request* semantic evaluation, then must
let the model's re-issued identical edit through once a verdict is logged. We
key verdicts by a stable `diff_id` (hash of file path + normalized diff) so an
identical re-issued edit hits the cache and is allowed, while a *fixed* edit is
a new diff_id and gets evaluated fresh. Verdicts are `semantic_verdict` records
in .bully/log.jsonl carrying `diff_id`.

Lookup is latest-wins within the current session window: a `session_init`
record (stamped by the SessionStart hook) resets the window, so a stale
verdict from a previous session can't suppress a fresh evaluation. Logs with
no `session_init` anchor (headless runs, tests) fall back to whole-log
latest-wins.
"""

from __future__ import annotations

import hashlib
import json

from bully.state.telemetry import telemetry_path


def diff_id(file_path: str, diff: str) -> str:
    """Stable 16-hex id for a pending edit: hash of path + normalized diff."""
    normalized = "\n".join(line.rstrip() for line in diff.splitlines())
    h = hashlib.sha256()
    h.update(file_path.encode("utf-8"))
    h.update(b"\0")
    h.update(normalized.encode("utf-8"))
    return h.hexdigest()[:16]


def cached_verdict(config_path: str, did: str, rule: str) -> str | None:
    """Latest logged verdict ('pass'|'violation') for (diff_id, rule), or None."""
    log_path = telemetry_path(config_path)
    result: str | None = None
    try:
        with open(log_path, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if rec.get("type") == "session_init":
                    # New session window: verdicts logged before it no longer
                    # apply (a stale pass must not suppress a fresh eval).
                    result = None
                    continue
                if (
                    rec.get("type") == "semantic_verdict"
                    and rec.get("diff_id") == did
                    and rec.get("rule") == rule
                ):
                    v = rec.get("verdict")
                    if v is not None:
                        result = v  # keep scanning: latest non-null verdict wins
    except OSError:
        return None
    return result
