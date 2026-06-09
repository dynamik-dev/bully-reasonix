"""`bully doctor` subcommand: runtime + Reasonix wiring diagnostics."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from bully.config.loader import parse_config
from bully.config.parser import ConfigError, Rule
from bully.engines.ast_grep import AST_GREP_INSTALL_HINT, ast_grep_available
from bully.state.trust import trust_status

_MIN_PYTHON = (3, 10)

# Mirrors reasonix v1.4.0 (internal/skill/skill.go, internal/config): skills are
# discovered at <dir>/skills/<name>/SKILL.md (or <name>.md) under the convention
# dirs for project root and home; reasonix.toml [skills] paths are additional
# roots used AS-IS (no "skills" suffix appended).
_CONVENTION_DIRS = (".reasonix", ".agents", ".agent", ".claude")
_EDIT_TOOLS = ("edit_file", "write_file", "multi_edit")
_SESSION_EVENTS = ("Stop", "UserPromptSubmit")
_STAMP_EVENT_WARNINGS = {
    "SessionStart": "no session_init stamps; the semantic verdict cache will span sessions",
    "SubagentStop": "evaluator completions will not be stamped in telemetry",
}
_REQUIRED_SKILL = "bully-evaluator"
_COMPANION_SKILLS = ("bully", "bully-init", "bully-author", "bully-review", "bully-scheduler")


def check_python_version(version_info: tuple[int, int] = sys.version_info[:2]) -> tuple[bool, str]:
    """Return (ok, message) for the Python version check.

    Split out so tests can feed synthetic version tuples without spawning
    a different interpreter.
    """
    major, minor = version_info[:2]
    if (major, minor) >= _MIN_PYTHON:
        return True, f"[OK] Python {major}.{minor}"
    need = f"{_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}"
    return False, f"[FAIL] Python {major}.{minor} < {need} -- upgrade required"


def read_skills_paths(toml_path: Path) -> list[str]:
    """Extract `[skills] paths` from a reasonix.toml.

    tomllib when available (3.11+); a minimal regex fallback on 3.10. This is
    diagnostic-grade extraction, not a TOML parser -- exotic syntax may be
    missed, which costs a WARN downstream, never a crash.
    """
    try:
        text = toml_path.read_text()
    except OSError:
        return []
    try:
        import tomllib
    except ImportError:
        tomllib = None
    if tomllib is not None:
        try:
            skills = tomllib.loads(text).get("skills", {})
        except tomllib.TOMLDecodeError:
            return []
        paths = skills.get("paths", []) if isinstance(skills, dict) else []
        return [p for p in paths if isinstance(p, str)]
    section = re.search(r"(?ms)^\[skills\]\s*$(.*?)(?=^\[|\Z)", text)
    if section is None:
        return []
    arr = re.search(r"(?ms)^\s*paths\s*=\s*\[(.*?)\]", section.group(1))
    if arr is None:
        return []
    return [p.strip().strip("\"'") for p in arr.group(1).split(",") if p.strip().strip("\"'")]


def skill_roots(root: Path) -> list[Path]:
    """Skill discovery roots in reasonix priority order: project convention
    dirs, reasonix.toml custom paths (as-is; ~ and relative expanded against
    the project root), home convention dirs."""
    roots = [root / c / "skills" for c in _CONVENTION_DIRS]
    for raw in read_skills_paths(root / "reasonix.toml"):
        p = Path(os.path.expanduser(raw))
        roots.append(p if p.is_absolute() else root / p)
    roots.extend(Path.home() / c / "skills" for c in _CONVENTION_DIRS)
    return roots


def find_skill(name: str, roots: list[Path]) -> Path | None:
    for r in roots:
        for candidate in (r / name / "SKILL.md", r / f"{name}.md"):
            if candidate.is_file():
                return candidate
    return None


def match_covers_edit_tools(match: str | None) -> bool:
    """True when a hook entry's matcher hits all three edit tools.

    Reasonix anchors the regex (^(?:m)$); a missing matcher matches every tool.
    """
    if not match:
        return True
    try:
        pattern = re.compile(f"^(?:{match})$")
    except re.error:
        return False
    return all(pattern.match(t) for t in _EDIT_TOOLS)


def _load_hooks(settings_path: Path) -> dict:
    try:
        data = json.loads(settings_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    hooks = data.get("hooks", {}) if isinstance(data, dict) else {}
    return hooks if isinstance(hooks, dict) else {}


def hook_entry_for(event: str, settings_files: list[Path]) -> tuple[dict, Path] | None:
    """First hook entry for `event` (project file wins) that runs the bully hook."""
    for settings in settings_files:
        entries = _load_hooks(settings).get(event)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and "reasonix-hook" in str(entry.get("command", "")):
                return entry, settings
    return None


def cmd_doctor(root: Path | None = None) -> int:
    root = (root or Path.cwd()).resolve()
    ok = True

    py_ok, py_msg = check_python_version()
    print(py_msg)
    if not py_ok:
        ok = False

    cfg = root / ".bully.yml"
    if cfg.is_file():
        print(f"[OK] config present at {cfg}")
    else:
        print(f"[FAIL] no .bully.yml at {root}")
        ok = False

    parsed_rules: list[Rule] = []
    if cfg.is_file():
        try:
            parsed_rules = parse_config(str(cfg))
            print(f"[OK] config parses ({len(parsed_rules)} rules)")
        except ConfigError as e:
            print(f"[FAIL] config parse error: {e}")
            ok = False

    if cfg.is_file():
        status, detail = trust_status(str(cfg))
        if status == "trusted":
            print(f"[OK] config trusted on this machine ({detail})")
        elif status == "mismatch":
            print(
                f"[WARN] config trusted but checksum changed: {detail}. Run: bully trust --refresh"
            )
        else:
            print(
                f"[WARN] config not trusted on this machine ({detail}). "
                "Rules will not run until you run: bully trust"
            )

    ast_rule_count = sum(1 for r in parsed_rules if r.engine == "ast")
    if ast_rule_count > 0:
        if ast_grep_available():
            print(f"[OK] ast-grep on PATH ({ast_rule_count} engine:ast rule(s))")
        else:
            print(
                f"[FAIL] {ast_rule_count} engine:ast rule(s) need ast-grep. {AST_GREP_INSTALL_HINT}"
            )
            ok = False

    # --- Reasonix hook wiring ------------------------------------------------
    settings_files = [
        root / ".reasonix" / "settings.json",
        Path.home() / ".reasonix" / "settings.json",
    ]
    has_session_rules = any(r.engine == "session" for r in parsed_rules)

    pre = hook_entry_for("PreToolUse", settings_files)
    if pre is None:
        print(
            "[FAIL] no PreToolUse hook running `python3 -m bully reasonix-hook` in "
            f"{settings_files[0]} or ~/.reasonix/settings.json -- edits are not linted"
        )
        ok = False
    else:
        entry, source = pre
        if match_covers_edit_tools(entry.get("match")):
            print(f"[OK] PreToolUse hook wired in {source}")
        else:
            print(
                f"[FAIL] PreToolUse hook in {source} has match={entry.get('match')!r} "
                "which does not cover edit_file|write_file|multi_edit"
            )
            ok = False

    for event in _SESSION_EVENTS:
        found = hook_entry_for(event, settings_files)
        if found is not None:
            print(f"[OK] {event} hook wired in {found[1]}")
        elif has_session_rules:
            print(f"[FAIL] {event} hook not wired -- engine: session rules will not be enforced")
            ok = False
        else:
            print(f"[WARN] {event} hook not wired (needed only for engine: session rules)")

    for event, consequence in _STAMP_EVENT_WARNINGS.items():
        found = hook_entry_for(event, settings_files)
        if found is not None:
            print(f"[OK] {event} hook wired in {found[1]}")
        else:
            print(f"[WARN] {event} hook not wired -- {consequence}")

    # --- Skill discovery -----------------------------------------------------
    roots = skill_roots(root)
    required = find_skill(_REQUIRED_SKILL, roots)
    if required is not None:
        print(f"[OK] skill {_REQUIRED_SKILL} at {required}")
    else:
        print(
            f"[FAIL] skill {_REQUIRED_SKILL} missing -- the semantic soft-gate dispatches it. "
            "Searched project/home {.reasonix,.agents,.agent,.claude}/skills and "
            "reasonix.toml [skills] paths"
        )
        ok = False
    for name in _COMPANION_SKILLS:
        found = find_skill(name, roots)
        if found is not None:
            print(f"[OK] skill {name} at {found}")
        else:
            print(f"[WARN] skill {name} missing -- /{name} will not be available")

    return 0 if ok else 1
