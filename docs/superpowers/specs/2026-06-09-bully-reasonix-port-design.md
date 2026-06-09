# bully-reasonix — port design

- **Date:** 2026-06-09
- **Status:** approved (design), pending spec review → writing-plans
- **Source:** `../bully` (agentic linter, Claude Code plugin; Python, stdlib-only)
- **Target:** Reasonix **Go line** (rewrite, not the legacy 0.x TypeScript line). Local CLI pinned `1.4.0-rc.1`; contracts validated against tag `v1.4.0`.
- **Approach:** **A — reuse the engine, swap the harness edge** (see `../CLAUDE.md`).

## 1. Goal & scope

Port bully to full parity on Reasonix. Bully's evaluation engine is harness-agnostic and is reused **verbatim**; only the thin layer that couples it to a harness is rewritten for Reasonix.

**In scope (full parity):** deterministic engine (script + ast), semantic evaluation, session rules, telemetry, trust gate, the 4 skills (`bully`, `bully-init`, `bully-author`, `bully-review`), the 2 subagents (`bully-evaluator`, `bully-scheduler`), `doctor`.

**Out of scope:** Go rewrite of the engine; keeping Claude Code support (this is a reasonix-native port, not dual-harness); `bench/` (Anthropic-SDK benchmark harness — port later if wanted).

## 2. Validated Reasonix contracts (do not re-derive — cited from source)

- **Hook events** (`internal/hook/hook.go`): `PreToolUse, PostToolUse, UserPromptSubmit, Stop, PostLLMCall, SessionStart, SessionEnd, SubagentStop, Notification, PreCompact`.
- **Blocking rule:** `IsBlocking(e) = (e == PreToolUse || e == UserPromptSubmit)`. **`PostToolUse` and `Stop` cannot block.**
- **Output routing** (`internal/hook/runner.go` `handle`): every non-pass outcome → `notify` (user-facing). **Only a `DecisionBlock` message is returned to the agent (model-facing).** ⇒ the only way a tool-event hook reaches the *model* is a `PreToolUse` exit-2 block.
- **Message channel:** `FormatOutcome` uses **stderr** (falls back to stdout). Hook writes its block payload to **stderr**.
- **Verdict mapping** (`decideOutcome`): exit `0`=pass, exit `2` on a gating event=block, anything else=warn; **timeout on a gating event = block** (so a slow hook spuriously blocks).
- **Timeouts:** `PreToolUse`/`UserPromptSubmit` default **5000 ms**; others 30000 ms. Override per-hook via `timeout` (ms). We set `timeout` explicitly.
- **Stdin Payload JSON** (`hook.go` `Payload`): `{ event, cwd, toolName, toolArgs (raw JSON of the tool call), toolResult, prompt, lastAssistantText, turn, message, trigger, reasoning }`.
- **Tool matcher** (`MatchesTool`): auto-anchored `^(?:<match>)$`, regex on `toolName`; non-tool events always match. Edit tools to match: `edit_file|write_file|multi_edit`.
- **Settings** (`hook.go`): project `<root>/.reasonix/settings.json` (loaded **only when the project is trusted**), global `~/.reasonix/settings.json`. Shape: `{ "hooks": { "<Event>": [ { "match", "command", "description?", "timeout?", "cwd?" } ] } }`.
- **Built-in tools** (edit surface): `edit_file`, `write_file`, `multi_edit`; also `read_file, bash, ls, glob, grep, task, run_skill, todo_write, web_fetch`.
- **Skills** (`internal/skill/skill.go`): Markdown, `SKILL.md` (dir layout) or `<name>.md`; frontmatter `name, description, allowed-tools?, model?, effort?`; `runAs: inline | subagent`. Loader **scans `.reasonix`, `.agents`, `.agent`, `.claude`** under project + home — so bully's existing `skills/*/SKILL.md` migrate in nearly unchanged. Invoked by the model via `run_skill` or by the user via `/<name>`.
- **Subagents:** `task` tool, plus `runAs: subagent` skills (isolated child loop, returns only final answer). Per-skill model via frontmatter `model:` and/or `reasonix.toml` `subagent_models = { "<skill>" = "<model>" }`.
- **Config / memory:** `reasonix.toml` (project) / `~/.config/reasonix/config.toml`; `[skills] paths`. Project memory file is **`REASONIX.md`** (`AGENTS.md` fallback; `@path` imports). No `.claude-plugin` manifest, no `CLAUDE_PLUGIN_ROOT` env.

## 3. Architecture (Approach A)

**Naming:** dist `bully-reasonix`, import package stays **`bully`** → `python3 -m bully …` and skill/hook commands carry over unchanged.

**The seam** — all reasonix-specifics live in two new modules; the engine is untouched:

- `src/bully/harness/reasonix.py` — parse the Payload, decode `toolArgs` per tool into a normalized `EditEvent`, and render hook output (exit code + stderr message).
  - `EditEvent{ tool, file_path, old_string, new_string, is_write }`. Decoders: `edit_file`→`{path|file_path, old_string, new_string}`; `write_file`→`{path, content}` (`is_write=True`); `multi_edit`→`{path, edits[]}` (apply sequentially).
- `src/bully/cli/reasonix_hook.py` — one CLI verb `reasonix-hook` wired to **every** event in settings.json; it dispatches on `payload.event` (`PreToolUse` → pipeline+gate, `Stop`/`SessionStart`/`SubagentStop` → record/notify). Single command keeps `settings.json` trivial.

The existing `runtime/runner.run_pipeline` consumes an `EditEvent` + diff context with near-zero change.

## 4. Pre-write diff (the crux — and a net improvement)

`PreToolUse` fires **before** the write, so we have the true `before` (disk) and compute `after` from the pending edit — no post-write reconstruction, no synthetic line-number fallback bully needed.

- `src/bully/diff/pending.py` — `build_pending_diff(edit_event) -> diff_context`:
  - `before` = current file on disk (`""` if absent, i.e. a new `write_file`).
  - `after` = `edit_file`: `before` with first `old_string`→`new_string`; `write_file`: `content`; `multi_edit`: edits applied in order.
  - real file line numbers throughout; reuse `diff/analysis.py` excerpt/can't-match logic unchanged.
- Keep `diff/context.py` (bully's post-write builder) for reference/tests; `pending.py` is the reasonix path.

## 5. The one divergence: model-facing work must block

Principle forced by §2: **only `PreToolUse` (per-edit) and `UserPromptSubmit` (per-turn) reach the model.** Map accordingly.

### 5a. Deterministic error rules → hard block (unchanged UX)
Run script+ast rules in `PreToolUse`. Any `severity: error` violation → write the violation list to **stderr**, **exit 2**. The bad edit never lands; the message is fed to the model, which fixes and re-issues. Warnings → **exit 1** (non-2 nonzero = `warn`, not `block`) + stderr note, surfaced to the user via `notify`. **Exit 0 = pass is silent** (`handle` skips pass outcomes) — use it only when there is nothing to say.

### 5b. Semantic rules → soft-gate with a session verdict-cache
There is no non-blocking model channel, so semantic eval becomes a one-time **soft-gate**:

1. On `PreToolUse`, after deterministic rules pass, find in-scope semantic rules surviving the existing can't-match prefilters (whitespace/comment-only, pure-deletion-on-avoid). Compute a stable `diff_id = hash(file + normalized_diff)` (normalize: strip trailing whitespace, ignore pure reindent).
2. **Look up `(diff_id, rule)` in the session verdict-cache** (`state/verdict_cache.py`, reading `.bully/session.jsonl`):
   - **no verdict** → **exit 2**, stderr = the `SEMANTIC EVALUATION REQUIRED` payload (`semantic/payload.py`, unchanged) **+ instructions**: evaluate via the `bully-evaluator` skill (`run_skill`), `python3 -m bully log-verdict --diff-id <id> --rule <r> --verdict <pass|violation>`, then **re-apply the identical edit if clean / fix then apply if not**.
   - **verdict = pass** → exit 0 (allow). The model's re-issued identical edit → same `diff_id` → cache hit → passes. **Loop broken.**
   - **verdict = violation** → exit 2 with the recorded violation (a re-issued identical bad edit blocks again — correct).
3. A *fixed* edit is a new diff → new `diff_id` → evaluated fresh (and typically passes). Verdict cache is **session-scoped** so it never suppresses future sessions.

*Rejected alternative:* evaluate inside the hook by shelling a subagent — removes the loop but adds per-edit API latency/cost and hook re-entrancy; revisit only if the soft-gate proves noisy.

### 5c. Session rules (`engine: session`)
Accumulate the turn's changed-set via the `Stop` hook (record to `.bully/session.jsonl`, **notify** on violation — Stop can't block). For *gating* parity, also evaluate the accumulated session rules at the **next `UserPromptSubmit`** and exit 2 to block the new turn until satisfied (mirrors bully's Stop-block). `SubagentStop` records a window marker (as in bully).

## 6. Component reuse map

| Layer | Disposition |
|---|---|
| `config/`, `engines/`, `semantic/payload.py`, `state/{baseline,trust,telemetry}.py`, `diff/analysis.py`, `runtime/{runner,rule_runner}.py` | **copy verbatim** |
| `diff/context.py` | keep (reference); new `diff/pending.py` is the live path |
| `cli/hook_mode.py`, `hooks/` (`hook.sh`,`hooks.json`), `.claude-plugin/`, `bin/bully` Claude bits | **drop / replace** |
| `cli/reasonix_hook.py`, `harness/reasonix.py`, `diff/pending.py`, `state/verdict_cache.py` | **new** |
| `cli/{session,stop}.py` | adapt to reasonix Stop/SessionStart/SubagentStop + UserPromptSubmit gate |
| `cli/doctor.py` | rewrite checks for `.reasonix/settings.json` + skill/subagent discovery |
| `skills/{bully,bully-init,bully-author,bully-review}/SKILL.md` | copy; reword Claude-isms (Agent tool→`run_skill`/`task`; PostToolUse exit-2→PreToolUse block; `bully` cmd resolution; `.reasonix` paths) |
| `agents/{bully-evaluator,bully-scheduler}.md` | → `skills/<name>/SKILL.md` with `runAs: subagent` |
| `tests/` | port; swap Claude-payload tests for reasonix-Payload tests; add pending-diff + verdict-cache tests |

## 7. Repo layout

```
bully-reasonix/
  CLAUDE.md                      # dev orientation (this port). Updated in W2.
  REASONIX.md                    # product memory file (new, W2)
  pyproject.toml                 # dist bully-reasonix, package bully, scripts: bully = bully.cli:main
  reasonix.toml                  # [skills] paths, subagent_models (example/install guidance)
  .reasonix/settings.json        # PreToolUse/Stop/SessionStart/SubagentStop/UserPromptSubmit → reasonix-hook
  src/bully/…                    # engine (copied) + harness/, diff/pending.py, cli/reasonix_hook.py, state/verdict_cache.py
  skills/…                       # 6 skills (4 + 2 subagent)
  tests/…
  docs/superpowers/specs/…       # this spec
```

`.reasonix/settings.json` (the wiring):
```json
{ "hooks": {
  "PreToolUse":   [{ "match": "edit_file|write_file|multi_edit", "command": "python3 -m bully reasonix-hook", "timeout": 15000 }],
  "Stop":         [{ "command": "python3 -m bully reasonix-hook", "timeout": 15000 }],
  "UserPromptSubmit":[{ "command": "python3 -m bully reasonix-hook", "timeout": 10000 }],
  "SessionStart": [{ "command": "python3 -m bully reasonix-hook" }],
  "SubagentStop": [{ "command": "python3 -m bully reasonix-hook" }]
}}
```

## 8. Cross-cutting: trust, timeouts, errors

- **Trust:** reasonix only loads project hooks when the project is trusted; bully's own per-machine trust gate (`state/trust.py`) is retained for rule-script execution. `BULLY_TRUST_ALL=1` bypass kept for tests/CI.
- **Latency budget:** the hook must finish well under its `timeout` (a gating-event timeout = block). Deterministic+prefilter+cache-lookup only — no model call in-hook. Set `timeout: 15000`; keep ast-grep optional/lazily skipped.
- **Fail-open:** any internal hook error → exit 0 (never block the user on a bug); log to telemetry. A malformed payload → exit 0.

## 9. Testing strategy

- Port bully's 49 pytest files; engine-level tests pass ~unchanged.
- New: reasonix-Payload fixtures (construct the exact stdin JSON per §2) asserting exit code + stderr for block vs pass; `diff/pending.py` (edit/write/multi_edit, new-file); verdict-cache loop-break (gate → log-verdict pass → re-issue passes).
- `scripts/lint.sh` analog: ruff + pytest + dogfood (`python3 -m bully reasonix-hook` over a crafted payload).
- **Live smoke (manual, deferred):** needs `reasonix setup` + a DeepSeek key (none set now). Wire `.reasonix/settings.json`, `reasonix run` an edit that violates a rule, observe refusal. Documented, not gating CI.

## 10. Build plan (waves; W1 fans out to subagents)

- **W0 — foundation (sequential):** `git init`; scaffold layout; copy engine; `pyproject.toml`; define `EditEvent` + the `harness/reasonix.py` and `cli/reasonix_hook.py` skeletons (the seam interface the W1 agents code against).
- **W1 — parallel subagents:**
  - **①** `harness/reasonix.py` + `diff/pending.py` + `cli/reasonix_hook.py` dispatch + `.reasonix/settings.json`.
  - **②** semantic soft-gate + `state/verdict_cache.py` + `log-verdict --diff-id` + `cli/{stop,session}.py` (Stop record/notify, UserPromptSubmit gate).
  - **③** 4 skill ports (reword Claude-isms).
  - **④** 2 subagent-skill ports (`runAs: subagent`) + `reasonix.toml` + `doctor` rewrite.
  - **⑤** test port + reasonix-Payload/pending-diff/verdict-cache tests.
- **W2 — integration (sequential):** assemble; `pytest` green; dogfood + (manual) live smoke; write `REASONIX.md`; update `CLAUDE.md`; first release commit.

## 11. Residual risks (non-blocking)

- Semantic soft-gate UX (pause per semantically-relevant edit) — mitigated by prefilters + verdict-cache; revisit if noisy (telemetry will show).
- `diff_id` normalization must be stable across the gated and re-issued edit — covered by a dedicated test.
- `multi_edit` arg schema field names — confirm exact keys from `internal/skill/tools.go` in W1-①.
