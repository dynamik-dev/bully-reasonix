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


def test_bully_skill_runs_the_soft_gate_loop():
    text = _read("skills/bully/SKILL.md")
    fm = _frontmatter(text)
    assert fm.get("name") == "bully"
    assert "runas" not in fm  # inline skill
    body = text.split("---\n", 2)[2]
    _assert_no_claude_isms(text, "skills/bully")
    assert "AGENTIC LINT -- blocked. Fix these before proceeding:" in body
    assert "AGENTIC LINT SEMANTIC EVALUATION REQUIRED" in body
    assert 'run_skill(name="bully-evaluator"' in body
    assert "--log-verdict --diff-id" in body
    assert "prior verdict" in body
    assert "AGENTIC LINT -- unsatisfied session rules gate this turn" in body


def test_bully_scheduler_is_a_subagent_skill():
    text = _read("skills/bully-scheduler/SKILL.md")
    fm = _frontmatter(text)
    assert fm.get("name") == "bully-scheduler"
    assert fm.get("runas") == "subagent"
    assert "bash" in fm.get("allowed-tools", "")
    assert "model" not in fm  # routed via reasonix.toml [agent] subagent_models
    body = text.split("---\n", 2)[2]
    _assert_no_claude_isms(text, "skills/bully-scheduler")
    assert "python3 -m bully.semantic.analyzer" in body
    assert "bully-scheduler:" in body  # PR-title prefix contract


def test_bully_init_is_reasonix_native():
    text = _read("skills/bully-init/SKILL.md")
    assert _frontmatter(text).get("name") == "bully-init"
    _assert_no_claude_isms(text, "skills/bully-init")
    assert "PreToolUse" in text
    assert "reasonix-hook" in text  # offers the hooks wiring block
    assert "[skills]" in text       # offers the reasonix.toml paths entry
    assert 'command -v bully 2>/dev/null || echo "python3 -m bully"' in text


def test_bully_author_is_reasonix_native():
    text = _read("skills/bully-author/SKILL.md")
    assert _frontmatter(text).get("name") == "bully-author"
    _assert_no_claude_isms(text, "skills/bully-author")
    assert "PreToolUse" in text
    assert 'command -v bully 2>/dev/null || echo "python3 -m bully"' in text
    assert "scripts/dogfood.sh" not in text
    assert "--print-prompt" in text  # fixture protocol survived the port
