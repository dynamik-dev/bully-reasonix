# bully-reasonix

Port of [`../bully`](../bully) — an agentic linter that runs as a coding-harness hook — from Claude Code to **Reasonix**, DeepSeek's Go terminal agent (<https://github.com/esengine/deepseek-reasonix>).

## Sources of truth — read these, don't restate them here

- **What to port:** `../bully`. Start at `../bully/CLAUDE.md` (module map) and `../bully/docs/` (design, rule format, telemetry).
- **What to port _to_:** the Reasonix repo (`gh repo view esengine/DeepSeek-Reasonix`, branch `main-v2`). Hook contract → `internal/hook/{hook,runner}.go`; skill/subagent contract → `internal/skill/skill.go`; config → `docs/SPEC.md` + <https://esengine.github.io/DeepSeek-Reasonix/configuration.html>.

## The invariant

Bully's engine — Python, stdlib-only, two-phase pipeline (deterministic script/AST rules, then semantic subagent eval) — is harness-agnostic and **ports unchanged**. Reasonix hooks are shell `command`s, so it still runs as `python3 -m bully`; there is no Go rewrite. Only the thin layer bridging harness ↔ engine changes. Rewire the edges, not the engine.

## Porting map — the non-obvious deltas (Claude Code → Reasonix)

| Concern | bully | bully-reasonix |
|---|---|---|
| Block a bad edit | `PostToolUse`, exit 2 | **`PreToolUse`, exit 2** — in Reasonix only `PreToolUse`/`UserPromptSubmit` can block |
| ↳ consequence | diff read from disk (post-write) | fires _before_ the write → build the diff from the pending `ToolArgs`, not the file |
| Hook wiring | `hooks/hooks.json`, matcher `Edit\|Write` | `.reasonix/settings.json` `hooks` map; `match` is an anchored regex → `(edit_file\|write_file\|multi_edit)` |
| Hook stdin | `tool_name` + `tool_input.{old_string,new_string,content}` | `ToolName` + raw `ToolArgs` (pending edit: `path`/`file_path` + edit text) |
| Feedback to model | exit-2 stderr + `hookSpecificOutput.additionalContext` | the `PreToolUse` block `message` (fed back to the model) |
| Evaluator subagent | `Agent(subagent_type: bully-evaluator)` | `bully-evaluator` as a `runAs: subagent` skill, invoked via `run_skill`/`task` |
| Skills | `skills/*/SKILL.md` | **same `SKILL.md` format** — Reasonix scans `.claude`/`.reasonix`, so they migrate in nearly as-is |
| Manifest / env | `.claude-plugin/plugin.json`, `CLAUDE_PLUGIN_ROOT` | none — `reasonix.toml` `[skills] paths` + `subagent_models`; no plugin-root env var |

## Status

Target: Reasonix **Go line** (local CLI `1.4.0-rc.1`; contracts validated vs `v1.4.0`). Design: `docs/superpowers/specs/2026-06-09-bully-reasonix-port-design.md`; plans in `docs/superpowers/plans/`.

- **M1+M2 done:** deterministic `PreToolUse` block + **semantic soft-gate**. Deterministic `error` violations block the pending edit (exit 2, real file untouched). Semantic rules block once with a `SEMANTIC EVALUATION REQUIRED` payload; the model evaluates via the `bully-evaluator` subagent skill (`skills/`) and logs verdicts (`--log-verdict --diff-id`); the re-issued clean edit is allowed via the session verdict cache (`state/verdict_cache.py`). Wired in `.reasonix/settings.json`.
- **M3 done:** session rules — per-edit changed-set recording on the PreToolUse allow path, `Stop` notify (exit 1; Stop can't block), `UserPromptSubmit` exit-2 gate over the accumulated set, `SessionStart`/`SubagentStop` telemetry stamps, fail-open `hook_fail_open` records, and the verdict cache session-scoped on `session_init`.
- **Next:** M4 — skill ports (`bully`, `bully-init`, `bully-author`, `bully-review`, `bully-scheduler` as `runAs: subagent`), `doctor` rewrite for `.reasonix`, `reasonix.toml`, `REASONIX.md`.

> This file guides development _in Claude Code_. The shipped product's standing instructions belong in **`REASONIX.md`** (Reasonix's memory file — it does not auto-load `CLAUDE.md`).
