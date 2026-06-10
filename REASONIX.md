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
`docs/telemetry.md`). End-to-end check: `docs/live-smoke.md`.
