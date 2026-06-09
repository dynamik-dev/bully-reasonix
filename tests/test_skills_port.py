# tests/test_skills_port.py
"""M4: ported skills, docs, and config artifacts -- Reasonix-native, no Claude-isms."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CLAUDE_ISMS = (
    "PostToolUse",
    "subagent_type",
    "plugins/cache",
    "CLAUDE_PLUGIN_ROOT",
    "additionalContext",
    "hooks.json",
    "pipeline/analyzer.py",
    "bully ack",
)


def _read(rel):
    return (ROOT / rel).read_text()


def _frontmatter(text):
    assert text.startswith("---\n")
    fm = text.split("---\n", 2)[1]
    return {
        k.strip().lower(): v.strip()
        for k, v in (line.split(":", 1) for line in fm.splitlines() if ":" in line)
    }


def _assert_no_claude_isms(text, where):
    for ism in CLAUDE_ISMS:
        assert ism not in text, f"{ism!r} leaked into {where}"


def test_ported_docs_exist_and_are_reasonix_native():
    for rel in ("docs/rule-authoring.md", "docs/telemetry.md"):
        text = _read(rel)
        _assert_no_claude_isms(text, rel)
        assert "Claude Code" not in text, rel
        assert "hook.sh" not in text, rel
    telemetry = _read("docs/telemetry.md")
    assert "python3 -m bully.semantic.analyzer" in telemetry
    assert "hook_fail_open" in telemetry
    assert "subagent_stop" in telemetry


def test_examples_catalog_ported():
    packs = sorted(p.name for p in (ROOT / "examples" / "rules").glob("*.yml"))
    assert packs == [
        "django.yml", "fastapi.yml", "go.yml", "nextjs.yml",
        "rails.yml", "react-ts.yml", "rust-cli.yml",
    ]
