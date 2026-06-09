#!/usr/bin/env bash
set -euo pipefail
# Single entry point for this repo's quality gate: ruff lint, format check,
# pytest, the ShellCheck pass (when installed), and the Reasonix hook dogfood.
# After `pip install -e ".[dev]"`, run: bash scripts/lint.sh

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

fail=0

echo "=> ruff check"
ruff check . || fail=1

echo "=> ruff format --check"
ruff format --check . || fail=1

if command -v shellcheck >/dev/null 2>&1; then
  echo "=> shellcheck"
  shellcheck scripts/*.sh || fail=1
else
  echo "=> shellcheck (not installed -- skipping)"
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
