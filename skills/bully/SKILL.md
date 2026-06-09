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
