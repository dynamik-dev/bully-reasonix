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
