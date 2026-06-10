# src/bully/harness/reasonix.py
"""Reasonix harness seam: decode the hook Payload into a normalized EditEvent.

Reasonix delivers a JSON Payload on stdin (internal/hook/hook.go). The edit
tools and their args (internal/tool/builtin/) are:
  edit_file  -> {path, old_string, new_string}      (single unique replace)
  write_file -> {path, content}
  multi_edit -> {path, edits:[{old_string,new_string,replace_all?}]}
`path` may be relative to the payload's cwd.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EDIT_TOOLS = {"edit_file", "write_file", "multi_edit"}


@dataclass(frozen=True)
class EditEvent:
    tool: str
    file_path: str  # absolute, resolved against cwd
    is_write: bool
    content: str | None  # write_file only
    edits: tuple[tuple[str, str, bool], ...]  # (old, new, replace_all)


def _resolve(cwd: str, path: str) -> str:
    p = Path(path)
    if p.is_absolute() or not cwd:
        return str(p)
    return str(Path(cwd) / p)


def edit_event_from_payload(payload: dict[str, Any]) -> EditEvent | None:
    """Return an EditEvent for an edit tool call, or None if not applicable."""
    tool = payload.get("toolName", "")
    if tool not in EDIT_TOOLS:
        return None
    args = payload.get("toolArgs") or {}
    if isinstance(args, (str, bytes)):
        try:
            args = json.loads(args)
        except (ValueError, TypeError):
            return None
    if not isinstance(args, dict):
        return None

    raw_path = args.get("path") or args.get("file_path") or ""
    if not raw_path:
        return None
    file_path = _resolve(payload.get("cwd", ""), raw_path)

    if tool == "write_file":
        return EditEvent(tool, file_path, True, args.get("content", "") or "", ())

    if tool == "multi_edit":
        steps: list[tuple[str, str, bool]] = []
        for e in args.get("edits") or []:
            if not isinstance(e, dict):
                continue
            steps.append(
                (
                    e.get("old_string", "") or "",
                    e.get("new_string", "") or "",
                    bool(e.get("replace_all", False)),
                )
            )
        return EditEvent(tool, file_path, False, None, tuple(steps))

    # edit_file
    return EditEvent(
        tool,
        file_path,
        False,
        None,
        ((args.get("old_string", "") or "", args.get("new_string", "") or "", False),),
    )
