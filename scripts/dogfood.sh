#!/usr/bin/env bash
set -euo pipefail
# Dogfood the Reasonix hook end-to-end: feed crafted PreToolUse payloads to the
# real `bully reasonix-hook` entry point (stdin JSON -> exit code + stderr) and
# assert the deterministic gate blocks a violating edit and admits a clean one.
# Exercises the subprocess boundary the unit tests stop short of (spec §9).

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Run the repo's own bully via the module form with src on PYTHONPATH. We do
# NOT use any installed `bully` console script: a stale one may shadow PATH
# (e.g. a plugin-cached build predating `reasonix-hook`), and spec §9 calls for
# exercising `python3 -m bully reasonix-hook` specifically.
export PYTHONPATH="$REPO_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
BULLY=(python3 -m bully)

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
elif ! grep -q no-forbidden <<<"$HOOK_OUT"; then
  echo "FAIL: write_file block message missing rule id; got: $HOOK_OUT"; fail=1
elif [[ -e "$WORK/new.py" ]]; then
  echo "FAIL: blocked write_file created the file"; fail=1
else
  echo "ok: violating write_file blocked (exit 2, no-forbidden), no file created"
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
