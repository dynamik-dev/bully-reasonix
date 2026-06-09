# bully-reasonix — Milestone 2: Semantic Soft-Gate + Verdict Cache — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Port bully's signature *semantic* evaluation to Reasonix. Because only `PreToolUse` reaches the model (spec §5b), a semantic rule becomes a one-time **soft-gate**: the hook blocks the edit (exit 2) to request evaluation, the model evaluates via the `bully-evaluator` subagent skill and logs verdicts, then the re-issued identical edit is **allowed** via a session **verdict cache** keyed by a stable `diff_id`.

**Architecture:** Build on M1. When `run_pipeline` returns `status == "evaluate"` (semantic rules in scope, past the can't-match prefilters), the hook computes `diff_id = hash(file + normalized diff)` and consults the verdict cache (`semantic_verdict` records in `.bully/log.jsonl`): all-pass → allow; any cached violation → block; otherwise → block with the `SEMANTIC EVALUATION REQUIRED` payload + instructions. Verdicts are logged with `--log-verdict --diff-id`. The `bully-evaluator` agent is ported to a Reasonix `runAs: subagent` skill.

**Tech Stack:** Python ≥3.10 stdlib-only, pytest, ruff. Target: Reasonix Go line (local CLI `1.4.0-rc.1`).

**Spec:** `docs/superpowers/specs/2026-06-09-bully-reasonix-port-design.md` §5b. **Builds on:** M1 (`cli/reasonix_hook.py`, `harness/reasonix.py`, `diff/pending.py`, `content_path`).

---

## Reasonix facts (validated `v1.4.0` — do not re-derive)

- **Subagent skill frontmatter:** `runAs: subagent` (parser reads `fm["runas"]`; `internal/skill/skill.go:parseRunAs`). Optional `model:`, `effort:`, `allowed-tools:` (comma-sep; empty = full registry).
- **Invocation:** the model calls the `run_skill` tool with `{name, arguments}`. For a subagent skill, `arguments` is REQUIRED and *becomes the entire task* the subagent receives (no other context). The subagent returns only its final text.
- **Skill discovery** scans `.reasonix/skills/`, `.claude/skills/`, etc.; a `skills/<name>/SKILL.md` at repo root is picked up. Use `skills/bully-evaluator/SKILL.md`.

## Engine facts (from `../bully`, copied into M1 — do not re-derive)

- `run_pipeline(...)` returns `{"status": "evaluate", "file", "diff", "passed_checks", "evaluate": [{id, description, severity}], "_evaluator_input": "<SEMANTIC EVALUATION REQUIRED ...>", ["line_anchors"]}` when semantic rules dispatch. `_evaluator_input` is the ready-to-send TRUSTED_POLICY/UNTRUSTED_EVIDENCE string.
- `bully.state.telemetry.telemetry_path(config_path) -> .bully/log.jsonl` (provisions `.bully/`); `append_record(log_path, dict)` appends one JSONL line.
- `bully.cli.log_verdict.cmd_log_verdict(config_path, rule_id, verdict, file_path)` writes `{ts, type:"semantic_verdict", rule, verdict, [file]}`. We add `diff_id`.
- `../bully/agents/bully-evaluator.md` is the agent we port to a Reasonix subagent skill.

---

## File structure (M2)

- Create: `src/bully/state/verdict_cache.py` — `diff_id()`, `cached_verdict()`.
- Modify: `src/bully/cli/log_verdict.py` — add `diff_id` param → record.
- Modify: `src/bully/cli/args.py` — add `--diff-id`.
- Modify: `src/bully/cli/__init__.py` — pass `args.diff_id` to `cmd_log_verdict`.
- Modify: `src/bully/cli/reasonix_hook.py` — route `status=="evaluate"` to the soft-gate.
- Create: `skills/bully-evaluator/SKILL.md` — Reasonix `runAs: subagent` evaluator (port of the bully agent).
- Test: `tests/test_verdict_cache.py`, `tests/test_log_verdict.py`, `tests/test_semantic_gate.py`, `tests/test_bully_evaluator_skill.py`.

---

## Task 1: Verdict cache (`diff_id` + `cached_verdict`)

**Files:** Create `src/bully/state/verdict_cache.py`; Test `tests/test_verdict_cache.py`

- [ ] **Step 1: Write the failing tests** → `tests/test_verdict_cache.py`:

```python
# tests/test_verdict_cache.py
from bully.state.telemetry import append_record, telemetry_path
from bully.state.verdict_cache import cached_verdict, diff_id


def test_diff_id_stable_and_normalized():
    d1 = diff_id("/p/app.py", "+x = 1   \n+y = 2\n")   # trailing ws on line 1
    d2 = diff_id("/p/app.py", "+x = 1\n+y = 2\n")
    assert d1 == d2                      # trailing whitespace normalized away
    assert diff_id("/p/app.py", "+x = 1\n") != d1       # different content -> different id
    assert diff_id("/p/OTHER.py", "+x = 1\n+y = 2\n") != d1  # path participates


def test_cached_verdict_none_when_absent(tmp_path):
    (tmp_path / ".bully.yml").write_text("schema_version: 1\nrules: {}\n")
    assert cached_verdict(str(tmp_path / ".bully.yml"), "abc123", "rule-x") is None


def test_cached_verdict_returns_latest(tmp_path):
    cfg = tmp_path / ".bully.yml"
    cfg.write_text("schema_version: 1\nrules: {}\n")
    log = telemetry_path(str(cfg))
    append_record(log, {"type": "semantic_verdict", "diff_id": "d1", "rule": "r1", "verdict": "violation"})
    append_record(log, {"type": "semantic_verdict", "diff_id": "d1", "rule": "r1", "verdict": "pass"})
    append_record(log, {"type": "semantic_verdict", "diff_id": "d1", "rule": "r2", "verdict": "violation"})
    assert cached_verdict(str(cfg), "d1", "r1") == "pass"        # latest wins
    assert cached_verdict(str(cfg), "d1", "r2") == "violation"
    assert cached_verdict(str(cfg), "d2", "r1") is None          # diff_id must match
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_verdict_cache.py -q`
Expected: FAIL — `ModuleNotFoundError: bully.state.verdict_cache`.

- [ ] **Step 3: Implement** → `src/bully/state/verdict_cache.py`:

```python
# src/bully/state/verdict_cache.py
"""Verdict cache for the semantic soft-gate (M2).

The PreToolUse gate blocks an edit to *request* semantic evaluation, then must
let the model's re-issued identical edit through once a verdict is logged. We
key verdicts by a stable `diff_id` (hash of file path + normalized diff) so an
identical re-issued edit hits the cache and is allowed, while a *fixed* edit is
a new diff_id and gets evaluated fresh. Verdicts are `semantic_verdict` records
in .bully/log.jsonl carrying `diff_id`.

Lookup is whole-log latest-wins. Session-scoping (so a stale verdict can't
suppress a future session's eval) lands with the session work in M3.
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
                if (
                    rec.get("type") == "semantic_verdict"
                    and rec.get("diff_id") == did
                    and rec.get("rule") == rule
                ):
                    result = rec.get("verdict")  # keep scanning: latest wins
    except OSError:
        return None
    return result
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_verdict_cache.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/bully/state/verdict_cache.py tests/test_verdict_cache.py
git commit -m "M2 T1: verdict cache (diff_id + cached_verdict)"
```

---

## Task 2: `--log-verdict --diff-id`

**Files:** Modify `src/bully/cli/log_verdict.py`, `src/bully/cli/args.py`, `src/bully/cli/__init__.py`; Test `tests/test_log_verdict.py`

- [ ] **Step 1: Write the failing test** → `tests/test_log_verdict.py`:

```python
# tests/test_log_verdict.py
from bully.cli.log_verdict import cmd_log_verdict
from bully.state.verdict_cache import cached_verdict


def test_log_verdict_writes_diff_id_record(tmp_path):
    cfg = tmp_path / ".bully.yml"
    cfg.write_text("schema_version: 1\nrules: {}\n")
    rc = cmd_log_verdict(str(cfg), "r1", "pass", str(tmp_path / "app.py"), diff_id="deadbeef")
    assert rc == 0
    # the verdict cache can now find it by (diff_id, rule)
    assert cached_verdict(str(cfg), "deadbeef", "r1") == "pass"


def test_log_verdict_without_diff_id_still_works(tmp_path):
    cfg = tmp_path / ".bully.yml"
    cfg.write_text("schema_version: 1\nrules: {}\n")
    assert cmd_log_verdict(str(cfg), "r1", "violation", None) == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_log_verdict.py -q`
Expected: FAIL — `cmd_log_verdict() got an unexpected keyword argument 'diff_id'`.

- [ ] **Step 3: Add `diff_id` to `cmd_log_verdict`** in `src/bully/cli/log_verdict.py`. Change the signature and add the field:

```python
def cmd_log_verdict(
    config_path: str | None,
    rule_id: str,
    verdict: str,
    file_path: str | None,
    diff_id: str | None = None,
) -> int:
    path = config_path or ".bully.yml"
    log_path = telemetry_path(path)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "type": "semantic_verdict",
        "rule": rule_id,
        "verdict": verdict,
    }
    if file_path:
        record["file"] = file_path
    if diff_id:
        record["diff_id"] = diff_id
    append_record(log_path, record)
    return 0
```

- [ ] **Step 4: Add the `--diff-id` flag** in `src/bully/cli/args.py`. Find the `--verdict` argument (`parser.add_argument("--verdict", choices=("pass", "violation"), default=None)`) and add right after it:

```python
    parser.add_argument("--diff-id", dest="diff_id", default=None,
                        help="With --log-verdict: the semantic soft-gate diff id to key this verdict to.")
```

- [ ] **Step 5: Thread it in the dispatcher.** In `src/bully/cli/__init__.py`, the `--log-verdict` branch calls `cmd_log_verdict(args.config, rule_id, args.verdict, args.file_path)`. Add the new arg:

```python
        sys.exit(cmd_log_verdict(args.config, rule_id, args.verdict, args.file_path, args.diff_id))
```

- [ ] **Step 6: Run to verify it passes + the CLI flag works end-to-end**

Run: `python3 -m pytest tests/test_log_verdict.py -q`
Expected: PASS (2 passed).

Run (CLI smoke):
```bash
TMP=$(mktemp -d); printf 'schema_version: 1\nrules: {}\n' > "$TMP/.bully.yml"
BULLY_TRUST_ALL=1 PYTHONPATH=src python3 -m bully --log-verdict --config "$TMP/.bully.yml" --rule r1 --verdict pass --diff-id abc123 --file "$TMP/x.py"; echo "exit=$?"
grep -o '"diff_id": "abc123"' "$TMP/.bully/log.jsonl"
```
Expected: `exit=0` and the grep prints `"diff_id": "abc123"`.

- [ ] **Step 7: Commit**

```bash
git add src/bully/cli/log_verdict.py src/bully/cli/args.py src/bully/cli/__init__.py tests/test_log_verdict.py
git commit -m "M2 T2: --log-verdict --diff-id"
```

---

## Task 3: The semantic soft-gate in `reasonix_hook.py`

**Files:** Modify `src/bully/cli/reasonix_hook.py`; Test `tests/test_semantic_gate.py`

**What changes:** M1's `_render` returns `(0, "")` for `status == "evaluate"` (semantic deferred). Now route it to a real gate.

- [ ] **Step 1: Write the failing tests** → `tests/test_semantic_gate.py`:

```python
# tests/test_semantic_gate.py
import re
import textwrap

from bully.cli.log_verdict import cmd_log_verdict
from bully.cli.reasonix_hook import handle_payload


def _proj(tmp_path):
    # a SEMANTIC rule; an edit adding a real code line dispatches it (passes can't-match)
    (tmp_path / ".bully.yml").write_text(textwrap.dedent("""\
        schema_version: 1
        rules:
          no-bare-except:
            description: "Avoid bare 'except:'; catch specific exceptions."
            engine: semantic
            scope: ["*.py"]
            severity: error
    """))
    f = tmp_path / "app.py"
    f.write_text("def f():\n    return 1\n")
    return tmp_path, f


def _edit(proj):
    return {
        "event": "PreToolUse", "cwd": str(proj), "toolName": "edit_file",
        "toolArgs": {"path": "app.py", "old_string": "    return 1",
                     "new_string": "    try:\n        return 1\n    except:\n        pass"},
    }


def test_semantic_no_verdict_requests_eval(tmp_path):
    proj, _ = _proj(tmp_path)
    code, msg = handle_payload(_edit(proj))
    assert code == 2
    assert "SEMANTIC EVALUATION REQUIRED" in msg          # the evaluator payload is included
    assert "no-bare-except" in msg
    assert "run_skill" in msg and "bully-evaluator" in msg  # how to evaluate
    assert "--log-verdict" in msg and "--diff-id" in msg    # how to record


def test_semantic_loop_breaks_after_pass_verdict(tmp_path):
    proj, _ = _proj(tmp_path)
    code, msg = handle_payload(_edit(proj))
    assert code == 2
    did = re.search(r"--diff-id (\w+)", msg).group(1)
    # model evaluates -> all clean -> logs pass for the rule
    cmd_log_verdict(str(proj / ".bully.yml"), "no-bare-except", "pass", str(proj / "app.py"), diff_id=did)
    # re-issued identical edit is now allowed
    assert handle_payload(_edit(proj)) == (0, "")


def test_semantic_cached_violation_blocks(tmp_path):
    proj, _ = _proj(tmp_path)
    code, msg = handle_payload(_edit(proj))
    did = re.search(r"--diff-id (\w+)", msg).group(1)
    cmd_log_verdict(str(proj / ".bully.yml"), "no-bare-except", "violation", str(proj / "app.py"), diff_id=did)
    code2, msg2 = handle_payload(_edit(proj))
    assert code2 == 2
    assert "prior verdict" in msg2 and "no-bare-except" in msg2
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_semantic_gate.py -q`
Expected: FAIL — `test_semantic_no_verdict_requests_eval` gets `code == 0` (M1 deferral) so the assertions fail.

> If instead the rule never dispatches (status stays `pass`, code 0 with no payload), the can't-match prefilter skipped it — confirm the edit adds a non-comment code line (`except:`), and report. Do not weaken the gate to make a mis-dispatched test pass.

- [ ] **Step 3: Implement the gate** in `src/bully/cli/reasonix_hook.py`.

Add this import alongside the other `bully.` imports near the top:
```python
from bully.state.verdict_cache import cached_verdict, diff_id
```

Add two helpers above `handle_payload`:
```python
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
        return 2, "AGENTIC LINT -- blocked (semantic, prior verdict). Fix before proceeding:\n" + body + "\n"

    if evaluate and all(cached.get(r["id"]) == "pass" for r in evaluate):
        return 0, ""  # this exact edit was already evaluated clean -> allow

    return 2, _semantic_request_msg(result, did)
```

Route `evaluate` to the gate in `_render` (replace the M1 comment-only fall-through). `_render` becomes:
```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_semantic_gate.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Full-suite regression**

Run: `python3 -m pytest -q`
Expected: all prior tests still pass (M1's 22 + the new M2 tests). Paste the count.

- [ ] **Step 6: Commit**

```bash
git add src/bully/cli/reasonix_hook.py tests/test_semantic_gate.py
git commit -m "M2 T3: semantic soft-gate with verdict-cache loop break"
```

---

## Task 4: `bully-evaluator` Reasonix subagent skill (copy + transform)

**Files:** Create `skills/bully-evaluator/SKILL.md`; Test `tests/test_bully_evaluator_skill.py`

This is a **port of an existing file**, not new prose. Copy `../bully/agents/bully-evaluator.md` and apply a few harness edits. The body (including its `VIOLATIONS / NO_VIOLATIONS` fenced block) is preserved verbatim — do not retype it.

- [ ] **Step 1: Write the failing test** → `tests/test_bully_evaluator_skill.py`:

```python
# tests/test_bully_evaluator_skill.py
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills" / "bully-evaluator" / "SKILL.md"


def _frontmatter(text):
    assert text.startswith("---\n")
    fm = text.split("---\n", 2)[1]
    return {
        k.strip().lower(): v.strip()
        for k, v in (line.split(":", 1) for line in fm.splitlines() if ":" in line)
    }


def test_skill_file_exists_and_is_a_subagent():
    assert SKILL.is_file()
    fm = _frontmatter(SKILL.read_text())
    assert fm.get("name") == "bully-evaluator"
    assert fm.get("runas") == "subagent"          # frontmatter `runAs:` -> key `runas`
    assert fm.get("description")


def test_skill_body_defines_the_output_contract():
    body = SKILL.read_text().split("---\n", 2)[2]
    assert "TRUSTED_POLICY" in body and "UNTRUSTED_EVIDENCE" in body
    assert "VIOLATIONS:" in body and "NO_VIOLATIONS:" in body
    # no leftover Claude-isms
    assert "PostToolUse" not in body and "subagent_type" not in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_bully_evaluator_skill.py -q`
Expected: FAIL — `assert SKILL.is_file()` (file absent).

- [ ] **Step 3: Create the skill by copying + transforming the bully agent.**

```bash
mkdir -p skills/bully-evaluator
cp ../bully/agents/bully-evaluator.md skills/bully-evaluator/SKILL.md
```

Now edit `skills/bully-evaluator/SKILL.md`:

1. **Replace the entire frontmatter block** (everything between the first two `---` lines) with exactly:
```yaml
name: bully-evaluator
description: Evaluates a single bully semantic-evaluation payload against a diff and returns a structured violation list. Invoked by the bully-reasonix PreToolUse soft-gate when a SEMANTIC EVALUATION REQUIRED payload is raised. Read-only — returns violations as text so the parent applies the fixes.
runAs: subagent
```
(drops the Claude-only `model: sonnet`, `tools:`, `color:` lines; a Reasonix subagent skill inherits the executor model — override via `reasonix.toml` `subagent_models = { "bully-evaluator" = "..." }` if desired.)

2. **In the body, first sentence:** change `The parent harness sends you a payload that has two clearly labeled regions:` → `Your ` + "`arguments`" + ` are a payload with two clearly labeled regions:`

3. **The tools sentence:** change `Do not request additional context — there is no mechanism to provide it. You have no ` + "`Read`, `Grep`, or `Glob`" + ` tools.` → `Do not request additional context and do not read files — there is no mechanism to provide more, and all context you need is already in the payload.`

Leave everything else (the TRUSTED_POLICY/UNTRUSTED_EVIDENCE explanation, the line-anchor note, and the `VIOLATIONS: / NO_VIOLATIONS:` fenced contract) **unchanged**.

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_bully_evaluator_skill.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Best-effort confirm Reasonix loads it** (don't fail the task if there's no non-interactive lister):

Run: `reasonix --help 2>&1 | grep -i skill || echo "(no skill subcommand in --help)"`. If a non-interactive skills listing exists, capture whether `bully-evaluator` appears. Otherwise rely on the frontmatter test and note it.

- [ ] **Step 6: Commit**

```bash
git add skills/bully-evaluator/SKILL.md tests/test_bully_evaluator_skill.py
git commit -m "M2 T4: bully-evaluator Reasonix subagent skill"
```

---

## Task 5: End-to-end loop dogfood + finalize M2

**Files:** Modify `CLAUDE.md`; end-to-end CLI verification (no source change beyond docs)

- [ ] **Step 1: Full suite + ruff**

Run: `python3 -m pytest -q` → all pass (paste count).
Run: `ruff check src tests` → `All checks passed!` (fix any new findings).

- [ ] **Step 2: Dogfood the full soft-gate loop via the CLI** (block → log pass → allow):

```bash
cd /Users/chrisarter/Documents/projects/bully-reasonix
TMP=$(mktemp -d)
printf 'schema_version: 1\nrules:\n  no-bare-except:\n    description: "Avoid bare except."\n    engine: semantic\n    scope: ["*.py"]\n    severity: error\n' > "$TMP/.bully.yml"
printf 'def f():\n    return 1\n' > "$TMP/app.py"
PAY='{"event":"PreToolUse","cwd":"'"$TMP"'","toolName":"edit_file","toolArgs":{"path":"app.py","old_string":"    return 1","new_string":"    try:\n        return 1\n    except:\n        pass"}}'
echo "--- first run: expect exit 2 + SEMANTIC EVALUATION REQUIRED ---"
OUT=$(printf '%s' "$PAY" | BULLY_TRUST_ALL=1 PYTHONPATH=src python3 -m bully reasonix-hook 2>&1); echo "exit=$?"; echo "$OUT" | head -4
DID=$(echo "$OUT" | grep -o -- '--diff-id [0-9a-f]*' | head -1 | awk '{print $2}')
echo "diff_id=$DID"
echo "--- log a pass verdict for that diff_id ---"
BULLY_TRUST_ALL=1 PYTHONPATH=src python3 -m bully --log-verdict --config "$TMP/.bully.yml" --rule no-bare-except --verdict pass --diff-id "$DID" --file "$TMP/app.py"; echo "logged exit=$?"
echo "--- second run (identical edit): expect exit 0, silent ---"
printf '%s' "$PAY" | BULLY_TRUST_ALL=1 PYTHONPATH=src python3 -m bully reasonix-hook; echo "exit=$?"
rm -rf "$TMP"
```
Expected: first run `exit=2` with the payload; `diff_id` extracted; verdict logged `exit=0`; second run `exit=0` (loop broken). Paste real output.

- [ ] **Step 3: Update `CLAUDE.md` Status** — replace the two M1 bullets with:

```markdown
- **M1+M2 done:** deterministic `PreToolUse` block + **semantic soft-gate**. Semantic rules block once (exit 2) with a `SEMANTIC EVALUATION REQUIRED` payload; the model evaluates via the `bully-evaluator` subagent skill and logs verdicts (`--log-verdict --diff-id`); the re-issued clean edit is allowed via the session verdict cache (`state/verdict_cache.py`).
- **Next:** M3 session rules (`Stop` record/notify + `UserPromptSubmit` gate) + fail-open telemetry.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "M2 T5: dogfood the soft-gate loop, finalize M2"
```

---

## Done-when (M2 acceptance)

- A `PreToolUse` edit that dispatches a semantic rule with **no cached verdict** → exit 2; stderr carries `SEMANTIC EVALUATION REQUIRED`, the `_evaluator_input` payload, the `diff_id`, the `run_skill bully-evaluator` instruction, and the `--log-verdict --diff-id` command.
- After logging `pass` for every evaluated rule at that `diff_id`, the **identical** re-issued edit → exit 0 (loop broken).
- A logged `violation` at that `diff_id` → exit 2 ("prior verdict").
- `--log-verdict --diff-id` writes a `semantic_verdict` record the cache reads back.
- `skills/bully-evaluator/SKILL.md` parses as a `runAs: subagent` skill and defines the VIOLATIONS/NO_VIOLATIONS contract.
- Full suite green; ruff clean; M1 behavior unchanged (deterministic block still exits 2; clean edit still exits 0).

## Deferred (NOT in M2)

- Session-scoping of the verdict cache (currently whole-log latest-wins) → **M3** with session handling.
- `Stop`/`UserPromptSubmit`/`SessionStart`/`SubagentStop`, fail-open telemetry (`TODO(M3)`) → **M3**.
- The 4 user skills, `bully-scheduler`, `doctor` rewrite, `reasonix.toml`, `REASONIX.md`, full bully test port, dropping dormant Claude files → **M4**.
