# bully-reasonix — Milestone 3: Session Rules + Fail-Open Telemetry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Port bully's `engine: session` rules to Reasonix and close out the M1/M2 TODOs: per-edit changed-set recording, Stop notify, a `UserPromptSubmit` exit-2 gate, `SessionStart`/`SubagentStop` telemetry stamps, fail-open telemetry, and session-scoping of the M2 verdict cache.

**Architecture:** Reasonix's `Stop` cannot block (spec §2), so session-rule enforcement splits in two: **Stop notifies** (exit 1 → `notify`) and keeps the changed-set when error rules are unsatisfied; the next **`UserPromptSubmit` gates** (exit 2 → block, message fed to the agent) until the rules are satisfied. The changed-set is accumulated per edit on the **PreToolUse allow path** (there is no PostToolUse wiring in our design — spec §3/§7). `SessionStart` stamps a `session_init` record which doubles as the verdict-cache session window anchor.

**Tech Stack:** Python ≥3.10 stdlib-only, pytest, ruff. Target: Reasonix Go line (local CLI `1.4.0-rc.1`).

**Spec:** `docs/superpowers/specs/2026-06-09-bully-reasonix-port-design.md` §5c, §8. **Builds on:** M1 (`cli/reasonix_hook.py` PreToolUse path) + M2 (verdict cache, soft-gate).

---

## Reasonix facts (validated `v1.4.0` — do not re-derive)

- `IsBlocking(e) = (e == PreToolUse || e == UserPromptSubmit)`. **Stop and SubagentStop cannot block** — any nonzero non-gating exit is a `warn` → `notify` (user-facing).
- On a gating event, **exit 2 = block** and the stderr message is returned to the **agent** (model-facing). Exit 1 = warn → notify. **Exit 0 = pass is silent** (output discarded) — a SessionStart banner therefore never renders; `REASONIX.md` (M4) carries the standing instructions instead.
- Non-tool events always match a hook entry with no `match` key.
- Stdin Payload for non-tool events still carries `cwd` — resolve the config by searching upward from `payload.cwd`.

## Engine facts (already in this repo — do not re-derive)

- `cli/session.py` and `cli/stop.py` are verbatim copies from `../bully` (Claude Code semantics: Stop exits 2). M3 rewrites `stop.py` for Reasonix semantics; `session.py` is reused as-is (`cmd_session_record`, `cmd_session_start`).
- `config/parser.py` accepts `engine: session` with **both** `when:` and `require:` as nested **block-style** mappings (the stdlib mini-YAML parser does NOT accept flow mappings like `when: { changed_any: [...] }` — write fixtures block-style). Valid severities: `error` | `warning`. `scope` defaults to `*` and is unused by session rules.
- `config/scope.py:scope_glob_matches` is right-anchored: repo-relative globs (`src/**`) match absolute recorded paths.
- `state/telemetry.py`: `telemetry_path(config_path) -> .bully/log.jsonl` (provisions `.bully/`); `append_record(log_path, dict)`.
- `state/verdict_cache.py:cached_verdict` is whole-log latest-wins (M2); M3 adds the `session_init` window reset.
- `run_pipeline` ignores `engine: session` rules on the per-edit path (they are only evaluated by the Stop/UserPromptSubmit logic) — same as bully.
- Tests set `BULLY_TRUST_ALL=1` globally in `tests/conftest.py`, so `trust_status` returns trusted.

## Decisions (made here, not in the spec — record, don't re-litigate)

1. **Changed-set recording happens on the PreToolUse allow path** (final exit ≠ 2 and status ≠ `untrusted`), reusing `cmd_session_record`. Rationale: it's the only per-edit hook in our wiring; an exit-2 edit never lands so it must not be recorded. Slight over-record if the tool itself later fails — acceptable.
2. **bully's strict semantic Stop-gate (`_semantic_gate_blocks` in `../bully/cli/stop.py`) is NOT ported.** Under the M2 soft-gate an edit cannot land without a logged verdict, so "unadjudicated dispatched rule" is unreachable; a `violation` verdict already re-blocks the re-issued edit at PreToolUse. Spec §5c lists only session rules for Stop/UserPromptSubmit.
3. **The `bully stop` CLI verb adopts Reasonix semantics** (exit 1 notify / keep set on errors), replacing the Claude-era exit-2 behavior, so the verb and the hook agree.
4. **Session lifecycle:** error violations at Stop → keep `session.jsonl` (the gate needs it); clean or warning-only Stop → delete it. `UserPromptSubmit` never deletes — clearing is Stop's job.

---

## File structure (M3)

- Modify: `src/bully/cli/reasonix_hook.py` — event dispatch table; record on allow; `Stop`/`UserPromptSubmit`/`SessionStart`/`SubagentStop` handlers; fail-open telemetry + top-level guard.
- Rewrite: `src/bully/cli/stop.py` — `_read_changed`, `_session_rule_violations`, `evaluate_session`, `reasonix_stop`, `reasonix_prompt_gate`; `cmd_stop_main` rewired; `cmd_subagent_stop` kept as-is.
- Modify: `src/bully/state/verdict_cache.py` — `session_init` window reset in `cached_verdict`.
- Modify: `.reasonix/settings.json` — add the four event wirings.
- Modify: `CLAUDE.md` — status section (Task 6).
- Test: `tests/test_session_rules.py` (new), `tests/test_reasonix_hook.py` (extend), `tests/test_verdict_cache.py` (extend).

---

## Task 1: Record the changed-set on the PreToolUse allow path

**Files:**
- Modify: `src/bully/cli/reasonix_hook.py`
- Test: `tests/test_session_rules.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session_rules.py`. Note `_det_proj` is an f-string, so the engine's `{file}` placeholder is escaped as `{{file}}`:

```python
# tests/test_session_rules.py
"""M3: session changed-set recording, Stop notify, UserPromptSubmit gate."""
import json
import textwrap

from bully.cli.reasonix_hook import handle_payload


def _det_proj(tmp_path, severity="error"):
    (tmp_path / ".bully.yml").write_text(textwrap.dedent(f"""\
        schema_version: 1
        rules:
          no-forbidden:
            description: "No FORBIDDEN marker."
            engine: script
            scope: ["*.py"]
            severity: {severity}
            script: "grep -n FORBIDDEN {{file}} && exit 1 || exit 0"
    """))
    f = tmp_path / "app.py"
    f.write_text("x = 1\n")
    return tmp_path, f


def _pre(proj, args, tool="edit_file"):
    return {"event": "PreToolUse", "cwd": str(proj), "toolName": tool, "toolArgs": args}


def _recorded(proj):
    sf = proj / ".bully" / "session.jsonl"
    if not sf.exists():
        return []
    return [json.loads(line)["file"] for line in sf.read_text().splitlines() if line.strip()]


def test_allowed_edit_is_recorded(tmp_path):
    proj, f = _det_proj(tmp_path)
    code, _ = handle_payload(_pre(proj, {"path": "app.py", "old_string": "x = 1", "new_string": "x = 2"}))
    assert code == 0
    assert _recorded(proj) == [str(f)]


def test_blocked_edit_is_not_recorded(tmp_path):
    proj, _ = _det_proj(tmp_path)
    code, _ = handle_payload(
        _pre(proj, {"path": "app.py", "old_string": "x = 1", "new_string": "x = 1  # FORBIDDEN"})
    )
    assert code == 2
    assert _recorded(proj) == []


def test_warned_edit_is_recorded(tmp_path):
    # warning severity -> exit 1 -> the edit still lands, so it is part of the changed-set
    proj, f = _det_proj(tmp_path, severity="warning")
    code, _ = handle_payload(
        _pre(proj, {"path": "app.py", "old_string": "x = 1", "new_string": "x = 1  # FORBIDDEN"})
    )
    assert code == 1
    assert _recorded(proj) == [str(f)]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_session_rules.py -v`
Expected: `test_allowed_edit_is_recorded` and `test_warned_edit_is_recorded` FAIL (nothing records yet, so `_recorded` returns `[]`); `test_blocked_edit_is_not_recorded` passes vacuously — keep it, it guards the Step 3 implementation.

- [ ] **Step 3: Refactor `handle_payload` into a dispatch + record on allow**

In `src/bully/cli/reasonix_hook.py`, add the import:

```python
from bully.cli.session import cmd_session_record
```

Replace the existing `handle_payload` with:

```python
def handle_payload(payload: dict) -> tuple[int, str]:
    """Core hook logic. Returns (exit_code, stderr_message)."""
    if payload.get("event") == "PreToolUse":
        return _handle_pretooluse(payload)
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
    except Exception:  # noqa: BLE001 — fail open: never block on an internal bug
        # TODO(M3 Task 4): best-effort hook_fail_open telemetry record here.
        return 0, ""
    code, msg = _render(result, config)
    if code != 2 and result.get("status") != "untrusted":
        # Only exit 2 stops the tool, so this edit will land: add it to the
        # session changed-set for the Stop / UserPromptSubmit session rules.
        try:
            cmd_session_record(str(config), ev.file_path)
        except Exception:  # noqa: BLE001 — recording must never break the gate
            pass
    return code, msg
```

(The body of the `try` block is M1/M2 code moved verbatim; only the dispatch split and the `cmd_session_record` call are new.)

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_session_rules.py tests/test_reasonix_hook.py tests/test_semantic_gate.py -v`
Expected: ALL PASS (existing M1/M2 hook tests must stay green).

- [ ] **Step 5: Commit**

```bash
git add tests/test_session_rules.py src/bully/cli/reasonix_hook.py
git commit -m "M3 T1: record session changed-set on PreToolUse allow path"
```

---

## Task 2: Reasonix session-rule evaluation (`cli/stop.py` rewrite)

**Files:**
- Rewrite: `src/bully/cli/stop.py`
- Test: `tests/test_session_rules.py` (extend)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_session_rules.py`:

```python
from bully.cli.stop import reasonix_prompt_gate, reasonix_stop


def _session_proj(tmp_path):
    # NOTE: when/require must be block-style nested mappings — the stdlib
    # mini-YAML parser does not accept flow mappings.
    (tmp_path / ".bully.yml").write_text(textwrap.dedent("""\
        schema_version: 1
        rules:
          src-needs-tests:
            description: "Changes under src/ require a test change."
            engine: session
            severity: error
            when:
              changed_any: ['src/**']
            require:
              changed_any: ['tests/**']
    """))
    return tmp_path


def _write_session(proj, files):
    bd = proj / ".bully"
    bd.mkdir(exist_ok=True)
    (bd / "session.jsonl").write_text("".join(json.dumps({"file": f}) + "\n" for f in files))


def _warning_variant(proj):
    cfg = (proj / ".bully.yml").read_text().replace("severity: error", "severity: warning")
    (proj / ".bully.yml").write_text(cfg)


def test_stop_no_session_file_is_silent(tmp_path):
    proj = _session_proj(tmp_path)
    assert reasonix_stop(str(proj / ".bully.yml")) == (0, "")


def test_stop_satisfied_resets_changed_set(tmp_path):
    proj = _session_proj(tmp_path)
    _write_session(proj, ["src/auth.py", "tests/test_auth.py"])
    assert reasonix_stop(str(proj / ".bully.yml")) == (0, "")
    assert not (proj / ".bully" / "session.jsonl").exists()


def test_stop_error_violation_notifies_and_keeps_set(tmp_path):
    proj = _session_proj(tmp_path)
    _write_session(proj, ["src/auth.py"])
    code, msg = reasonix_stop(str(proj / ".bully.yml"))
    assert code == 1                                   # notify -- Stop can't block in Reasonix
    assert "src-needs-tests" in msg
    assert "gate the next prompt" in msg
    assert (proj / ".bully" / "session.jsonl").exists()  # kept for the prompt gate


def test_stop_warning_only_notifies_and_resets(tmp_path):
    proj = _session_proj(tmp_path)
    _warning_variant(proj)
    _write_session(proj, ["src/auth.py"])
    code, msg = reasonix_stop(str(proj / ".bully.yml"))
    assert code == 1
    assert "src-needs-tests" in msg
    assert not (proj / ".bully" / "session.jsonl").exists()  # warnings don't gate


def test_prompt_gate_blocks_on_unsatisfied_error_rule(tmp_path):
    proj = _session_proj(tmp_path)
    _write_session(proj, ["src/auth.py"])
    code, msg = reasonix_prompt_gate(str(proj / ".bully.yml"))
    assert code == 2
    assert "src-needs-tests" in msg


def test_prompt_gate_clean_passes(tmp_path):
    proj = _session_proj(tmp_path)
    _write_session(proj, ["src/auth.py", "tests/test_auth.py"])
    assert reasonix_prompt_gate(str(proj / ".bully.yml")) == (0, "")


def test_prompt_gate_warning_only_passes(tmp_path):
    proj = _session_proj(tmp_path)
    _warning_variant(proj)
    _write_session(proj, ["src/auth.py"])
    assert reasonix_prompt_gate(str(proj / ".bully.yml")) == (0, "")
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_session_rules.py -v`
Expected: the whole file errors at collection with `ImportError: cannot import name 'reasonix_prompt_gate'`.

- [ ] **Step 3: Rewrite `src/bully/cli/stop.py`** with this full content:

```python
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
```

(`cmd_stop` is gone — `cmd_stop_main` is the only caller and now wraps `reasonix_stop`. `untrusted_stderr` import dropped. `cmd_subagent_stop*` are byte-identical to before.)

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_session_rules.py -v && ruff check src/bully/cli/stop.py`
Expected: ALL PASS, ruff clean.

- [ ] **Step 5: Run the whole suite** (the old Claude-era `bully stop` tests, if any reference `cmd_stop`, must be caught here)

Run: `python3 -m pytest -q`
Expected: ALL PASS. If anything imports `cmd_stop`, update it to `reasonix_stop` semantics deliberately — do not re-add Claude-era exit-2-at-Stop behavior.

- [ ] **Step 6: Commit**

```bash
git add src/bully/cli/stop.py tests/test_session_rules.py
git commit -m "M3 T2: reasonix session-rule evaluation (Stop notify + prompt gate)"
```

---

## Task 3: Wire Stop / UserPromptSubmit / SessionStart / SubagentStop into the hook

**Files:**
- Modify: `src/bully/cli/reasonix_hook.py`
- Modify: `.reasonix/settings.json`
- Test: `tests/test_session_rules.py` (extend)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_session_rules.py`:

```python
def test_stop_event_dispatches(tmp_path):
    proj = _session_proj(tmp_path)
    _write_session(proj, ["src/auth.py"])
    code, msg = handle_payload({"event": "Stop", "cwd": str(proj)})
    assert code == 1
    assert "src-needs-tests" in msg


def test_prompt_submit_event_gates(tmp_path):
    proj = _session_proj(tmp_path)
    _write_session(proj, ["src/auth.py"])
    code, msg = handle_payload({"event": "UserPromptSubmit", "cwd": str(proj)})
    assert code == 2
    assert "src-needs-tests" in msg


def test_session_start_stamps_session_init(tmp_path):
    proj = _session_proj(tmp_path)
    assert handle_payload({"event": "SessionStart", "cwd": str(proj)})[0] == 0
    assert '"session_init"' in (proj / ".bully" / "log.jsonl").read_text()


def test_subagent_stop_stamps_record(tmp_path):
    proj = _session_proj(tmp_path)
    assert handle_payload({"event": "SubagentStop", "cwd": str(proj)}) == (0, "")
    assert '"subagent_stop"' in (proj / ".bully" / "log.jsonl").read_text()


def test_event_without_config_is_noop(tmp_path):
    assert handle_payload({"event": "Stop", "cwd": str(tmp_path)}) == (0, "")
    assert handle_payload({"event": "UserPromptSubmit", "cwd": str(tmp_path)}) == (0, "")


def test_full_session_gate_loop(tmp_path):
    proj = _session_proj(tmp_path)
    (proj / "src").mkdir()
    (proj / "tests").mkdir()
    (proj / "src" / "auth.py").write_text("a = 1\n")
    (proj / "tests" / "test_auth.py").write_text("t = 1\n")

    def edit(path, old, new):
        return handle_payload({"event": "PreToolUse", "cwd": str(proj), "toolName": "edit_file",
                               "toolArgs": {"path": path, "old_string": old, "new_string": new}})

    # turn 1: edit src only -> recorded
    assert edit("src/auth.py", "a = 1", "a = 2")[0] == 0
    # Stop: violation -> notify, keep the set
    code, msg = handle_payload({"event": "Stop", "cwd": str(proj)})
    assert code == 1 and "src-needs-tests" in msg
    # next prompt: gated
    code, msg = handle_payload({"event": "UserPromptSubmit", "cwd": str(proj)})
    assert code == 2 and "src-needs-tests" in msg
    # the agent satisfies the rule by editing a test file
    assert edit("tests/test_auth.py", "t = 1", "t = 2")[0] == 0
    # prompt now passes; Stop is clean and resets the set
    assert handle_payload({"event": "UserPromptSubmit", "cwd": str(proj)}) == (0, "")
    assert handle_payload({"event": "Stop", "cwd": str(proj)}) == (0, "")
    assert not (proj / ".bully" / "session.jsonl").exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_session_rules.py -v`
Expected: the 6 new tests FAIL (non-PreToolUse events currently return `(0, "")`).

- [ ] **Step 3: Add the handlers and dispatch** in `src/bully/cli/reasonix_hook.py`.

Update imports:

```python
from bully.cli.session import cmd_session_record, cmd_session_start
from bully.cli.stop import cmd_subagent_stop, reasonix_prompt_gate, reasonix_stop
```

Replace `handle_payload` (from Task 1) with the dispatch table, and add the handlers:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_session_rules.py tests/test_reasonix_hook.py -v`
Expected: ALL PASS (`test_non_pretooluse_event_is_noop` still passes — its project has no session file, so Stop is silent).

- [ ] **Step 5: Wire the events in `.reasonix/settings.json`** — replace the file with:

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
    ],
    "Stop": [
      {
        "command": "python3 -m bully reasonix-hook",
        "timeout": 15000,
        "description": "bully-reasonix session-rule check (notify)"
      }
    ],
    "UserPromptSubmit": [
      {
        "command": "python3 -m bully reasonix-hook",
        "timeout": 10000,
        "description": "bully-reasonix session-rule gate"
      }
    ],
    "SessionStart": [
      {
        "command": "python3 -m bully reasonix-hook",
        "description": "bully-reasonix session_init telemetry stamp"
      }
    ],
    "SubagentStop": [
      {
        "command": "python3 -m bully reasonix-hook",
        "description": "bully-reasonix subagent telemetry stamp"
      }
    ]
  }
}
```

Validate: `python3 -c "import json; json.load(open('.reasonix/settings.json'))" && echo OK`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add src/bully/cli/reasonix_hook.py .reasonix/settings.json tests/test_session_rules.py
git commit -m "M3 T3: wire Stop/UserPromptSubmit/SessionStart/SubagentStop events"
```

---

## Task 4: Fail-open telemetry (`hook_fail_open`)

**Files:**
- Modify: `src/bully/cli/reasonix_hook.py`
- Test: `tests/test_reasonix_hook.py` (extend)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_reasonix_hook.py` (add `import json` and `import io` at the top of the file):

```python
def test_internal_error_fails_open_and_logs(tmp_path, monkeypatch):
    proj, _ = _proj(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr("bully.cli.reasonix_hook.run_pipeline", boom)
    code, msg = handle_payload(
        _pre(proj, {"path": "app.py", "old_string": "x = 1", "new_string": "x = 2"})
    )
    assert (code, msg) == (0, "")  # fail open: the edit is not blocked
    lines = (proj / ".bully" / "log.jsonl").read_text().splitlines()
    rec = json.loads([ln for ln in lines if "hook_fail_open" in ln][-1])
    assert rec["type"] == "hook_fail_open"
    assert rec["event"] == "PreToolUse"
    assert rec["file"].endswith("app.py")
    assert "RuntimeError" in rec["error"]


def test_top_level_guard_fails_open(tmp_path, monkeypatch):
    from bully.cli import reasonix_hook as rh

    def boom(payload):
        raise RuntimeError("dispatch exploded")

    monkeypatch.setattr("sys.stdin", io.StringIO('{"event": "Stop", "cwd": "/nonexistent"}'))
    monkeypatch.setattr(rh, "handle_payload", boom)
    assert rh.run_reasonix_hook() == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_reasonix_hook.py -v`
Expected: `test_internal_error_fails_open_and_logs` FAILS (no `hook_fail_open` line; `IndexError` on `[-1]`), `test_top_level_guard_fails_open` FAILS (RuntimeError propagates).

- [ ] **Step 3: Implement.** In `src/bully/cli/reasonix_hook.py`:

Add imports:

```python
from datetime import datetime, timezone

from bully.state.telemetry import append_record, telemetry_path
```

Add the helper:

```python
def _log_fail_open(config: Path, event: str, file_path: str, exc: BaseException) -> None:
    """Best-effort record of a swallowed hook crash, so a systematically
    failing hook is visible to bully-review instead of silently passing."""
    try:
        append_record(
            telemetry_path(str(config)),
            {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "type": "hook_fail_open",
                "event": event,
                "file": file_path,
                "error": f"{type(exc).__name__}: {exc}"[:300],
            },
        )
    except Exception:  # noqa: BLE001 — telemetry must never break fail-open
        pass
```

In `_handle_pretooluse`, replace the generic except branch (and its `TODO(M3 ...)` comment):

```python
    except Exception as e:  # noqa: BLE001 — fail open: never block on an internal bug
        _log_fail_open(config, "PreToolUse", ev.file_path, e)
        return 0, ""
```

In `run_reasonix_hook`, wrap the dispatch:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_reasonix_hook.py -v && ruff check src/bully/cli/reasonix_hook.py`
Expected: ALL PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/bully/cli/reasonix_hook.py tests/test_reasonix_hook.py
git commit -m "M3 T4: fail-open hook_fail_open telemetry + top-level guard"
```

---

## Task 5: Session-scope the verdict cache

**Files:**
- Modify: `src/bully/state/verdict_cache.py`
- Test: `tests/test_verdict_cache.py` (extend)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_verdict_cache.py`:

```python
def test_session_init_resets_verdict_window(tmp_path):
    cfg = tmp_path / ".bully.yml"
    cfg.write_text("schema_version: 1\nrules: {}\n")
    log = telemetry_path(str(cfg))
    append_record(log, {"type": "semantic_verdict", "diff_id": "d1", "rule": "r1", "verdict": "pass"})
    append_record(log, {"type": "session_init"})
    # a stale pass from a previous session must not suppress a fresh eval
    assert cached_verdict(str(cfg), "d1", "r1") is None


def test_verdict_in_latest_session_window_wins(tmp_path):
    cfg = tmp_path / ".bully.yml"
    cfg.write_text("schema_version: 1\nrules: {}\n")
    log = telemetry_path(str(cfg))
    append_record(log, {"type": "session_init"})
    append_record(log, {"type": "semantic_verdict", "diff_id": "d1", "rule": "r1", "verdict": "violation"})
    append_record(log, {"type": "session_init"})
    append_record(log, {"type": "semantic_verdict", "diff_id": "d1", "rule": "r1", "verdict": "pass"})
    assert cached_verdict(str(cfg), "d1", "r1") == "pass"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_verdict_cache.py -v`
Expected: `test_session_init_resets_verdict_window` FAILS (`'pass' is not None`); the other new test passes incidentally (latest-wins) — that's fine, keep it as a regression guard.

- [ ] **Step 3: Implement.** In `src/bully/state/verdict_cache.py`, inside `cached_verdict`'s scan loop, insert before the `semantic_verdict` check:

```python
                if rec.get("type") == "session_init":
                    # New session window: verdicts logged before it no longer
                    # apply (a stale pass must not suppress a fresh eval).
                    result = None
                    continue
```

Update the module docstring's final paragraph (currently "Lookup is whole-log latest-wins. Session-scoping ... lands with the session work in M3.") to:

```
Lookup is latest-wins within the current session window: a `session_init`
record (stamped by the SessionStart hook) resets the window, so a stale
verdict from a previous session can't suppress a fresh evaluation. Logs with
no `session_init` anchor (headless runs, tests) fall back to whole-log
latest-wins.
```

- [ ] **Step 4: Run the tests** (M2's loop-break tests must stay green — they log verdicts with no `session_init`, covered by the fallback)

Run: `python3 -m pytest tests/test_verdict_cache.py tests/test_semantic_gate.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bully/state/verdict_cache.py tests/test_verdict_cache.py
git commit -m "M3 T5: session-scope the verdict cache on session_init"
```

---

## Task 6: Finalize — full suite, dogfood, status update

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Full verification**

Run: `python3 -m pytest -q && ruff check src tests`
Expected: all tests pass (32 from M1+M2 plus ~18 new), ruff clean.

- [ ] **Step 2: Dogfood the gate headlessly** (from the repo root):

```bash
D=$(mktemp -d) && printf 'schema_version: 1\nrules:\n  src-needs-tests:\n    description: "src changes need tests"\n    engine: session\n    severity: error\n    when:\n      changed_any: ["src/**"]\n    require:\n      changed_any: ["tests/**"]\n' > "$D/.bully.yml" && mkdir "$D/.bully" && printf '{"file": "src/x.py"}\n' > "$D/.bully/session.jsonl" && printf '{"event": "UserPromptSubmit", "cwd": "%s"}' "$D" | BULLY_TRUST_ALL=1 PYTHONPATH=src python3 -m bully reasonix-hook; echo "exit=$?"
```

Expected: stderr contains `AGENTIC LINT -- unsatisfied session rules gate this turn` and `src-needs-tests`; prints `exit=2`. Then the Stop path:

```bash
printf '{"event": "Stop", "cwd": "%s"}' "$D" | BULLY_TRUST_ALL=1 PYTHONPATH=src python3 -m bully reasonix-hook; echo "exit=$?"
```

Expected: the violation list on stderr; `exit=1`; `$D/.bully/session.jsonl` still exists.

- [ ] **Step 3: Update `CLAUDE.md`** — in the `## Status` section, replace the `Next:` bullet with:

```markdown
- **M3 done:** session rules — per-edit changed-set recording on the PreToolUse allow path, `Stop` notify (exit 1; Stop can't block), `UserPromptSubmit` exit-2 gate over the accumulated set, `SessionStart`/`SubagentStop` telemetry stamps, fail-open `hook_fail_open` records, and the verdict cache session-scoped on `session_init`.
- **Next:** M4 — skill ports (`bully`, `bully-init`, `bully-author`, `bully-review`, `bully-scheduler` as `runAs: subagent`), `doctor` rewrite for `.reasonix`, `reasonix.toml`, `REASONIX.md`.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "M3 T6: finalize session rules; update CLAUDE.md status"
```

---

## Self-review notes (spec coverage)

- §5c Stop record/notify → Tasks 1 (record) + 2/3 (notify). UserPromptSubmit exit-2 gate → Tasks 2/3. SubagentStop window marker → Task 3.
- §8 fail-open telemetry → Task 4. Trust gate retained in `evaluate_session` → Task 2. Latency: Stop/UserPromptSubmit handlers are file-reads only; timeouts set in Task 3's settings.json.
- Handoff item "session-scope the verdict cache" → Task 5 (anchored by Task 3's `SessionStart` stamping).
- Deliberately out of scope: bully's strict semantic Stop-gate (see Decisions #2), `bully ack` (no unadjudicated-at-Stop state exists to ack), skills/doctor/REASONIX.md (M4).
