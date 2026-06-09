# bully-reasonix

An **agentic linter** for [Reasonix](https://github.com/esengine/DeepSeek-Reasonix),
DeepSeek's Go terminal agent. It runs as a Reasonix hook and lints every *pending*
edit the agent makes — blocking a bad change **before** it lands and feeding the
reason back to the model so it self-corrects.

This is a port of **bully** (originally a Claude Code plugin). The evaluation
engine is reused unchanged; only the thin layer bridging harness ↔ engine is
Reasonix-native. Python, stdlib-only.

## What it does

- **Deterministic rules** (`engine: script` / `ast`) — run on every edit in a
  `PreToolUse` hook. An `error` violation blocks the edit (exit 2); the agent gets
  an `AGENTIC LINT -- blocked` message and fixes it.
- **Semantic rules** (`engine: semantic`) — judgement calls. The edit pauses once;
  the agent evaluates the diff via the `bully-evaluator` subagent, logs a verdict,
  and the clean re-issue is admitted (a session verdict cache breaks the loop).
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
