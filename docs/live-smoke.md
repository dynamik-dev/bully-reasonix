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
   (`when` and `require` each take a `changed_any` glob list — the shape shown
   above is the whole schema; `severity: error` is what makes it *gate* the next
   prompt rather than only notify.)
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
