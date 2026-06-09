# bully-reasonix — Milestone 1: Foundation + Deterministic Blocking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a reasonix-native agentic linter that blocks bad edits via the deterministic engine — a `PreToolUse` hook that runs bully's script/AST rules against the *pending* edit and exits 2 to refuse violating writes.

**Architecture:** Reuse bully's Python engine verbatim; add a thin reasonix harness seam. A single `reasonix-hook` CLI verb parses the reasonix Payload (stdin JSON), decodes the pending edit into an `EditEvent`, materializes the post-edit content to a temp file, and runs the existing two-phase pipeline against it — `file_path` (real) drives scope/telemetry, `content_path` (temp) is what the engines read. Deterministic `error` violations → stderr + exit 2.

**Tech Stack:** Python ≥3.10 (stdlib-only runtime), pytest, ruff, ast-grep (optional). Target harness: Reasonix Go line (local CLI `1.4.0-rc.1`; contracts validated vs `v1.4.0`).

**Spec:** `docs/superpowers/specs/2026-06-09-bully-reasonix-port-design.md` (read §2–§4 before starting).

---

## Milestone roadmap (this plan = M1 only)

- **M1 (this plan):** foundation + deterministic `PreToolUse` blocking. Working, testable linter.
- **M2:** semantic soft-gate + session verdict-cache + `bully-evaluator` subagent skill (spec §5b).
- **M3:** session rules — `Stop` record/notify + `UserPromptSubmit` gate (spec §5c).
- **M4:** 4 user skills + `bully-scheduler` + `doctor` rewrite + `reasonix.toml`/`REASONIX.md` + full test port + drop dormant Claude files + release.

Each milestone gets its own plan, written after the prior one is green.

---

## File structure (M1)

- Create: `pyproject.toml` — dist `bully-reasonix`, package `bully`, pytest `pythonpath=["src"]`.
- Create: `.gitignore`.
- Create: `tests/conftest.py` — sets `BULLY_TRUST_ALL=1`.
- Copy: `../bully/src/bully/` → `src/bully/` (engine; minus `bench/`).
- Create: `src/bully/harness/__init__.py`, `src/bully/harness/reasonix.py` — Payload → `EditEvent`.
- Create: `src/bully/diff/pending.py` — `compute_after`, `build_pending_diff`.
- Modify: `src/bully/runtime/rule_runner.py` — add `content_path` to `RuleContext`.
- Modify: `src/bully/runtime/runner.py` — `run_pipeline(..., content_path=None)`; adapters read `content_path`.
- Create: `src/bully/cli/reasonix_hook.py` — `handle_payload`, `run_reasonix_hook`.
- Modify: `src/bully/cli/__init__.py` — register the `reasonix-hook` verb.
- Create: `.reasonix/settings.json` — `PreToolUse` wiring.
- Test: `tests/test_reasonix_payload.py`, `tests/test_pending_diff.py`, `tests/test_content_path.py`, `tests/test_reasonix_hook.py`.

---

## Task 1: Scaffold the package (copy engine, packaging, test harness)

**Files:** Create `pyproject.toml`, `.gitignore`, `tests/conftest.py`; copy `src/bully/`.

- [ ] **Step 1: Copy bully's engine into place (minus bench)**

```bash
cd /Users/chrisarter/Documents/projects/bully-reasonix
mkdir -p src
cp -R ../bully/src/bully src/bully
rm -rf src/bully/bench
find src/bully -name __pycache__ -type d -prune -exec rm -rf {} +
find src/bully -name '*.pyc' -delete
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "bully-reasonix"
version = "0.1.0"
description = "Agentic linter for the Reasonix (DeepSeek) coding harness — port of bully"
requires-python = ">=3.10"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.8.0", "ast-grep-cli>=0.39"]

[project.scripts]
bully = "bully.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.ruff]
line-length = 100
target-version = "py310"
extend-exclude = [".pytest_cache"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM"]
ignore = ["E501", "SIM105"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 3: Write `.gitignore`**

```gitignore
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.venv/
.bully/
dist/
*.egg-info/
```

- [ ] **Step 4: Write `tests/conftest.py`**

```python
import os

# The trust gate short-circuits rule execution for un-reviewed configs.
# Tests exercise the pipeline directly, so bypass it globally.
os.environ.setdefault("BULLY_TRUST_ALL", "1")
```

- [ ] **Step 5: Verify the engine imports**

Run: `PYTHONPATH=src python3 -c "from bully.runtime.runner import run_pipeline; from bully import BULLY_VERSION; print('engine ok', BULLY_VERSION)"`
Expected: `engine ok <version>` (no ImportError).

- [ ] **Step 6: Verify pytest collects (zero tests is fine)**

Run: `python3 -m pytest -q`
Expected: `no tests ran` (exit 5) or `0 passed` — importantly, no collection/import errors.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore tests/conftest.py src/
git commit -m "M1 T1: scaffold bully-reasonix (copy engine, packaging, test harness)"
```

---

## Task 2: `EditEvent` + reasonix Payload decoder

**Files:**
- Create: `src/bully/harness/__init__.py`, `src/bully/harness/reasonix.py`
- Test: `tests/test_reasonix_payload.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reasonix_payload.py
from bully.harness.reasonix import edit_event_from_payload


def _payload(tool, args, cwd="/proj"):
    return {"event": "PreToolUse", "cwd": cwd, "toolName": tool, "toolArgs": args}


def test_edit_file_decodes_single_replace():
    ev = edit_event_from_payload(
        _payload("edit_file", {"path": "app.py", "old_string": "a", "new_string": "b"})
    )
    assert ev.tool == "edit_file"
    assert ev.file_path == "/proj/app.py"  # relative path resolved against cwd
    assert ev.is_write is False
    assert ev.content is None
    assert ev.edits == (("a", "b", False),)


def test_write_file_decodes_content():
    ev = edit_event_from_payload(_payload("write_file", {"path": "/abs/x.py", "content": "hi"}))
    assert ev.is_write is True
    assert ev.file_path == "/abs/x.py"  # absolute path left as-is
    assert ev.content == "hi"
    assert ev.edits == ()


def test_multi_edit_decodes_steps_with_replace_all():
    ev = edit_event_from_payload(
        _payload(
            "multi_edit",
            {"path": "m.py", "edits": [
                {"old_string": "a", "new_string": "b"},
                {"old_string": "c", "new_string": "d", "replace_all": True},
            ]},
        )
    )
    assert ev.edits == (("a", "b", False), ("c", "d", True))


def test_toolargs_accepts_json_string():
    # toolArgs is normally a nested object, but tolerate a JSON-encoded string.
    ev = edit_event_from_payload(_payload("edit_file", '{"path": "z.py", "old_string": "a", "new_string": "b"}'))
    assert ev.file_path == "/proj/z.py"


def test_non_edit_tool_returns_none():
    assert edit_event_from_payload(_payload("read_file", {"path": "z.py"})) is None


def test_missing_path_returns_none():
    assert edit_event_from_payload(_payload("edit_file", {"old_string": "a", "new_string": "b"})) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_reasonix_payload.py -q`
Expected: FAIL — `ModuleNotFoundError: bully.harness`.

- [ ] **Step 3: Implement `harness/reasonix.py`**

Create `src/bully/harness/__init__.py` (empty file).

```python
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
    file_path: str                              # absolute, resolved against cwd
    is_write: bool
    content: str | None                         # write_file only
    edits: tuple[tuple[str, str, bool], ...]    # (old, new, replace_all)


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
                (e.get("old_string", "") or "", e.get("new_string", "") or "", bool(e.get("replace_all", False)))
            )
        return EditEvent(tool, file_path, False, None, tuple(steps))

    # edit_file
    return EditEvent(
        tool, file_path, False, None,
        ((args.get("old_string", "") or "", args.get("new_string", "") or "", False),),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_reasonix_payload.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/bully/harness/ tests/test_reasonix_payload.py
git commit -m "M1 T2: reasonix Payload -> EditEvent decoder"
```

---

## Task 3: Pending diff (`compute_after` + `build_pending_diff`)

**Files:**
- Create: `src/bully/diff/pending.py`
- Test: `tests/test_pending_diff.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pending_diff.py
from bully.diff.pending import build_pending_diff_from, compute_after
from bully.harness.reasonix import EditEvent


def _edit(old, new, replace_all=False):
    return EditEvent("edit_file", "/x.py", False, None, ((old, new, replace_all),))


def test_compute_after_single_replace():
    assert compute_after("x = 1\n", _edit("x = 1", "x = 2")) == "x = 2\n"


def test_compute_after_write_uses_content():
    ev = EditEvent("write_file", "/x.py", True, "brand new\n", ())
    assert compute_after("old\n", ev) == "brand new\n"


def test_compute_after_multi_edit_in_order_and_replace_all():
    ev = EditEvent("multi_edit", "/x.py", False, None, (("a", "b", False), ("z", "Z", True)))
    assert compute_after("a a z z\n", ev) == "b a Z Z\n"  # first a->b once; all z->Z


def test_build_pending_diff_is_unified_with_real_paths():
    diff = build_pending_diff_from("/proj/app.py", "x = 1\n", "x = 2\n", is_write=False)
    assert "--- /proj/app.py.before" in diff
    assert "+++ /proj/app.py.after" in diff
    assert "-x = 1" in diff and "+x = 2" in diff


def test_build_pending_diff_write_returns_line_numbered_content():
    out = build_pending_diff_from("/proj/new.py", "", "line one\nline two\n", is_write=True)
    assert "1: line one" in out and "2: line two" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_pending_diff.py -q`
Expected: FAIL — `ModuleNotFoundError: bully.diff.pending`.

- [ ] **Step 3: Implement `diff/pending.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_pending_diff.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/bully/diff/pending.py tests/test_pending_diff.py
git commit -m "M1 T3: pre-write pending diff builder"
```

---

## Task 4: Thread `content_path` through the engine (backward-compatible)

**Why:** deterministic engines read the file on disk; pre-write that's stale. `content_path` lets the hook point the engines at the materialized pending content while `file_path` stays real for scope/baseline/telemetry. Default `content_path=file_path` → existing behavior unchanged.

**Files:**
- Modify: `src/bully/runtime/rule_runner.py`
- Modify: `src/bully/runtime/runner.py`
- Test: `tests/test_content_path.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_content_path.py
import textwrap

from bully.runtime.runner import run_pipeline


def _project(tmp_path, rule_script):
    (tmp_path / ".bully.yml").write_text(textwrap.dedent(f"""\
        schema_version: 1
        rules:
          no-forbidden:
            description: "No FORBIDDEN marker."
            engine: script
            scope: ["*.py"]
            severity: error
            script: "{rule_script}"
    """))
    return tmp_path


def test_default_content_path_reads_file_on_disk(tmp_path):
    proj = _project(tmp_path, "grep -n FORBIDDEN {file} && exit 1 || exit 0")
    f = proj / "app.py"
    f.write_text("x = 1  # FORBIDDEN\n")  # the real file is dirty
    result = run_pipeline(str(proj / ".bully.yml"), str(f), diff="")
    assert result["status"] == "blocked"


def test_content_path_overrides_what_engines_read(tmp_path):
    proj = _project(tmp_path, "grep -n FORBIDDEN {file} && exit 1 || exit 0")
    clean = proj / "app.py"
    clean.write_text("x = 1\n")  # real file is CLEAN (scope matches this path)
    pending = proj / ".bully" / "tmp" / "pending.py"
    pending.parent.mkdir(parents=True)
    pending.write_text("x = 1  # FORBIDDEN\n")  # pending content is DIRTY
    result = run_pipeline(str(proj / ".bully.yml"), str(clean), diff="", content_path=str(pending))
    assert result["status"] == "blocked"  # engines read pending, not the clean real file


def test_content_path_clean_passes(tmp_path):
    proj = _project(tmp_path, "grep -n FORBIDDEN {file} && exit 1 || exit 0")
    real = proj / "app.py"
    real.write_text("x = 1  # FORBIDDEN\n")  # real dirty, but...
    pending = proj / ".bully" / "tmp" / "pending.py"
    pending.parent.mkdir(parents=True)
    pending.write_text("x = 1\n")  # ...pending is clean
    result = run_pipeline(str(proj / ".bully.yml"), str(real), diff="", content_path=str(pending))
    assert result["status"] == "pass"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_content_path.py -q`
Expected: FAIL — `test_content_path_overrides...` errors on unexpected `content_path` kwarg (TypeError).

- [ ] **Step 3: Add `content_path` to `RuleContext`**

In `src/bully/runtime/rule_runner.py`, add the field to the frozen dataclass:
```python
@dataclass(frozen=True)
class RuleContext:
    file_path: str
    diff: str
    baseline: dict  # keys are (rule_id, rel_path, line, checksum) tuples
    config_path: str | None
    content_path: str | None = None  # what engines read; None -> file_path
```

In the same file, in `evaluate_rule`, change the disable-comment lookup to read the pending content (leave `is_baselined` keyed on the real `file_path`):
```python
        filtered: list[Violation] = []
        for v in violations:
            disable_path = ctx.content_path or ctx.file_path
            if line_has_disable(disable_path, v.line, rule.id):
                continue
            if is_baselined(ctx.baseline, rule.id, ctx.config_path, ctx.file_path, v.line):
                continue
            filtered.append(v)
```

- [ ] **Step 4: Thread `content_path` through `run_pipeline`**

In `src/bully/runtime/runner.py`, add the keyword-only param:
```python
def run_pipeline(
    config_path: str,
    file_path: str,
    diff: str,
    rule_filter: set[str] | None = None,
    *,
    include_skipped: bool = False,
    content_path: str | None = None,
    phase_timer=_NOOP_PHASE_TIMER,
) -> dict:
```

Set it on the `RuleContext` and point the engine adapters at it:
```python
    rule_ctx = RuleContext(
        file_path=file_path,
        diff=diff,
        baseline=baseline,
        config_path=config_path,
        content_path=content_path or file_path,
    )
    config_root = str(Path(config_path).resolve().parent)

    def _adapter_script(rule, rctx):
        return execute_script_rule(rule, rctx.content_path, rctx.diff, cwd=config_root)

    def _adapter_ast(rule, rctx):
        return execute_ast_rule(rule, rctx.content_path)
```

- [ ] **Step 5: Run to verify the new tests pass**

Run: `python3 -m pytest tests/test_content_path.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add src/bully/runtime/rule_runner.py src/bully/runtime/runner.py tests/test_content_path.py
git commit -m "M1 T4: thread backward-compatible content_path through the engine"
```

---

## Task 5: `reasonix-hook` PreToolUse path (parse → materialize → run → block)

**Files:**
- Create: `src/bully/cli/reasonix_hook.py`
- Modify: `src/bully/cli/__init__.py` (register the verb)
- Test: `tests/test_reasonix_hook.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reasonix_hook.py
import textwrap

from bully.cli.reasonix_hook import handle_payload


def _proj(tmp_path):
    (tmp_path / ".bully.yml").write_text(textwrap.dedent("""\
        schema_version: 1
        rules:
          no-forbidden:
            description: "No FORBIDDEN marker."
            engine: script
            scope: ["*.py"]
            severity: error
            script: "grep -n FORBIDDEN {file} && exit 1 || exit 0"
    """))
    f = tmp_path / "app.py"
    f.write_text("x = 1\n")
    return tmp_path, f


def _pre(tmp_path, args, tool="edit_file"):
    return {"event": "PreToolUse", "cwd": str(tmp_path), "toolName": tool, "toolArgs": args}


def test_blocks_edit_that_introduces_violation(tmp_path):
    proj, f = _proj(tmp_path)
    code, msg = handle_payload(
        _pre(proj, {"path": "app.py", "old_string": "x = 1", "new_string": "x = 1  # FORBIDDEN"})
    )
    assert code == 2
    assert "no-forbidden" in msg

    # the bad edit never landed — the real file is untouched
    assert "FORBIDDEN" not in f.read_text()
    # and the materialized temp file was cleaned up (glob on a missing dir is also empty)
    assert not list((proj / ".bully" / "tmp").glob("pending-*"))


def test_passes_clean_edit(tmp_path):
    proj, _ = _proj(tmp_path)
    code, msg = handle_payload(
        _pre(proj, {"path": "app.py", "old_string": "x = 1", "new_string": "x = 2"})
    )
    assert code == 0
    assert msg == ""


def test_write_file_new_file_is_evaluated(tmp_path):
    proj, _ = _proj(tmp_path)
    code, _ = handle_payload(
        _pre(proj, {"path": "new.py", "content": "y = 1  # FORBIDDEN\n"}, tool="write_file")
    )
    assert code == 2


def test_non_pretooluse_event_is_noop(tmp_path):
    proj, _ = _proj(tmp_path)
    assert handle_payload({"event": "Stop", "cwd": str(proj)}) == (0, "")


def test_non_edit_tool_is_noop(tmp_path):
    proj, _ = _proj(tmp_path)
    assert handle_payload(_pre(proj, {"path": "app.py"}, tool="read_file")) == (0, "")


def test_no_config_is_noop(tmp_path):
    # no .bully.yml anywhere above the file -> no-op, no crash
    (tmp_path / "loose.py").write_text("z = 1\n")
    code, _ = handle_payload(_pre(tmp_path, {"path": "loose.py", "old_string": "z = 1", "new_string": "z = 2"}))
    assert code == 0


def test_malformed_payload_fails_open(tmp_path):
    assert handle_payload({}) == (0, "")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_reasonix_hook.py -q`
Expected: FAIL — `ModuleNotFoundError: bully.cli.reasonix_hook`.

- [ ] **Step 3: Implement `cli/reasonix_hook.py`**

```python
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
```

- [ ] **Step 4: Register the verb in `cli/__init__.py`**

In `src/bully/cli/main()`, alongside the other positional subcommand short-circuits (e.g. after the `subagent-stop` block), add:
```python
    if len(sys.argv) >= 2 and sys.argv[1] == "reasonix-hook":
        from bully.cli.reasonix_hook import run_reasonix_hook  # noqa: PLC0415

        sys.exit(run_reasonix_hook())
```

- [ ] **Step 5: Run to verify the tests pass**

Run: `python3 -m pytest tests/test_reasonix_hook.py -q`
Expected: PASS (7 passed).

- [ ] **Step 6: Commit**

```bash
git add src/bully/cli/reasonix_hook.py src/bully/cli/__init__.py tests/test_reasonix_hook.py
git commit -m "M1 T5: reasonix-hook PreToolUse deterministic blocking path"
```

---

## Task 6: Wire `.reasonix/settings.json`, dogfood end-to-end, finalize M1

**Files:**
- Create: `.reasonix/settings.json`
- Modify: `CLAUDE.md` (status pointer)
- Test: end-to-end CLI invocation

- [ ] **Step 1: Write `.reasonix/settings.json`**

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "match": "edit_file|write_file|multi_edit",
        "command": "python3 -m bully reasonix-hook",
        "timeout": 15000,
        "description": "bully-reasonix deterministic lint gate"
      }
    ]
  }
}
```

- [ ] **Step 2: Dogfood — feed a real Payload through the CLI and confirm exit 2**

Run:
```bash
cd /Users/chrisarter/Documents/projects/bully-reasonix
TMP=$(mktemp -d)
printf 'schema_version: 1\nrules:\n  no-forbidden:\n    description: "no FORBIDDEN"\n    engine: script\n    scope: ["*.py"]\n    severity: error\n    script: "grep -n FORBIDDEN {file} && exit 1 || exit 0"\n' > "$TMP/.bully.yml"
printf 'x = 1\n' > "$TMP/app.py"
printf '{"event":"PreToolUse","cwd":"%s","toolName":"edit_file","toolArgs":{"path":"app.py","old_string":"x = 1","new_string":"x = 1  # FORBIDDEN"}}' "$TMP" \
  | BULLY_TRUST_ALL=1 PYTHONPATH=src python3 -m bully reasonix-hook; echo "exit=$?"
grep -c FORBIDDEN "$TMP/app.py"   # real file must be untouched -> 0
```
Expected: stderr shows the `no-forbidden` block message, `exit=2`, and the real file grep prints `0` (the bad edit never landed).

- [ ] **Step 3: Dogfood the clean-edit pass case**

Run:
```bash
printf '{"event":"PreToolUse","cwd":"%s","toolName":"edit_file","toolArgs":{"path":"app.py","old_string":"x = 1","new_string":"x = 2"}}' "$TMP" \
  | BULLY_TRUST_ALL=1 PYTHONPATH=src python3 -m bully reasonix-hook; echo "exit=$?"
```
Expected: no stderr, `exit=0`.

- [ ] **Step 4: Full test suite + lint**

Run: `python3 -m pytest -q`
Expected: all M1 tests pass (≈21 passed).

Run: `python3 -m ruff check src tests`
Expected: no errors (fix any reported).

- [ ] **Step 5: Update `CLAUDE.md` Status section to point at this milestone**

Replace the `## Status` paragraph in `CLAUDE.md` with:
```markdown
## Status

Target: Reasonix **Go line** (local CLI `1.4.0-rc.1`; contracts validated vs `v1.4.0`). Design: `docs/superpowers/specs/2026-06-09-bully-reasonix-port-design.md`.

- **M1 done:** deterministic `PreToolUse` blocking — `python3 -m bully reasonix-hook` runs script/AST rules against the pending edit (`content_path`), exit 2 blocks. Wired in `.reasonix/settings.json`.
- Next: M2 semantic soft-gate + verdict-cache (`docs/superpowers/plans/`).
```

- [ ] **Step 6: Commit**

```bash
git add .reasonix/settings.json CLAUDE.md
git commit -m "M1 T6: wire .reasonix/settings.json, dogfood, finalize M1"
```

---

## Done-when (M1 acceptance)

- `python3 -m pytest -q` green; `ruff check` clean.
- Piping a `PreToolUse` edit_file/write_file/multi_edit Payload that introduces an `error`-severity deterministic violation → **exit 2**, violation on **stderr**, real file untouched, no temp residue.
- A clean edit → exit 0, silent.
- Non-PreToolUse events and non-edit tools → exit 0 no-op. Malformed payload / internal error → exit 0 (fail open).
- `content_path` defaults to `file_path` — the copied engine's own behavior is unchanged.

## Deferred to later milestones (do NOT do in M1)

- Semantic rules (`status: "evaluate"`) currently exit 0 (no-op) — the soft-gate + verdict-cache is **M2**.
- `Stop`/`UserPromptSubmit`/`SessionStart`/`SubagentStop` handling — **M3**.
- Removing dormant Claude files (`cli/hook_mode.py`, `cli/session.py`, `cli/stop.py`), `doctor` rewrite, skills, `reasonix.toml`, `REASONIX.md`, full bully test port — **M4**.
