# tests/test_bully_evaluator_skill.py
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills" / "bully-evaluator" / "SKILL.md"


def _frontmatter(text):
    assert text.startswith("---\n")
    fm = text.split("---\n", 2)[1]
    return {
        k.strip().lower(): v.strip()
        for k, v in (line.split(":", 1) for line in fm.splitlines() if ":" in line)
    }


def test_skill_file_exists_and_is_a_subagent():
    assert SKILL.is_file()
    fm = _frontmatter(SKILL.read_text())
    assert fm.get("name") == "bully-evaluator"
    assert fm.get("runas") == "subagent"  # frontmatter `runAs:` -> key `runas`
    assert fm.get("description")


def test_skill_body_defines_the_output_contract():
    body = SKILL.read_text().split("---\n", 2)[2]
    assert "TRUSTED_POLICY" in body and "UNTRUSTED_EVIDENCE" in body
    assert "VIOLATIONS:" in body and "NO_VIOLATIONS:" in body
    # no leftover Claude-isms
    assert "PostToolUse" not in body and "subagent_type" not in body
