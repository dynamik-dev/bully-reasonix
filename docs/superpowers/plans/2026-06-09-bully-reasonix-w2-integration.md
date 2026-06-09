# W2 — Integration & Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out the bully → Reasonix port with the integration wave (spec §10 W2): a runnable quality gate that dogfoods the real hook, a manual live-smoke runbook, and the first release commit (`1.0.0-rc.1`).

**Architecture:** No engine or harness code changes — M1–M4 already shipped the working product (72 tests green, ruff clean). W2 adds the *operator surface* around it: two bash scripts under `scripts/` (`dogfood.sh` exercises `python3 -m bully reasonix-hook` end-to-end through stdin/exit-code, which the unit tests stop short of; `lint.sh` is the one-command quality gate), a `docs/live-smoke.md` runbook for the manual Reasonix+DeepSeek smoke that can't run in CI, a top-level `README.md`, and a version bump. The engine under `src/bully/` stays byte-identical to the port snapshot (outside the already-rewritten `cli/doctor.py`).

**Tech Stack:** Python 3.10+ (stdlib-only engine), bash 3.2-compatible scripts (macOS default), pytest, ruff, Reasonix Go CLI (target `v1.4.0`).

---

## What we are NOT touching (invariants to preserve)

- **`src/bully/` is byte-identical to the port snapshot, except `cli/doctor.py`.** W2 adds *no* code under `src/bully/`. In particular: **do NOT change `BULLY_VERSION = "0.8.6"` in `src/bully/__init__.py`.** That constant is the telemetry *producer* tag tracking the engine snapshot, not the distribution version. The release version lives only in `pyproject.toml`. Conflating them would break the byte-identical engine invariant the M4 review protected.
- No new pytest changes to existing engine/harness tests. W2 only *adds* test files for the two new scripts.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `scripts/dogfood.sh` | Create | End-to-end smoke of the real hook: craft PreToolUse payloads, pipe to `bully reasonix-hook`, assert exit 2 blocks a violating edit and exit 0 admits a clean one (spec §9). |
| `scripts/lint.sh` | Create | One-command quality gate: ruff check, ruff format --check, shellcheck (if present), pytest, dogfood. |
| `tests/test_dogfood.py` | Create | Runs `scripts/dogfood.sh` as a subprocess; asserts exit 0 (all internal asserts pass) + valid bash syntax + strict-mode header. |
| `tests/test_lint_script.py` | Create | Asserts `scripts/lint.sh` parses (`bash -n`), has the strict-mode header, and invokes ruff/pytest/dogfood. |
| `docs/live-smoke.md` | Create | Manual runbook for the deferred live smoke against a real Reasonix + DeepSeek key. Not gating CI. |
| `README.md` | Create | Public entry doc: what bully-reasonix is, install, quickstart, where to go next. |
| `pyproject.toml:7` | Modify | Version `0.1.0` → `1.0.0-rc.1`. |
| `CLAUDE.md` | Modify | Flip the Status section: M1–M4 + W2 done; port complete. |
| `REASONIX.md` | Modify (pass) | Light review; add a one-line pointer to `docs/live-smoke.md`. |

Each task produces a self-contained, committable change.

---

### Task 1: `scripts/dogfood.sh` — the Reasonix hook smoke

**Files:**
- Create: `scripts/dogfood.sh`
- Test: `tests/test_dogfood.py`

**Why this exists:** `tests/test_reasonix_hook.py` calls `handle_payload()` in-process. It never crosses the real subprocess boundary — stdin JSON → `run_reasonix_hook()` → process exit code → stderr text. The dogfood does exactly that against the installed/module entry point, so a packaging or dispatch regression (e.g. `reasonix-hook` verb not wired in `cli/__init__.py`) is caught.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dogfood.py`:

```python
# tests/test_dogfood.py
"""The dogfood script must drive the real `bully reasonix-hook` subprocess and
exit 0 (all of its internal block/allow assertions held)."""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "dogfood.sh"


def test_dogfood_script_exists_and_is_strict():
    assert SCRIPT.is_file(), "scripts/dogfood.sh missing"
    head = SCRIPT.read_text().splitlines()[:5]
    assert any("set -euo pipefail" in line for line in head), "missing strict-mode header"


def test_dogfood_script_has_valid_bash_syntax():
    # `bash -n` parses without executing.
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_dogfood_script_passes():
    # Runs the real hook over crafted payloads; exit 0 == every assertion held.
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "dogfood OK" in r.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_dogfood.py -q`
Expected: FAIL — `scripts/dogfood.sh missing` (file not created yet).

- [ ] **Step 3: Write the script**

Create `scripts/dogfood.sh`:

```bash
#!/usr/bin/env bash
# Dogfood the Reasonix hook end-to-end: feed crafted PreToolUse payloads to the
# real `bully reasonix-hook` entry point (stdin JSON -> exit code + stderr) and
# assert the deterministic gate blocks a violating edit and admits a clean one.
# Exercises the subprocess boundary the unit tests stop short of (spec §9).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Resolve the hook command: prefer the installed console script, else module
# form with src on PYTHONPATH (fresh checkout, no `pip install -e .`).
if command -v bully >/dev/null 2>&1; then
  BULLY=(bully)
else
  export PYTHONPATH="$REPO_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
  BULLY=(python3 -m bully)
fi

# The crafted project is trusted by construction; skip the machine-local gate.
export BULLY_TRUST_ALL=1

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cat >"$WORK/.bully.yml" <<'YML'
schema_version: 1
rules:
  no-forbidden:
    description: "No FORBIDDEN marker."
    engine: script
    scope: ["*.py"]
    severity: error
    script: "grep -n FORBIDDEN {file} && exit 1 || exit 0"
YML
printf 'x = 1\n' >"$WORK/app.py"

# Run the hook with a payload on stdin; stash exit code + combined output.
HOOK_OUT=""
HOOK_CODE=0
run_hook() {
  local payload="$1"
  set +e
  HOOK_OUT="$(printf '%s' "$payload" | "${BULLY[@]}" reasonix-hook 2>&1)"
  HOOK_CODE=$?
  set -e
}

fail=0

# 1) An edit that introduces a violation must be blocked (exit 2) and name the rule.
run_hook "{\"event\":\"PreToolUse\",\"cwd\":\"$WORK\",\"toolName\":\"edit_file\",\"toolArgs\":{\"path\":\"$WORK/app.py\",\"old_string\":\"x = 1\",\"new_string\":\"x = 1  # FORBIDDEN\"}}"
if [[ $HOOK_CODE -ne 2 ]]; then
  echo "FAIL: violating edit_file exit $HOOK_CODE (expected 2)"; fail=1
elif ! grep -q no-forbidden <<<"$HOOK_OUT"; then
  echo "FAIL: block message missing rule id; got: $HOOK_OUT"; fail=1
elif grep -q FORBIDDEN "$WORK/app.py"; then
  echo "FAIL: blocked edit still landed on disk"; fail=1
else
  echo "ok: violating edit_file blocked (exit 2, no-forbidden), file untouched"
fi

# 2) A clean edit must be admitted (exit 0, silent).
run_hook "{\"event\":\"PreToolUse\",\"cwd\":\"$WORK\",\"toolName\":\"edit_file\",\"toolArgs\":{\"path\":\"$WORK/app.py\",\"old_string\":\"x = 1\",\"new_string\":\"x = 2\"}}"
if [[ $HOOK_CODE -ne 0 ]]; then
  echo "FAIL: clean edit_file exit $HOOK_CODE (expected 0); got: $HOOK_OUT"; fail=1
else
  echo "ok: clean edit_file admitted (exit 0)"
fi

# 3) A brand-new write_file that introduces a violation must be blocked, with
#    nothing written to disk.
run_hook "{\"event\":\"PreToolUse\",\"cwd\":\"$WORK\",\"toolName\":\"write_file\",\"toolArgs\":{\"path\":\"$WORK/new.py\",\"content\":\"y = 1  # FORBIDDEN\n\"}}"
if [[ $HOOK_CODE -ne 2 ]]; then
  echo "FAIL: violating write_file exit $HOOK_CODE (expected 2)"; fail=1
elif [[ -e "$WORK/new.py" ]]; then
  echo "FAIL: blocked write_file created the file"; fail=1
else
  echo "ok: violating write_file blocked (exit 2), no file created"
fi

# 4) The materialized pending temp file must be cleaned up after each run.
if compgen -G "$WORK/.bully/tmp/pending-*" >/dev/null; then
  echo "FAIL: leaked materialized temp file under .bully/tmp"; fail=1
else
  echo "ok: no leaked pending temp files"
fi

if [[ $fail -ne 0 ]]; then
  echo "dogfood FAILED"; exit 1
fi
echo "dogfood OK"
```

- [ ] **Step 4: Make it executable**

Run: `chmod +x scripts/dogfood.sh`

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_dogfood.py -q`
Expected: PASS (3 tests). If `bully` isn't installed, the script's PYTHONPATH fallback runs the module form — still green.

Sanity (optional): `bash scripts/dogfood.sh` → prints four `ok:` lines and `dogfood OK`.

- [ ] **Step 6: Commit**

```bash
git add scripts/dogfood.sh tests/test_dogfood.py
git commit -m "W2 T1: reasonix-hook dogfood smoke (crafted PreToolUse payloads, exit-code asserts)"
```

---

### Task 2: `scripts/lint.sh` — one-command quality gate

**Files:**
- Create: `scripts/lint.sh`
- Test: `tests/test_lint_script.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_lint_script.py`:

```python
# tests/test_lint_script.py
"""The lint orchestrator must parse, be strict-mode, and wire the four checks.

It is NOT executed here: running pytest inside pytest is nonsense. We assert
structure (`bash -n` + content), and Task 1 already proves the dogfood leg."""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "lint.sh"


def test_lint_script_exists_and_is_strict():
    assert SCRIPT.is_file(), "scripts/lint.sh missing"
    head = SCRIPT.read_text().splitlines()[:5]
    assert any("set -euo pipefail" in line for line in head), "missing strict-mode header"


def test_lint_script_has_valid_bash_syntax():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_lint_script_runs_the_four_checks():
    body = SCRIPT.read_text()
    for needed in ("ruff check", "ruff format --check", "pytest", "dogfood.sh"):
        assert needed in body, f"lint.sh does not invoke {needed!r}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_lint_script.py -q`
Expected: FAIL — `scripts/lint.sh missing`.

- [ ] **Step 3: Write the script**

Create `scripts/lint.sh`:

```bash
#!/usr/bin/env bash
# Single entry point for this repo's quality gate: ruff lint, format check,
# shellcheck (when installed), pytest, and the Reasonix hook dogfood.
# After `pip install -e ".[dev]"`, run: bash scripts/lint.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

fail=0

echo "=> ruff check"
ruff check . || fail=1

echo "=> ruff format --check"
ruff format --check . || fail=1

echo "=> shellcheck"
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck scripts/*.sh || fail=1
else
  echo "   (shellcheck not installed -- skipping)"
fi

echo "=> pytest"
pytest -q || fail=1

echo "=> dogfood (reasonix hook smoke)"
bash scripts/dogfood.sh || fail=1

if [[ $fail -ne 0 ]]; then
  echo
  echo "One or more checks failed."
  exit 1
fi

echo
echo "All checks passed."
```

- [ ] **Step 4: Make it executable**

Run: `chmod +x scripts/lint.sh`

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_lint_script.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add scripts/lint.sh tests/test_lint_script.py
git commit -m "W2 T2: scripts/lint.sh one-command quality gate (ruff + shellcheck + pytest + dogfood)"
```

---

### Task 3: `docs/live-smoke.md` — manual live-smoke runbook

**Files:**
- Create: `docs/live-smoke.md`

No automated test — this is a prose runbook for the smoke that needs a real Reasonix binary and a DeepSeek key (neither present in CI). The verification step is a docs sanity check.

- [ ] **Step 1: Write the runbook**

Create `docs/live-smoke.md`:

````markdown
# Live smoke — manual

The automated suite (`scripts/lint.sh`) proves the engine and the hook plumbing
in isolation. This runbook is the one check it can't do in CI: drive a **real
Reasonix session** with a **real DeepSeek key** and watch bully actually refuse a
bad edit. It is manual and **not gating** — run it before a release, or when you
change the harness seam (`harness/reasonix.py`, `cli/reasonix_hook.py`,
`.reasonix/settings.json`).

## Prerequisites

1. **Reasonix CLI** on PATH — the Go line, `v1.4.0` or newer
   (<https://github.com/esengine/DeepSeek-Reasonix>). Confirm: `reasonix version`.
2. **DeepSeek API key** configured for Reasonix (`reasonix setup`, then provide
   the key as that flow directs). Bully itself makes no API calls — the key is
   for the model that *consumes* bully's hook output and dispatches the evaluator.
3. **bully importable** in the session: either `pip install -e .` in this repo, or
   `export PYTHONPATH="$PWD/src"` before launching Reasonix.
4. **Wiring in place** — this repo already ships it; in another project run
   `python3 -m bully doctor` and fix every `[FAIL]`:
   - `.reasonix/settings.json` hooks → `python3 -m bully reasonix-hook`
     (PreToolUse / Stop / UserPromptSubmit / SessionStart / SubagentStop).
   - `reasonix.toml` `[skills] paths = ["skills"]` so the evaluator skill resolves.
   - A trusted `.bully.yml` (`python3 -m bully trust`), or `export BULLY_TRUST_ALL=1`.

## Smoke 1 — deterministic block (the core gate)

1. Put a hard rule in `.bully.yml`:
   ```yaml
   schema_version: 1
   rules:
     no-todo-comments:
       description: "No TODO comments in committed source."
       engine: script
       scope: ["*.py"]
       severity: error
       script: "grep -n TODO {file} && exit 1 || exit 0"
   ```
2. Start Reasonix in the project and ask it to *"add a `# TODO: fix later` comment
   to some Python file."*
3. **Expect:** the `edit_file`/`write_file` is **refused** before it lands. The
   model receives an `AGENTIC LINT -- blocked` message naming `no-todo-comments`,
   and self-corrects (drops the TODO, or asks). Confirm the file on disk never got
   the TODO.

## Smoke 2 — semantic soft-gate (the one-time pause)

1. Add a semantic rule (one that needs judgement, not grep):
   ```yaml
     no-silent-except:
       description: "An `except` block must not swallow the error silently (no bare pass)."
       engine: semantic
       scope: ["*.py"]
       severity: error
   ```
2. Ask Reasonix to *"wrap this call in a try/except that just passes on error."*
3. **Expect, in order:**
   - First attempt is **paused** with `AGENTIC LINT SEMANTIC EVALUATION REQUIRED`
     plus a `diff-id` and instructions.
   - The model dispatches the evaluator —
     `run_skill(name="bully-evaluator", ...)` — judges the diff, then logs a verdict:
     `python3 -m bully --log-verdict --diff-id <id> --rule no-silent-except --verdict <pass|violation> --file <path>`.
   - On `violation`: it fixes the code and the corrected edit (a new diff) sails
     through. On `pass`: re-issuing the **identical** edit hits the verdict cache and
     is admitted. Either way the loop terminates — no infinite re-pausing.

## Smoke 3 — session rule (cross-edit gate)

1. Add a session rule (evaluated over the whole turn's changed-set). Session
   rules use `when.changed_any` to select into the check and `require.changed_any`
   to declare the paths that must also change:
   ```yaml
     changelog-with-src:
       description: "Editing src/ in a turn requires touching CHANGELOG.md too."
       engine: session
       severity: error
       when:
         changed_any: ["src/**"]
       require:
         changed_any: ["CHANGELOG.md"]
   ```
   (See `docs/rule-authoring.md` for the full session-rule shape.)
2. Ask Reasonix to change a file under `src/` and **stop** without touching the
   changelog.
3. **Expect:** at `Stop` you get a **notify** (`Stop` can't block), and your **next
   prompt** is **gated** — `UserPromptSubmit` exits 2 with
   `AGENTIC LINT -- unsatisfied session rules` until you add the CHANGELOG entry (or
   amend the rule with `bully-author`).

## After the smoke

- Inspect telemetry: `.bully/log.jsonl` should show the block / evaluate / verdict /
  session records (`docs/telemetry.md` decodes the record types).
- `python3 -m bully doctor` should be all `[OK]`/benign `[WARN]`.
- If all three smokes behaved as described, the live behavior matches the unit
  suite — safe to promote `1.0.0-rc.1` → `1.0.0`.
````

- [ ] **Step 2: Verify the doc renders and links resolve**

Run: `python3 -c "import pathlib,sys; t=pathlib.Path('docs/live-smoke.md').read_text(); sys.exit(0 if ('reasonix-hook' in t and 'bully-evaluator' in t and t.count('```')%2==0) else 1)"`
Expected: exit 0 (key commands present, code fences balanced).

- [ ] **Step 3: Commit**

```bash
git add docs/live-smoke.md
git commit -m "W2 T3: manual live-smoke runbook (deterministic block, semantic soft-gate, session gate)"
```

---

### Task 4: Release prep — version bump, README, doc pass

**Files:**
- Modify: `pyproject.toml` (line 7)
- Create: `README.md`
- Modify: `CLAUDE.md` (Status section)
- Modify: `REASONIX.md` (add live-smoke pointer)
- **Do NOT touch:** `src/bully/__init__.py` `BULLY_VERSION` (see invariants).

- [ ] **Step 1: Bump the distribution version**

Edit `pyproject.toml` line 7:

```toml
version = "1.0.0-rc.1"
```

- [ ] **Step 2: Confirm BULLY_VERSION is untouched**

Run: `grep -n 'BULLY_VERSION' src/bully/__init__.py`
Expected: still `BULLY_VERSION = "0.8.6"` (engine/telemetry tag — leave it).

Run: `git diff --stat src/bully/`
Expected: **empty** — no engine files changed in W2.

- [ ] **Step 3: Write the README**

Create `README.md`:

```markdown
# bully-reasonix

An **agentic linter** for [Reasonix](https://github.com/esengine/DeepSeek-Reasonix),
DeepSeek's Go terminal agent. It runs as a Reasonix hook and lints every *pending*
edit the agent makes — blocking a bad change **before** it lands and feeding the
reason back to the model so it self-corrects.

This is a port of [`bully`](../bully) (originally a Claude Code plugin). The
evaluation engine is reused unchanged; only the thin layer bridging harness ↔
engine is Reasonix-native. Python, stdlib-only.

## What it does

- **Deterministic rules** (`engine: script` / `ast`) — run on every edit in a
  `PreToolUse` hook. An `error` violation blocks the edit (exit 2); the agent gets
  an `AGENTIC LINT -- blocked` message and fixes it.
- **Semantic rules** (`engine: semantic`) — judgement calls. The edit pauses once;
  the agent evaluates the diff via the `bully-evaluator` subagent, logs a verdict,
  and the clean re-issue is admitted (session verdict cache breaks the loop).
- **Session rules** (`engine: session`) — invariants across a whole turn's
  changed-set (e.g. "touching `src/` requires a CHANGELOG entry"), enforced at the
  next prompt.

## Install

```bash
pip install -e ".[dev]"        # or: export PYTHONPATH="$PWD/src"
python3 -m bully doctor        # verify wiring + skill discovery
```

In another project, point its `reasonix.toml` `[skills] paths` at this repo's
`skills/`, wire the hooks in `.reasonix/settings.json` (see this repo's copy), and
add a `.bully.yml`. `bully doctor` walks you through any gaps.

## Quickstart

1. Create rules: `/bully-init` (or hand-write `.bully.yml` — see
   `docs/rule-authoring.md` and `examples/rules/`).
2. Trust the config: `python3 -m bully trust`.
3. Work normally in Reasonix. Bully interrupts only when a rule fires; the playbook
   for those interruptions is **`REASONIX.md`** and the `bully` skill.

## Develop

```bash
bash scripts/lint.sh           # ruff + shellcheck + pytest + hook dogfood
```

Manual end-to-end check against a real Reasonix + DeepSeek key:
`docs/live-smoke.md`.

## Layout

- `src/bully/` — engine (copied verbatim) + the Reasonix seam
  (`harness/reasonix.py`, `cli/reasonix_hook.py`, `diff/pending.py`,
  `state/verdict_cache.py`).
- `skills/` — six skills: `bully`, `bully-init`, `bully-author`, `bully-review`
  (inline) and `bully-evaluator`, `bully-scheduler` (`runAs: subagent`).
- `docs/` — `rule-authoring.md`, `telemetry.md`, `live-smoke.md`, and the port
  design under `docs/superpowers/`.
```

- [ ] **Step 4: Update CLAUDE.md status**

In `CLAUDE.md`, replace the `- **Next:** …` line at the end of the `## Status`
section:

Old:
```markdown
- **Next:** W2 integration — dogfood script (`scripts/lint.sh` analog), manual live-smoke doc, release prep.
```

New:
```markdown
- **W2 done (port complete):** `scripts/{dogfood,lint}.sh` (the dogfood drives the real `reasonix-hook` subprocess over crafted PreToolUse payloads — spec §9), `docs/live-smoke.md` manual runbook, `README.md`, version `1.0.0-rc.1`. All milestones merged. Remaining before `1.0.0`: run the manual live smoke against a real Reasonix + DeepSeek instance (`docs/live-smoke.md`).
```

- [ ] **Step 5: Pass over REASONIX.md**

In `REASONIX.md`, append a pointer to the live-smoke runbook in the final
Diagnostics line. Replace:

```markdown
Diagnostics: `python3 -m bully doctor`. Telemetry: `.bully/log.jsonl` (see
`docs/telemetry.md`).
```

with:

```markdown
Diagnostics: `python3 -m bully doctor`. Telemetry: `.bully/log.jsonl` (see
`docs/telemetry.md`). End-to-end check: `docs/live-smoke.md`.
```

- [ ] **Step 6: Verify nothing else drifted**

Run: `git diff --stat`
Expected: only `pyproject.toml`, `README.md`, `CLAUDE.md`, `REASONIX.md` changed.
**No `src/bully/` files in the diff.**

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml README.md CLAUDE.md REASONIX.md
git commit -m "W2 T4: release prep -- v1.0.0-rc.1, README, CLAUDE.md/REASONIX.md pass"
```

---

### Task 5: W2 acceptance gate — full quality run

**Files:** none (verification only).

- [ ] **Step 1: Run the whole gate**

Run: `bash scripts/lint.sh`
Expected: every section green, ending in `All checks passed.` Specifically: ruff
clean, `ruff format --check` clean, pytest reports **all** tests passing (the prior
72 plus the new dogfood/lint-script tests), and `dogfood OK`.

- [ ] **Step 2: Confirm the engine invariant held across the whole wave**

Run: `git diff 325edb9 -- src/bully/ | head`
Expected: **empty** — W2 changed nothing under `src/bully/` (doctor was already
rewritten in M4; W2 adds only scripts, docs, tests, and root product files).

- [ ] **Step 3: Confirm the tree is clean and the version is set**

Run: `git status --short && grep '^version' pyproject.toml`
Expected: clean working tree, `version = "1.0.0-rc.1"`.

W2 is complete. Hand back to the controller for the opus milestone review, then
merge + push. (Tagging `v1.0.0-rc.1` and the GitHub release are a separate,
user-initiated step — not part of this plan.)

---

## Self-Review

**1. Spec coverage (§10 W2 — "assemble; pytest green; dogfood + (manual) live smoke; write REASONIX.md; update CLAUDE.md; first release commit"):**
- assemble + pytest green → Task 5 (`scripts/lint.sh` runs the full suite).
- dogfood → Tasks 1 & 2 (`dogfood.sh` is the spec §9 "`reasonix-hook` over a crafted payload"; `lint.sh` is the §9 "ruff + pytest + dogfood" analog).
- manual live smoke → Task 3 (`docs/live-smoke.md`, documented + non-gating per §9).
- REASONIX.md → already shipped in M4; Task 4 Step 5 does the required pass.
- update CLAUDE.md → Task 4 Step 4.
- first release commit → Task 4 Step 7 (version `1.0.0-rc.1`, README).

**2. Placeholder scan:** every created file (`dogfood.sh`, `lint.sh`, both test files, `live-smoke.md`, `README.md`) and every edit (version line, CLAUDE.md/REASONIX.md replacements) is given in full. No TBD/TODO/"add error handling" stubs.

**3. Type/name consistency:** the rule id `no-forbidden`, the `BULLY_TRUST_ALL=1` bypass, the `reasonix-hook` verb, the `--log-verdict --diff-id` flag form, and the `python3 -m bully` fallback all match the actual source (`cli/__init__.py`, `cli/reasonix_hook.py`, `harness/reasonix.py`, `state/trust.py`). The dogfood payload shape (`event`/`cwd`/`toolName`/`toolArgs` with `path`/`old_string`/`new_string`/`content`) matches `edit_event_from_payload` and `tests/test_reasonix_hook.py` exactly.

**4. Invariant guard:** the plan explicitly forbids touching `BULLY_VERSION` and verifies `git diff src/bully/` is empty (Task 4 Step 6, Task 5 Step 2), preserving the byte-identical engine the M4 review protected.
