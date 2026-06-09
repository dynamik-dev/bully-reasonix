# src/bully/diff/pending.py
"""Pre-write diff builder for the Reasonix PreToolUse hook.

PreToolUse fires *before* the write, so `before` is the real file on disk and
`after` is the pending edit applied — no post-write reconstruction needed.
Mirrors the unified-diff / write-content format of diff/context.py so the rest
of the pipeline consumes it identically.
"""

from __future__ import annotations

import difflib

from bully.diff.context import cap_write_content
from bully.harness.reasonix import EditEvent


def compute_after(before: str, ev: EditEvent) -> str:
    """Apply a pending edit to `before` and return the resulting content."""
    if ev.is_write:
        return ev.content or ""
    after = before
    for old, new, replace_all in ev.edits:
        if not old:
            continue
        after = after.replace(old, new) if replace_all else after.replace(old, new, 1)
    return after


def build_pending_diff_from(
    file_path: str, before: str, after: str, is_write: bool, context_lines: int = 5
) -> str:
    """Build the diff/content payload from already-read before/after content."""
    if is_write:
        return cap_write_content(after)
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{file_path}.before",
            tofile=f"{file_path}.after",
            n=context_lines,
        )
    )


def build_pending_diff(file_path: str, ev: EditEvent, context_lines: int = 5) -> str:
    """Read `file_path`, apply the edit, and return the diff (standalone helper)."""
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            before = f.read()
    except OSError:
        before = ""
    after = compute_after(before, ev)
    return build_pending_diff_from(file_path, before, after, ev.is_write, context_lines)
