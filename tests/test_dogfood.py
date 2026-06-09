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
    r = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, cwd=str(REPO), timeout=60
    )
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "dogfood OK" in r.stdout
