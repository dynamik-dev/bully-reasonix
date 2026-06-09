# bully-reasonix — Milestone 4: Skill Ports + Doctor + Product Files — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the user-facing layer of the port: the 4 inline skills (`bully`, `bully-init`, `bully-author`, `bully-review`), the `bully-scheduler` subagent skill, a `doctor` rewritten for Reasonix wiring + skill discovery, `reasonix.toml`, `REASONIX.md`, and the docs/examples the skills reference.

**Architecture:** The engine is untouched (M1–M3 invariant). `bully` and `bully-scheduler` are **rewrites** (their flows changed: PreToolUse soft-gate loop replaces the PostToolUse/Stop-gate flow; agent file becomes a `runAs: subagent` skill). `bully-init`/`bully-author`/`bully-review` are **copy + enumerated-edit ports** (the engine CLI they drive is identical). `doctor` swaps its Claude-specific checks (`.claude/settings.json` PostToolUse, plugin cache) for Reasonix ones (`.reasonix/settings.json` events, convention-dir + `reasonix.toml` skill discovery).

**Tech Stack:** Python ≥3.10 stdlib-only, pytest, ruff. Target: Reasonix Go line (local CLI `1.4.0-rc.1`, contracts validated `v1.4.0`).

**Spec:** `docs/superpowers/specs/2026-06-09-bully-reasonix-port-design.md` §6 (reuse-map rows for skills/agents/doctor), §7 (layout). **Builds on:** M2 (soft-gate messages, `bully-evaluator` skill, `--log-verdict --diff-id`), M3 (session gate, `hook_fail_open`/`session_init`/`subagent_stop` records).

---

## Reasonix facts (validated `v1.4.0` source — do not re-derive)

- **Skill discovery roots**, priority order (`internal/skill/skill.go` `roots()`): `<project>/{.reasonix,.agents,.agent,.claude}/skills/` → `reasonix.toml` `[skills] paths` entries **used AS-IS** (no `skills/` suffix appended; `~` expands; relative paths resolve against the project root) → `~/{.reasonix,.agents,.agent,.claude}/skills/`.
- **Skill file shapes:** `<root>/<name>/SKILL.md` or `<root>/<name>.md`. A directory without a `SKILL.md` is not a skill.
- **Frontmatter keys:** `name`, `description`, `allowed-tools` (comma-separated literal Reasonix tool names — `bash, read_file, edit_file, write_file, grep, glob`, …), `model`, `effort`, `runAs: inline | subagent` (default inline). No `tools:`/`color:`/`metadata:` semantics (extra keys are ignored, harmless).
- **`reasonix.toml` schema** (`internal/config/config.go`): `[skills]` has `paths`, `excluded_paths`, `disabled_skills`, `max_depth`. Per-skill subagent model routing is **`[agent]` `subagent_models = { "<skill>" = "<model-ref>" }`** (plus `subagent_model` default) — *not* under `[skills]`.
- **Subagent skills** run an isolated child loop; **only the final answer text returns to the parent**. Invoked via `run_skill` (or `task`), or `/<name>` by the user.
- **Hook matcher** is auto-anchored: `^(?:<match>)$` on `toolName`; a missing `match` key matches every tool. Settings shape: `{ "hooks": { "<Event>": [ { "match"?, "command", "timeout"?, … } ] } }` in `<root>/.reasonix/settings.json` (project, trusted-only) and `~/.reasonix/settings.json`.
- Only `PreToolUse`/`UserPromptSubmit` block (exit 2 → message fed to the **model**); other events' nonzero exits are `notify` (user-facing).

## Engine facts (already in this repo — do not re-derive)

- **Analyzer** is `src/bully/semantic/analyzer.py` with an argparse `main()` → invoke as `python3 -m bully.semantic.analyzer --log .bully/log.jsonl --config .bully.yml [--json]`. **`--config` is required** (bully's old skill text said `python3 pipeline/analyzer.py` with optional config — both are wrong here).
- **M2 soft-gate messages** (`cli/reasonix_hook.py`) the `bully` skill must mirror *exactly*:
  - deterministic block: `AGENTIC LINT -- blocked. Fix these before proceeding:` (+ optional `Passed checks: …` — `runtime/hook_io.format_blocked_stderr`);
  - semantic request: `AGENTIC LINT SEMANTIC EVALUATION REQUIRED (edit paused).` then `Evaluate these rules against the diff: <ids>`, the `run_skill(name="bully-evaluator", arguments=<payload>)` instruction, the verdict command `python3 -m bully --log-verdict --diff-id <did> --rule <id> --verdict <pass|violation> --file <path>`, the re-apply instruction, then the raw `_evaluator_input` payload text (`<TRUSTED_POLICY>`/`<UNTRUSTED_EVIDENCE>`);
  - re-issued bad edit: `AGENTIC LINT -- blocked (semantic, prior verdict). Fix before proceeding:`;
  - warnings (exit 1) are **user-facing only** (notify) — the model never sees them;
  - session gate (`cli/stop.py reasonix_prompt_gate`): `AGENTIC LINT -- unsatisfied session rules gate this turn:` + `Make the required companion change(s), then continue with the user's request.`;
  - Stop notify (user-facing): `bully session check failed:`.
- **Verdict-cache semantics:** the re-applied *identical* edit passes only when **every** dispatched rule has a logged `pass` for that exact `diff_id`; a logged `violation` re-blocks the identical edit; a *changed* edit is a new `diff_id`, evaluated fresh.
- **Telemetry record types** in `.bully/log.jsonl`: per-edit records, `semantic_verdict`, `semantic_skipped` (M2), `session_init`, `subagent_stop`, `hook_fail_open` (M3). The analyzer consumes the first three; M4's consumer for the last three is the `bully-review` skill (grep), per the M3 close-out decision.
- `tests/conftest.py` sets `BULLY_TRUST_ALL=1` → `trust_status` returns `("trusted", "env:BULLY_TRUST_ALL")` in tests.
- `cmd_doctor()` is called zero-arg from `cli/__init__.py` — the rewrite keeps that call site working via a defaulted parameter.
- `skills/bully-evaluator/SKILL.md` + `tests/test_bully_evaluator_skill.py` (M2) are the established skill-port pattern: minimal frontmatter, content assertions, Claude-ism bans.

## Decisions (made here — record, don't re-litigate)

1. **`bully` and `bully-scheduler` are full rewrites; `bully-init`/`bully-author`/`bully-review` are copy + enumerated edits.** The first two changed shape (soft-gate loop; agent→subagent-skill); the other three only need harness-edge rewording.
2. **No Stop-gate / `bully ack` content anywhere.** M3 decision: under the soft-gate an edit cannot land unadjudicated, so the state is unreachable; the `ack` verb was never ported. The test suite bans the string `bully ack`.
3. **Binary resolution in skills:** `BULLY=$(command -v bully 2>/dev/null || echo "python3 -m bully")`, used **unquoted** ($BULLY may be multi-word). No plugin-cache scans, no `PYTHONPATH` fallbacks beyond a one-line `pip install -e` hint.
4. **Analyzer invocation:** module form `python3 -m bully.semantic.analyzer` everywhere (skills + telemetry doc). The string `pipeline/analyzer.py` is banned by test.
5. **`hook_fail_open`/`session_init`/`subagent_stop` get their consumer as a "Hook health" step in `bully-review`** (grep one-liners). The analyzer stays verbatim-from-bully — no engine divergence for a count grep can produce.
6. **Doctor severities:** PreToolUse hook missing or matcher not covering all three edit tools → **FAIL**. `Stop`/`UserPromptSubmit` missing → **FAIL iff the config has `engine: session` rules**, else WARN. `SessionStart`/`SubagentStop` missing → WARN. Skill `bully-evaluator` missing → **FAIL** (the soft-gate dispatches it); the five companion skills → WARN (block messages are self-describing; the system degrades, not breaks).
7. **Doctor parses `reasonix.toml` with `tomllib` when available, a minimal regex fallback on 3.10** (requires-python is ≥3.10; tomllib is 3.11+). Diagnostic-grade extraction is acceptable — a parse miss costs a WARN, never a crash.
8. **Port `docs/rule-authoring.md`, `docs/telemetry.md`, and `examples/rules/*.yml` from `../bully`** — the skills reference all three; examples are Claude-ism-free already, the docs need 4 line-level rewords + 2 new record-type sections.
9. **`bully-scheduler` drops its `model: sonnet` frontmatter** (Anthropic-specific). Routing goes via `reasonix.toml` `[agent] subagent_models` (shipped commented-out — model refs are provider-specific). `tools: Bash, Read, Edit, Write` → `allowed-tools: bash, read_file, edit_file, write_file, grep, glob`.
10. **`REASONIX.md` is short standing instructions** (Reasonix doesn't auto-load `CLAUDE.md`; exit-0 hook output is silent, so this file is the only always-loaded surface). It includes the session-gate escape: *make the companion change, or fix the rule via `bully-author`*.
11. **`bully-init` gains a wiring step** (replacing the plugin-install false-positives list): offer to write the `.reasonix/settings.json` hooks block and the `reasonix.toml` `[skills] paths` entry when doctor FAILs on them — there is no plugin system to do it for us.

## File structure (M4)

- Create: `skills/bully/SKILL.md` (rewrite), `skills/bully-scheduler/SKILL.md` (rewrite), `skills/bully-init/SKILL.md`, `skills/bully-author/SKILL.md`, `skills/bully-review/SKILL.md` (copy+edit ports)
- Create: `docs/rule-authoring.md`, `docs/telemetry.md` (copy+edit), `examples/rules/*.yml` (copy verbatim)
- Create: `reasonix.toml`, `REASONIX.md`
- Rewrite: `src/bully/cli/doctor.py`
- Modify: `CLAUDE.md` (status)
- Test: `tests/test_skills_port.py` (new, grows across Tasks 1–6 and 8), `tests/test_doctor.py` (new)

---

## Task 1: Port the docs and examples catalog the skills reference

**Files:**
- Create: `docs/rule-authoring.md`, `docs/telemetry.md`, `examples/rules/*.yml`
- Test: `tests/test_skills_port.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_skills_port.py`:

```python
# tests/test_skills_port.py
"""M4: ported skills, docs, and config artifacts -- Reasonix-native, no Claude-isms."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CLAUDE_ISMS = (
    "PostToolUse",
    "subagent_type",
    "plugins/cache",
    "CLAUDE_PLUGIN_ROOT",
    "additionalContext",
    "hooks.json",
    "pipeline/analyzer.py",
    "bully ack",
)


def _read(rel):
    return (ROOT / rel).read_text()


def _frontmatter(text):
    assert text.startswith("---\n")
    fm = text.split("---\n", 2)[1]
    return {
        k.strip().lower(): v.strip()
        for k, v in (line.split(":", 1) for line in fm.splitlines() if ":" in line)
    }


def _assert_no_claude_isms(text, where):
    for ism in CLAUDE_ISMS:
        assert ism not in text, f"{ism!r} leaked into {where}"


def test_ported_docs_exist_and_are_reasonix_native():
    for rel in ("docs/rule-authoring.md", "docs/telemetry.md"):
        text = _read(rel)
        _assert_no_claude_isms(text, rel)
        assert "Claude Code" not in text, rel
        assert "hook.sh" not in text, rel
    telemetry = _read("docs/telemetry.md")
    assert "python3 -m bully.semantic.analyzer" in telemetry
    assert "hook_fail_open" in telemetry
    assert "subagent_stop" in telemetry


def test_examples_catalog_ported():
    packs = sorted(p.name for p in (ROOT / "examples" / "rules").glob("*.yml"))
    assert packs == [
        "django.yml", "fastapi.yml", "go.yml", "nextjs.yml",
        "rails.yml", "react-ts.yml", "rust-cli.yml",
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_skills_port.py -q`
Expected: FAIL (`FileNotFoundError` on `docs/rule-authoring.md`)

- [ ] **Step 3: Copy the sources**

```bash
mkdir -p examples/rules
cp ../bully/examples/rules/*.yml examples/rules/
cp ../bully/docs/rule-authoring.md ../bully/docs/telemetry.md docs/
```

- [ ] **Step 4: Apply the known Claude-ism edits**

`docs/rule-authoring.md` — three Edits:

old:
```
Bully is the cop; native linters (ruff, biome, eslint, tsc, phpstan, rubocop, clippy, …) are the lawmakers. The PostToolUse hook runs on every Edit/Write regardless; the routing question is just *where a rule's definition lives*.
```
new:
```
Bully is the cop; native linters (ruff, biome, eslint, tsc, phpstan, rubocop, clippy, …) are the lawmakers. The PreToolUse hook runs on every pending edit regardless; the routing question is just *where a rule's definition lives*.
```

old:
```
`hook.sh` calls `--validate` once per session, so a malformed config surfaces on the first edit rather than silently dropping rules across hundreds of edits.
```
new:
```
Run `--validate` after any hand-edit of `.bully.yml` so a malformed config surfaces immediately rather than silently dropping rules across hundreds of edits.
```

old:
```
`bully lint` defaults to *advisory* posture: untrusted configs exit 0 so the PostToolUse hook doesn't block edits on infra issues. CI callers that parse exit codes should pass `--strict`:
```
new:
```
`bully lint` defaults to *advisory* posture: untrusted configs exit 0 so the PreToolUse hook doesn't block edits on infra issues. CI callers that parse exit codes should pass `--strict`:
```

`docs/telemetry.md` — two Edits:

old:
```
Every pipeline run appends a record to `.bully/log.jsonl`. The `bully-review` skill reads that log and classifies rule health so the config can evolve with the codebase. The session-aware Stop gate reads the same log to decide what to block.
```
new:
```
Every pipeline run appends a record to `.bully/log.jsonl`. The `bully-review` skill reads that log and classifies rule health so the config can evolve with the codebase. The semantic soft-gate's verdict cache reads the same log to decide which pending edits to admit.
```

old:
```
`SessionStart` writes one stamp per Claude Code session:
```
new:
```
`SessionStart` writes one stamp per Reasonix session:
```

- [ ] **Step 5: Sweep the remainder**

Replace **every** occurrence of `python3 pipeline/analyzer.py` in `docs/telemetry.md` with `python3 -m bully.semantic.analyzer` (keep each command's flags as-is — the "Running the analyzer" section has several). Then verify nothing is left:

```bash
grep -inE 'claude|posttooluse|hook\.sh|plugins/cache|pipeline/analyzer|subagent_type|additionalcontext|stop gate' docs/rule-authoring.md docs/telemetry.md
```

Expected: no output. If `stop gate` (or similar prose) still matches, reword that sentence to refer to the soft-gate / UserPromptSubmit session gate as appropriate — the gate moved in this port.

- [ ] **Step 6: Append the M3 record types to `docs/telemetry.md`**

Add after the "## Session init records" section:

```markdown
## Hook fail-open records

Any internal hook crash is swallowed (the hook must never block an edit on its own bug) and recorded:

```json
{"ts": "2026-06-09T12:00:00Z", "type": "hook_fail_open", "event": "PreToolUse", "file": "src/x.py", "error": "ValueError: ..."}
```

`bully-review` surfaces these: edits made while the hook was failing went **unlinted**, and a repeating `error` string is a bug to fix before any rule tuning.

## Subagent stop records

`SubagentStop` appends `{"type": "subagent_stop", "ts": "..."}` — a completion marker for evaluator dispatches, used to cross-check `evaluate_requested` activity.
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_skills_port.py -q`
Expected: 2 passed

- [ ] **Step 8: Commit**

```bash
ruff check src tests && python3 -m pytest -q
git add docs/rule-authoring.md docs/telemetry.md examples tests/test_skills_port.py
git commit -m "M4 T1: port rule-authoring/telemetry docs + examples catalog"
```

---

## Task 2: `skills/bully/SKILL.md` — the hook-output interpreter (rewrite)

**Files:**
- Create: `skills/bully/SKILL.md`
- Test: `tests/test_skills_port.py` (append)

- [ ] **Step 1: Append the failing test**

```python
def test_bully_skill_runs_the_soft_gate_loop():
    text = _read("skills/bully/SKILL.md")
    fm = _frontmatter(text)
    assert fm.get("name") == "bully"
    assert "runas" not in fm  # inline skill
    body = text.split("---\n", 2)[2]
    _assert_no_claude_isms(text, "skills/bully")
    assert "AGENTIC LINT -- blocked. Fix these before proceeding:" in body
    assert "AGENTIC LINT SEMANTIC EVALUATION REQUIRED" in body
    assert 'run_skill(name="bully-evaluator"' in body
    assert "--log-verdict --diff-id" in body
    assert "prior verdict" in body
    assert "AGENTIC LINT -- unsatisfied session rules gate this turn" in body
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_skills_port.py::test_bully_skill_runs_the_soft_gate_loop -q`
Expected: FAIL (file not found)

- [ ] **Step 3: Write the skill**

Create `skills/bully/SKILL.md` with exactly this content:

````markdown
---
name: bully
description: Interprets bully hook output in Reasonix -- re-issues edits blocked by deterministic rules, runs the semantic soft-gate loop (dispatch bully-evaluator, log verdicts, re-apply), and resolves the session-rule prompt gate. Use whenever a tool result or prompt gate begins with "AGENTIC LINT".
---

# Agentic Lint

Interpret and act on bully's hook output. Not user-invocable. Bully runs as a `PreToolUse`
hook on every pending edit (`edit_file`/`write_file`/`multi_edit`): when it exits 2, the
edit is **rejected before touching the file** and the message below is fed back to you.
The file on disk is unchanged in every blocked case -- "fix" always means *issue a
corrected edit*, never *repair a file the bad edit damaged*.

## When an edit is blocked (deterministic rules)

Message format:

```
AGENTIC LINT -- blocked. Fix these before proceeding:

- [no-compact] line 42: return compact('result');
- [no-db-facade] line 58: $users = DB::table('users')->get();

Passed checks: rule-a, rule-b
```

Re-issue the edit with every listed violation fixed before any other tool call. The hook
re-fires on the new attempt and re-checks. Repeat until clear. Line numbers refer to the
file as it would have looked *after* your rejected edit.

## When an edit pauses for semantic evaluation

Message begins:

```
AGENTIC LINT SEMANTIC EVALUATION REQUIRED (edit paused).
```

and carries the rule ids to evaluate, a `--diff-id` value, follow-up instructions, and the
full evaluator payload (a text block with `<TRUSTED_POLICY>` and `<UNTRUSTED_EVIDENCE>`
regions). The edit is paused, not judged -- nothing landed. Run this loop:

### 1. Evaluate

Dispatch the evaluator subagent:

```
run_skill(name="bully-evaluator", arguments=<the payload text from the message, verbatim>)
```

Pass the payload exactly as it appears -- it is already a formatted string with trust
boundaries; do NOT re-serialize it as JSON. Judge inline only when it is a single rule
over a short diff; for anything more, use the subagent -- it is an independent context
that did not write the edit, and inline self-evaluation is the author grading its own
homework.

The evaluator returns:

```
VIOLATIONS:
- [rule-id] line N: <what's wrong>
  fix: <suggestion>

NO_VIOLATIONS:
- rule-id-a
```

If the response is malformed, re-dispatch once. If it is still malformed, judge inline
against the diff as a liveness fallback and continue the loop -- still log the verdicts.

### 2. Log one verdict per rule -- load-bearing

For **every** rule id listed in the message, run once, with the exact `--diff-id` the
message gave:

```
python3 -m bully --log-verdict --diff-id <id> --rule <rule-id> --verdict <pass|violation> --file <file-path>
```

`violation` if the rule appears under `VIOLATIONS:`, `pass` if under `NO_VIOLATIONS:`.
This is not optional bookkeeping: the hook admits the re-applied edit **only when every
dispatched rule has a logged `pass` for that exact diff**. An unlogged rule re-triggers
the gate; a logged `violation` blocks the identical edit outright.

### 3. Re-apply

- **All rules pass** → re-apply the *identical* edit (same tool, same content). Identical
  content hashes to the same diff id, hits the cached passes, and is allowed through.
- **Any error-severity violation** → fix it (the evaluator's `fix:` is a starting point)
  and apply the corrected edit instead. A changed edit is a new diff and is evaluated
  fresh. Severities are listed per rule in the payload's `<TRUSTED_POLICY>` block.
- **Warning-severity violations** → note in one sentence, then re-apply with or without
  the fix at your judgment; warnings never block.

## When a prior verdict blocks

Message begins `AGENTIC LINT -- blocked (semantic, prior verdict). Fix before proceeding:`.
You re-issued an edit already judged in violation this session. Do not re-apply it
unchanged and do not re-log verdicts for the same diff: fix the violation and apply the
corrected edit.

## When a new turn is gated (session rules)

At prompt submission the message begins:

```
AGENTIC LINT -- unsatisfied session rules gate this turn:
- [error] changelog-updated: src changes need a changelog entry.
```

A session rule ties changes together across a turn (the previous turn's Stop already
warned the user with `bully session check failed:`). Make the required companion
change(s) first, then continue with the user's request. If the rule itself is wrong for
this repo, say so and adjust `.bully.yml` via the `bully-author` skill instead of
working around it -- fix the change or fix the rule, never ignore the gate.

## passed_checks

Rules already verified by deterministic script checks. Do not re-investigate their
concerns. Use them to catch cross-rule interactions (e.g. a semantic rule that overlaps
a passed script rule on an indirect code path).
````

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_skills_port.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add skills/bully tests/test_skills_port.py
git commit -m "M4 T2: bully skill -- Reasonix soft-gate interpreter"
```

---

## Task 3: `skills/bully-scheduler/SKILL.md` — agent → subagent skill (rewrite)

**Files:**
- Create: `skills/bully-scheduler/SKILL.md`
- Test: `tests/test_skills_port.py` (append)

- [ ] **Step 1: Append the failing test**

```python
def test_bully_scheduler_is_a_subagent_skill():
    text = _read("skills/bully-scheduler/SKILL.md")
    fm = _frontmatter(text)
    assert fm.get("name") == "bully-scheduler"
    assert fm.get("runas") == "subagent"
    assert "bash" in fm.get("allowed-tools", "")
    assert "model" not in fm  # routed via reasonix.toml [agent] subagent_models
    body = text.split("---\n", 2)[2]
    _assert_no_claude_isms(text, "skills/bully-scheduler")
    assert "python3 -m bully.semantic.analyzer" in body
    assert "bully-scheduler:" in body  # PR-title prefix contract
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_skills_port.py::test_bully_scheduler_is_a_subagent_skill -q`
Expected: FAIL (file not found)

- [ ] **Step 3: Write the skill**

Create `skills/bully-scheduler/SKILL.md` with exactly this content:

```markdown
---
name: bully-scheduler
description: Background entropy agent. Runs the bully rule-health analyzer against accumulated telemetry and opens a single, small PR retiring or downgrading the most-deserving rule (one rule per run).
runAs: subagent
allowed-tools: bash, read_file, edit_file, write_file, grep, glob
---

You are bully's background entropy agent. Your job is to keep the bully rule set healthy
without manual intervention. Each run you do *one* small thing -- never a sweep. You run
as an isolated subagent: only your final answer is returned to the parent, so end with a
one-line summary -- `no-op: <reason>` when you took no action, or the PR URL when you
opened one.

## What to do (in order)

1. Run `python3 -m bully.semantic.analyzer --log .bully/log.jsonl --config .bully.yml --json`. If telemetry is empty (`total_edits == 0`), stop: `no-op: empty telemetry`.
2. Check whether a prior scheduler PR is still open. Run `gh pr list --search 'bully-scheduler in:title' --state open --json number,title`. If the result is non-empty, stop: `no-op: prior scheduler PR open` -- wait for review before opening another.
3. Confirm the log window is wide enough to act on. Parse `report.window.first` and `report.window.last` from the analyzer JSON. If the window spans fewer than 14 days, stop: `no-op: telemetry window too narrow`.
4. Pick the single highest-priority candidate from the report:
   - First preference: a rule classified `dead`. (Window-age requirement is satisfied by step 3 -- every rule in `dead` has been silent across the whole window.)
   - Second preference: a rule classified `noisy` (violation_rate >= 0.7) and never fixed in PR notes.
   - Third preference: a rule classified `slow` (mean_latency_ms >= 1000).
5. If no candidate qualifies, stop: `no-op: no qualifying candidate`.
6. Open one PR that does *only one of these*:
   - Removes the dead rule from `.bully.yml` (do not touch any other rule).
   - Demotes a noisy rule's severity from `error` to `warning` and adds a note in the rule's `description`.
   - Annotates a slow rule with a `# slow: ...` YAML comment so a human can move it to pre-commit/CI.
7. PR title MUST start with `bully-scheduler:` so the prior-PR check above finds it. PR body must include the exact telemetry numbers used to justify the change.

## Constraints

- Never delete a rule that has any `evaluate_requested` in the last 7 days -- that's an active semantic rule the analyzer might just be miscounting.
- Never touch the rule set in CI, only in branch PRs.
- Never make more than one rule change per PR.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_skills_port.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add skills/bully-scheduler tests/test_skills_port.py
git commit -m "M4 T3: bully-scheduler as runAs:subagent skill"
```

---

## Task 4: `skills/bully-init/SKILL.md` — copy + edits

**Files:**
- Create: `skills/bully-init/SKILL.md` (from `../bully/skills/bully-init/SKILL.md`)
- Test: `tests/test_skills_port.py` (append)

- [ ] **Step 1: Append the failing test**

```python
def test_bully_init_is_reasonix_native():
    text = _read("skills/bully-init/SKILL.md")
    assert _frontmatter(text).get("name") == "bully-init"
    _assert_no_claude_isms(text, "skills/bully-init")
    assert "PreToolUse" in text
    assert "reasonix-hook" in text  # offers the hooks wiring block
    assert "[skills]" in text       # offers the reasonix.toml paths entry
    assert 'command -v bully 2>/dev/null || echo "python3 -m bully"' in text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_skills_port.py::test_bully_init_is_reasonix_native -q`
Expected: FAIL (file not found)

- [ ] **Step 3: Copy the source**

```bash
mkdir -p skills/bully-init
cp ../bully/skills/bully-init/SKILL.md skills/bully-init/SKILL.md
```

- [ ] **Step 4: Apply the edits (exact old → new)**

**4a — framing (cop vs lawmakers):**

old:
```
Bully is the cop; native linters (ruff, biome, eslint, tsc, phpstan, rubocop, golangci-lint, clippy, …) are the lawmakers. The PostToolUse hook runs on every Edit/Write, so bully is always the entry point to enforcement. Where a rule *definition* lives is a separate question:
```
new:
```
Bully is the cop; native linters (ruff, biome, eslint, tsc, phpstan, rubocop, golangci-lint, clippy, …) are the lawmakers. The PreToolUse hook runs on every pending edit (`edit_file`/`write_file`/`multi_edit`), so bully is always the entry point to enforcement. Where a rule *definition* lives is a separate question:
```

**4b — Step 2 proposal question:**

old:
```
> I found `<linter>` configured. Add a passthrough rule so bully runs it on every Edit/Write? The linter keeps owning its own rules -- bully just enforces "pass the linter" whenever you touch a matching file.
```
new:
```
> I found `<linter>` configured. Add a passthrough rule so bully runs it on every edit? The linter keeps owning its own rules -- bully just enforces "pass the linter" whenever you touch a matching file.
```

**4c — Step 2c enforcement-guarantee line:**

old:
```
For each migration, state the enforcement-guarantee line once: *"Bully still runs on every Edit/Write -- we're just deciding where the rule definition lives."* Then present the chosen routing and wait for confirmation before queueing.
```
new:
```
For each migration, state the enforcement-guarantee line once: *"Bully still runs on every edit -- we're just deciding where the rule definition lives."* Then present the chosen routing and wait for confirmation before queueing.
```

**4d — draft-validate code block:**

old:
```
# 1. Write the draft to a scratch path (use the Write tool):
#      /tmp/bully-init-draft.yml  <-- full proposed .bully.yml contents
#
# 2. Parse-check the draft:
BULLY=$(command -v bully 2>/dev/null || ls -d ~/.claude/plugins/cache/*/bully/*/bully 2>/dev/null | sort -V | tail -1)
"$BULLY" --validate --config /tmp/bully-init-draft.yml
```
new:
```
# 1. Write the draft to a scratch path (use the file-write tool):
#      /tmp/bully-init-draft.yml  <-- full proposed .bully.yml contents
#
# 2. Parse-check the draft ($BULLY deliberately unquoted -- the fallback is multi-word):
BULLY=$(command -v bully 2>/dev/null || echo "python3 -m bully")
$BULLY --validate --config /tmp/bully-init-draft.yml
```

**4e — Binary resolution section:**

old:
```
### Binary resolution

Bully ships a `bin/bully` wrapper that the plugin auto-adds to `$PATH`, so `command -v bully` should resolve on any 0.8.5+ install. Older caches won't have `bin/bully`; resolve with this one-liner (used above):

```bash
BULLY=$(command -v bully 2>/dev/null || ls -d ~/.claude/plugins/cache/*/bully/*/bin/bully 2>/dev/null | sort -V | tail -1)
```

`sort -V | tail -1` picks the newest cached version. If `$BULLY` is empty (very old install, no `bin/bully`), fall back to `PYTHONPATH=<plugin-path>/src python3 -m bully ...`.
```
new:
```
### Binary resolution

A pip install of `bully-reasonix` (e.g. `pip install -e <repo>`) provides both the `bully` console script and the `bully` module. Resolve once and use `$BULLY` -- unquoted, the fallback is a multi-word command -- in every command below:

```bash
BULLY=$(command -v bully 2>/dev/null || echo "python3 -m bully")
```

If `$BULLY --validate ...` fails with "No module named bully", bully-reasonix isn't installed in this Python environment: run `pip install -e <path-to-bully-reasonix>` first.
```

**4f — Step 6 items 1–2 (trust + doctor):**

old:
```
1. **Trust the config** so script/ast rules can execute: `bully trust` (fallback: `PYTHONPATH=<plugin-path>/src python3 -m bully --trust --config .bully.yml`).
2. **Run `bully doctor`** and surface any `[FAIL]` lines. **Known false positives for plugin installs** -- note them but do not try to "fix" them:
   - `[FAIL] no PostToolUse hook invoking hook.sh found in .claude/settings.json` -- the plugin loads `hooks/hooks.json` dynamically via the Claude Code plugin system. `.claude/settings.json` is *not* where the hook lives for plugin installs, so this FAIL is expected and harmless.
   - Skill/agent paths pointing at an older version (e.g. binary is `0.5.0` but doctor resolves skills from `0.3.0`). Doctor picks the first match in the plugin cache; stale cache directories from prior versions can shadow the current one. Either tell the user to delete old cache dirs under `~/.claude/plugins/cache/bully-marketplace/bully/` or just note it.

   Any `[FAIL]` outside that list is real -- surface it.
```
new:
````
1. **Trust the config** so script/ast rules can execute: `$BULLY trust` (equivalently `$BULLY --trust --config .bully.yml`).
2. **Run `$BULLY doctor`** and surface any `[FAIL]` lines. Doctor checks the Reasonix wiring; the two failures you can fix from here:
   - `[FAIL] no PreToolUse hook ...` -- the project's `.reasonix/settings.json` does not invoke the bully hook, so edits are not linted. Offer to write this hooks block (merge into an existing file rather than overwriting):

     ```json
     {
       "hooks": {
         "PreToolUse": [{ "match": "edit_file|write_file|multi_edit", "command": "python3 -m bully reasonix-hook", "timeout": 15000 }],
         "Stop": [{ "command": "python3 -m bully reasonix-hook", "timeout": 15000 }],
         "UserPromptSubmit": [{ "command": "python3 -m bully reasonix-hook", "timeout": 10000 }],
         "SessionStart": [{ "command": "python3 -m bully reasonix-hook" }],
         "SubagentStop": [{ "command": "python3 -m bully reasonix-hook" }]
       }
     }
     ```

   - `[FAIL] skill bully-evaluator missing ...` -- the bully skills aren't discoverable. Offer to add to the project's `reasonix.toml`:

     ```toml
     [skills]
     paths = ["<path-to-bully-reasonix>/skills"]
     ```

   `[WARN]` lines are advisory (optional event wirings, missing companion skills); report them, don't chase them. Any other `[FAIL]` is real -- surface it.
````

**4g — Step 6 item 4 smoke test:**

old:
```
4. **Smoke-test script rules.** For each rule with a concrete `script:`, pick the first in-scope file (e.g. `git ls-files | grep -E '\.(ts|tsx)$' | head -1` against the rule's scope) and run `bully lint <file> --rule <rule-id>`. Report each verdict. If a rule that is *meant* to fire on a known pattern returns pass, flag it as a likely miscompile -- surface it now, not after 40 edits.
```
new:
```
4. **Smoke-test script rules.** For each rule with a concrete `script:`, pick the first in-scope file (e.g. `git ls-files | grep -E '\.(ts|tsx)$' | head -1` against the rule's scope) and run `$BULLY lint <file> --rule <rule-id>`. Report each verdict. If a rule that is *meant* to fire on a known pattern returns pass, flag it as a likely miscompile -- surface it now, not after 40 edits.
```

- [ ] **Step 5: Verify no leftovers, run the tests**

```bash
grep -inE 'claude|posttooluse|plugins/cache|hooks\.json|bin/bully|PYTHONPATH' skills/bully-init/SKILL.md
```
Expected: no output (fix any stragglers in the same spirit as the edits above).

Run: `python3 -m pytest tests/test_skills_port.py -q`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add skills/bully-init tests/test_skills_port.py
git commit -m "M4 T4: port bully-init skill"
```

---

## Task 5: `skills/bully-author/SKILL.md` — copy + edits

**Files:**
- Create: `skills/bully-author/SKILL.md` (from `../bully/skills/bully-author/SKILL.md`)
- Test: `tests/test_skills_port.py` (append)

- [ ] **Step 1: Append the failing test**

```python
def test_bully_author_is_reasonix_native():
    text = _read("skills/bully-author/SKILL.md")
    assert _frontmatter(text).get("name") == "bully-author"
    _assert_no_claude_isms(text, "skills/bully-author")
    assert "PreToolUse" in text
    assert 'command -v bully 2>/dev/null || echo "python3 -m bully"' in text
    assert "scripts/dogfood.sh" not in text
    assert "--print-prompt" in text  # fixture protocol survived the port
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_skills_port.py::test_bully_author_is_reasonix_native -q`
Expected: FAIL (file not found)

- [ ] **Step 3: Copy the source**

```bash
mkdir -p skills/bully-author
cp ../bully/skills/bully-author/SKILL.md skills/bully-author/SKILL.md
```

- [ ] **Step 4: Apply the edits (exact old → new)**

**4a — routing intro:**

old:
```
Bully is the cop; linters are the lawmakers. The PostToolUse hook runs on every Edit/Write regardless of *where* a rule's definition lives. The routing question is just which tool the hook invokes to check the file. In priority order:
```
new:
```
Bully is the cop; linters are the lawmakers. The PreToolUse hook runs on every pending edit (`edit_file`/`write_file`/`multi_edit`) regardless of *where* a rule's definition lives. The routing question is just which tool the hook invokes to check the file. In priority order:
```

**4b — enforcement-guarantee line:**

old:
```
> I'd enable this rule in `<linter>`'s config. Bully still enforces it on every Edit/Write via a passthrough rule -- the question is just *where the rule definition lives*, not whether bully enforces it.
```
new:
```
> I'd enable this rule in `<linter>`'s config. Bully still enforces it on every edit via a passthrough rule -- the question is just *where the rule definition lives*, not whether bully enforces it.
```

**4c — binary resolution (fixture-testing protocol):**

old:
```
The plugin ships `bin/bully` on `$PATH` (0.8.5+), but older caches won't. Resolve it once at the top of the protocol and use `$BULLY` in every command below:

```bash
BULLY=$(command -v bully 2>/dev/null || ls -d ~/.claude/plugins/cache/*/bully/*/bin/bully 2>/dev/null | sort -V | tail -1)
```

If `$BULLY` is empty (no `bin/bully` in the cache), fall back to `PYTHONPATH=~/.bully/src python3 -m bully` for manual installs.
```
new:
```
A pip install of `bully-reasonix` provides both the `bully` console script and the `bully` module. Resolve once at the top of the protocol and use `$BULLY` -- unquoted, the fallback is a multi-word command -- in every command below:

```bash
BULLY=$(command -v bully 2>/dev/null || echo "python3 -m bully")
```

If neither resolves ("No module named bully"), install it first: `pip install -e <path-to-bully-reasonix>`.
```

**4d — drop the bully-repo dogfood aside (Adding a new rule, step 5):**

old:
```
   In this repo, also run `bash scripts/dogfood.sh`. If the rule mass-flags the codebase, narrow it or treat the flags as real cleanup.
```
new:
```
   If the rule mass-flags the codebase, narrow it or treat the flags as real cleanup.
```

**4e — drop the dogfood alternative (Removing a rule, step 3):**

old:
```
3. Sanity-check (re-resolve `$BULLY` with the one-liner from the fixture-testing protocol if needed):
   ```bash
   bash scripts/dogfood.sh
   # or
   $BULLY --file <existing-file> --config .bully.yml
   ```
```
new:
```
3. Sanity-check (re-resolve `$BULLY` with the one-liner from the fixture-testing protocol if needed):
   ```bash
   $BULLY --file <existing-file> --config .bully.yml
   ```
```

- [ ] **Step 5: Verify no leftovers, run the tests**

```bash
grep -inE 'claude|posttooluse|plugins/cache|bin/bully|PYTHONPATH|dogfood' skills/bully-author/SKILL.md
```
Expected: no output.

Run: `python3 -m pytest tests/test_skills_port.py -q`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add skills/bully-author tests/test_skills_port.py
git commit -m "M4 T5: port bully-author skill"
```

---

## Task 6: `skills/bully-review/SKILL.md` — copy + edits + hook-health step

**Files:**
- Create: `skills/bully-review/SKILL.md` (from `../bully/skills/bully-review/SKILL.md`)
- Test: `tests/test_skills_port.py` (append)

- [ ] **Step 1: Append the failing test**

```python
def test_bully_review_is_reasonix_native():
    text = _read("skills/bully-review/SKILL.md")
    assert _frontmatter(text).get("name") == "bully-review"
    _assert_no_claude_isms(text, "skills/bully-review")
    assert "python3 -m bully.semantic.analyzer" in text
    assert "hook_fail_open" in text
    assert "session_init" in text
    assert "/bully-scheduler" in text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_skills_port.py::test_bully_review_is_reasonix_native -q`
Expected: FAIL (file not found)

- [ ] **Step 3: Copy the source**

```bash
mkdir -p skills/bully-review
cp ../bully/skills/bully-review/SKILL.md skills/bully-review/SKILL.md
```

- [ ] **Step 4: Apply the edits (exact old → new)**

**4a — analyzer invocation (Step 1):**

old:
```
```bash
python3 pipeline/analyzer.py --log .bully/log.jsonl
```

Add `--config .bully.yml --json` when you need structured output to reason over. Thresholds `--noisy-threshold` (default 0.5) and `--slow-threshold-ms` (default 500) are tunable.
```
new:
```
```bash
python3 -m bully.semantic.analyzer --log .bully/log.jsonl --config .bully.yml
```

Add `--json` when you need structured output to reason over. Thresholds `--noisy-threshold` (default 0.5) and `--slow-threshold-ms` (default 500) are tunable. (If "No module named bully": `pip install -e <path-to-bully-reasonix>` first.)
```

**4b — insert a new section between "## Step 1: Run the analyzer" and "## Step 2: Classify":**

````markdown
## Step 1b: Hook health

The analyzer covers rules; these records cover the hook itself. Check them before
classifying -- a sick hook makes every rule look dead:

```bash
grep -c '"type": "hook_fail_open"' .bully/log.jsonl
```

Nonzero means the PreToolUse hook crashed and failed open -- those edits went
**unlinted**. Read the latest few (`grep '"type": "hook_fail_open"' .bully/log.jsonl | tail -3`),
report the `error` strings, and treat a repeating error as a bug to surface before any
rule tuning.

Also sanity-check the session stamps:

- `"type": "session_init"` -- one per Reasonix session. None in the whole log means the
  SessionStart hook isn't wired (`python3 -m bully doctor`) and the semantic verdict
  cache never resets per session.
- `"type": "subagent_stop"` -- evaluator/subagent completion markers. Zero despite
  `evaluate_requested` records means the SubagentStop hook isn't wired; harmless to
  rules, but telemetry undercounts evaluator activity.
````

**4c — background scheduling:**

old:
```
For continuous self-pruning rather than ad-hoc cleanup, the `bully-scheduler` agent (under `agents/bully-scheduler.md`) runs the same analyzer on a schedule and opens at most one rule-retirement PR per run. Wire it via the `/schedule` skill — there's no separate config needed.
```
new:
```
For continuous self-pruning rather than ad-hoc cleanup, the `bully-scheduler` skill (`skills/bully-scheduler/SKILL.md`, `runAs: subagent`) runs the same analyzer and opens at most one rule-retirement PR per run. Invoke it as `/bully-scheduler` (or dispatch it via `run_skill`) for a one-off pruning pass, or run it on a schedule from CI/cron with a non-interactive `reasonix` invocation.
```

- [ ] **Step 5: Verify no leftovers, run the tests**

```bash
grep -inE 'claude|posttooluse|pipeline/analyzer|agents/' skills/bully-review/SKILL.md
```
Expected: no output.

Run: `python3 -m pytest tests/test_skills_port.py -q`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add skills/bully-review tests/test_skills_port.py
git commit -m "M4 T6: port bully-review skill + hook-health telemetry consumer"
```

---

## Task 7: Rewrite `cli/doctor.py` for Reasonix wiring + skill discovery

**Files:**
- Rewrite: `src/bully/cli/doctor.py`
- Test: `tests/test_doctor.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_doctor.py`:

```python
# tests/test_doctor.py
"""M4: doctor rewritten for .reasonix wiring + reasonix skill discovery."""
import json

import pytest

from bully.cli.doctor import (
    check_python_version,
    cmd_doctor,
    match_covers_edit_tools,
    read_skills_paths,
)

HOOK_CMD = "python3 -m bully reasonix-hook"

FULL_HOOKS = {
    "hooks": {
        "PreToolUse": [{"match": "edit_file|write_file|multi_edit", "command": HOOK_CMD}],
        "Stop": [{"command": HOOK_CMD}],
        "UserPromptSubmit": [{"command": HOOK_CMD}],
        "SessionStart": [{"command": HOOK_CMD}],
        "SubagentStop": [{"command": HOOK_CMD}],
    }
}

ALL_SKILLS = (
    "bully-evaluator", "bully", "bully-init",
    "bully-author", "bully-review", "bully-scheduler",
)

SCRIPT_RULE = (
    "schema_version: 1\n"
    "rules:\n"
    "  no-x:\n"
    '    description: "No X."\n'
    "    engine: script\n"
    '    scope: ["*.py"]\n'
    "    severity: error\n"
    '    script: "grep -n X {file} && exit 1 || exit 0"\n'
)

SESSION_RULE = (
    "  changelog-updated:\n"
    '    description: "src changes need a changelog entry."\n'
    "    engine: session\n"
    "    severity: error\n"
    "    when:\n"
    "      changed_any:\n"
    '        - "src/**"\n'
    "    require:\n"
    "      changed_any:\n"
    '        - "CHANGELOG.md"\n'
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _project(parent, hooks=FULL_HOOKS, skills=ALL_SKILLS, session_rule=False):
    root = parent / "proj"
    skills_dir = root / ".reasonix" / "skills"
    skills_dir.mkdir(parents=True)
    if hooks is not None:
        (root / ".reasonix" / "settings.json").write_text(json.dumps(hooks))
    for name in skills:
        d = skills_dir / name
        d.mkdir()
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\nbody\n")
    config = SCRIPT_RULE + (SESSION_RULE if session_rule else "")
    (root / ".bully.yml").write_text(config)
    return root


def test_doctor_passes_fully_wired_project(tmp_path, home, capsys):
    root = _project(tmp_path)
    assert cmd_doctor(root) == 0
    out = capsys.readouterr().out
    assert "[FAIL]" not in out
    assert "[OK] PreToolUse hook wired" in out
    assert "[OK] skill bully-evaluator at" in out


def test_doctor_fails_without_pretooluse_hook(tmp_path, home, capsys):
    hooks = {"hooks": {k: v for k, v in FULL_HOOKS["hooks"].items() if k != "PreToolUse"}}
    root = _project(tmp_path, hooks=hooks)
    assert cmd_doctor(root) == 1
    assert "[FAIL] no PreToolUse hook" in capsys.readouterr().out


def test_doctor_fails_when_match_misses_an_edit_tool(tmp_path, home, capsys):
    hooks = json.loads(json.dumps(FULL_HOOKS))
    hooks["hooks"]["PreToolUse"][0]["match"] = "edit_file"
    root = _project(tmp_path, hooks=hooks)
    assert cmd_doctor(root) == 1
    assert "does not cover" in capsys.readouterr().out


def test_session_events_warn_without_session_rules_fail_with(tmp_path, home, capsys):
    pre_only = {"hooks": {"PreToolUse": FULL_HOOKS["hooks"]["PreToolUse"]}}
    root = _project(tmp_path, hooks=pre_only)
    assert cmd_doctor(root) == 0
    out = capsys.readouterr().out
    assert "[WARN] Stop hook not wired" in out
    assert "[WARN] UserPromptSubmit hook not wired" in out
    assert "[WARN] SessionStart hook not wired" in out
    assert "[WARN] SubagentStop hook not wired" in out

    parent = tmp_path / "with-session-rule"
    parent.mkdir()
    root2 = _project(parent, hooks=pre_only, session_rule=True)
    assert cmd_doctor(root2) == 1
    out = capsys.readouterr().out
    assert "[FAIL] Stop hook not wired" in out
    assert "[FAIL] UserPromptSubmit hook not wired" in out


def test_missing_evaluator_fails_missing_companion_warns(tmp_path, home, capsys):
    root = _project(tmp_path, skills=tuple(s for s in ALL_SKILLS if s != "bully-evaluator"))
    assert cmd_doctor(root) == 1
    assert "[FAIL] skill bully-evaluator missing" in capsys.readouterr().out

    parent = tmp_path / "evaluator-only"
    parent.mkdir()
    root2 = _project(parent, skills=("bully-evaluator",))
    assert cmd_doctor(root2) == 0
    assert "[WARN] skill bully missing" in capsys.readouterr().out


def test_skills_found_via_reasonix_toml_paths(tmp_path, home, capsys):
    root = _project(tmp_path, skills=())
    ext = tmp_path / "elsewhere" / "skills"
    for name in ALL_SKILLS:
        d = ext / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\nbody\n")
    (root / "reasonix.toml").write_text(f'[skills]\npaths = ["{ext}"]\n')
    assert cmd_doctor(root) == 0
    assert "[FAIL]" not in capsys.readouterr().out


def test_read_skills_paths_handles_missing_file_and_lists(tmp_path):
    assert read_skills_paths(tmp_path / "nope.toml") == []
    p = tmp_path / "reasonix.toml"
    p.write_text('[skills]\npaths = ["skills", "~/more-skills"]\n\n[agent]\n')
    assert read_skills_paths(p) == ["skills", "~/more-skills"]


def test_match_covers_edit_tools():
    assert match_covers_edit_tools(None) is True
    assert match_covers_edit_tools("edit_file|write_file|multi_edit") is True
    assert match_covers_edit_tools("edit_file") is False
    assert match_covers_edit_tools("(((") is False


def test_check_python_version():
    ok, msg = check_python_version((3, 10))
    assert ok and msg == "[OK] Python 3.10"
    ok, msg = check_python_version((3, 9))
    assert not ok and msg.startswith("[FAIL] Python 3.9 < 3.10")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_doctor.py -q`
Expected: FAIL (`ImportError: cannot import name 'match_covers_edit_tools'`)

- [ ] **Step 3: Rewrite `src/bully/cli/doctor.py`**

Replace the entire file with:

```python
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
```

Note: in the `[FAIL] skill … missing` print, only the **first** string fragment is an f-string — the `{.reasonix,...}` braces in the later fragments must stay literal.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_doctor.py -q`
Expected: 9 passed

- [ ] **Step 5: Run the full suite (the doctor rewrite must not break `cli/__init__.py`'s zero-arg call)**

Run: `python3 -m pytest -q && ruff check src tests`
Expected: all pass, ruff clean

- [ ] **Step 6: Commit**

```bash
git add src/bully/cli/doctor.py tests/test_doctor.py
git commit -m "M4 T7: doctor rewrite -- .reasonix wiring + skill discovery"
```

---

## Task 8: `reasonix.toml` + `REASONIX.md`

**Files:**
- Create: `reasonix.toml`, `REASONIX.md`
- Test: `tests/test_skills_port.py` (append)

- [ ] **Step 1: Append the failing tests**

```python
def test_reasonix_toml_wires_the_skills_dir():
    text = _read("reasonix.toml")
    assert "[skills]" in text
    assert 'paths = ["skills"]' in text
    assert "subagent_models" in text


def test_reasonix_md_carries_the_standing_instructions():
    text = _read("REASONIX.md")
    _assert_no_claude_isms(text, "REASONIX.md")
    assert "--log-verdict" in text
    assert "AGENTIC LINT" in text
    assert "bully-author" in text  # the session-gate escape: fix the change or fix the rule
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_skills_port.py -q`
Expected: 2 failures (files not found)

- [ ] **Step 3: Write `reasonix.toml`**

```toml
# Reasonix project config for bully-reasonix.
#
# Installing bully into another project: point that project's reasonix.toml at
# this repo's skills/ directory (absolute path, or relative to that project's
# root) and wire the hooks in its .reasonix/settings.json -- see REASONIX.md
# and `python3 -m bully doctor`.

[skills]
paths = ["skills"]

[agent]
# Optional: route the bully subagent skills to a cheaper/faster model. Values
# must be model refs configured in your reasonix providers; uncomment and adjust:
# subagent_models = { "bully-evaluator" = "deepseek-chat", "bully-scheduler" = "deepseek-chat" }
```

- [ ] **Step 4: Write `REASONIX.md`**

````markdown
# bully — agentic lint for this project

bully lints every pending edit through Reasonix hooks. You do not run it as part of
normal work — it interrupts you when a rule fires. Rules live in `.bully.yml`; the
full playbook is the `bully` skill. Everything below keys off messages that begin
with `AGENTIC LINT`.

## Edit blocked (`AGENTIC LINT -- blocked`)

The edit did **not** land — the file on disk is unchanged. Fix every listed violation
and issue the corrected edit.

## Edit paused (`AGENTIC LINT SEMANTIC EVALUATION REQUIRED`)

Follow the message's instructions: evaluate the listed rules (dispatch the
`bully-evaluator` skill via `run_skill` unless it is a single rule over a short diff),
then log one verdict per rule with the diff id the message gave:

    python3 -m bully --log-verdict --diff-id <id> --rule <rule-id> --verdict <pass|violation> --file <path>

Logging is load-bearing: the re-applied edit is admitted only once **every** listed
rule has a logged `pass` for that exact diff. If a rule is violated, fix the edit and
apply the corrected version instead — a changed edit is evaluated fresh.

## Turn gated (`AGENTIC LINT -- unsatisfied session rules`)

A session rule from the previous turn is unsatisfied (e.g. "changing src/ requires a
CHANGELOG entry"). Either make the required companion change, or — if the rule itself
is wrong for this repo — adjust `.bully.yml` with the `bully-author` skill and tell
the user. Then continue with the user's request. Never ignore the gate.

## Skills

- `bully` — full hook-output playbook (the sections above, in depth)
- `bully-init` / `bully-author` / `bully-review` — create / edit / audit rules
- `bully-evaluator`, `bully-scheduler` — subagents; dispatch them, don't chat with them

Diagnostics: `python3 -m bully doctor`. Telemetry: `.bully/log.jsonl` (see
`docs/telemetry.md`).
````

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_skills_port.py -q`
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add reasonix.toml REASONIX.md tests/test_skills_port.py
git commit -m "M4 T8: reasonix.toml skills wiring + REASONIX.md standing instructions"
```

---

## Task 9: Status update + full verification

**Files:**
- Modify: `CLAUDE.md` (Status section)

- [ ] **Step 1: Update `CLAUDE.md`**

Replace:
```
- **Next:** M4 — skill ports (`bully`, `bully-init`, `bully-author`, `bully-review`, `bully-scheduler` as `runAs: subagent`), `doctor` rewrite for `.reasonix`, `reasonix.toml`, `REASONIX.md`.
```
with:
```
- **M4 done:** all 6 skills shipped (`bully`, `bully-init`, `bully-author`, `bully-review` inline; `bully-evaluator`, `bully-scheduler` as `runAs: subagent`), `doctor` rewritten for `.reasonix/settings.json` wiring + reasonix skill discovery (convention dirs + `reasonix.toml` `[skills] paths`), `reasonix.toml` + `REASONIX.md`, and ported `docs/{rule-authoring,telemetry}.md` + `examples/rules/`.
- **Next:** W2 integration — dogfood script (`scripts/lint.sh` analog), manual live-smoke doc, release prep.
```

- [ ] **Step 2: Full verification**

Run: `python3 -m pytest -q && ruff check src tests`
Expected: all tests pass (54 pre-existing + 18 new: 9 in `test_skills_port.py`, 9 in `test_doctor.py`), ruff clean.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "M4 T9: finalize skill ports; update CLAUDE.md status"
```

---

## Out of scope (don't drift into these)

- Extending the analyzer to count `hook_fail_open` (Decision 5: grep in `bully-review` is the consumer).
- Re-adding any Stop-gate / `bully ack` flow (M3 decision: unreachable under the soft-gate).
- `scripts/lint.sh` / dogfood script, live-smoke doc, release prep — W2.
- `bench/` port — explicitly out of scope in the spec.
