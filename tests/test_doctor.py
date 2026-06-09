# tests/test_doctor.py
"""M4: doctor rewritten for .reasonix wiring + reasonix skill discovery."""
import json

import pytest

from bully.cli.doctor import (
    check_python_version,
    cmd_doctor,
    match_covers_edit_tools,
    read_skills_paths,
)

HOOK_CMD = "python3 -m bully reasonix-hook"

FULL_HOOKS = {
    "hooks": {
        "PreToolUse": [{"match": "edit_file|write_file|multi_edit", "command": HOOK_CMD}],
        "Stop": [{"command": HOOK_CMD}],
        "UserPromptSubmit": [{"command": HOOK_CMD}],
        "SessionStart": [{"command": HOOK_CMD}],
        "SubagentStop": [{"command": HOOK_CMD}],
    }
}

ALL_SKILLS = (
    "bully-evaluator", "bully", "bully-init",
    "bully-author", "bully-review", "bully-scheduler",
)

SCRIPT_RULE = (
    "schema_version: 1\n"
    "rules:\n"
    "  no-x:\n"
    '    description: "No X."\n'
    "    engine: script\n"
    '    scope: ["*.py"]\n'
    "    severity: error\n"
    '    script: "grep -n X {file} && exit 1 || exit 0"\n'
)

SESSION_RULE = (
    "  changelog-updated:\n"
    '    description: "src changes need a changelog entry."\n'
    "    engine: session\n"
    "    severity: error\n"
    "    when:\n"
    "      changed_any:\n"
    '        - "src/**"\n'
    "    require:\n"
    "      changed_any:\n"
    '        - "CHANGELOG.md"\n'
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _project(parent, hooks=FULL_HOOKS, skills=ALL_SKILLS, session_rule=False):
    root = parent / "proj"
    skills_dir = root / ".reasonix" / "skills"
    skills_dir.mkdir(parents=True)
    if hooks is not None:
        (root / ".reasonix" / "settings.json").write_text(json.dumps(hooks))
    for name in skills:
        d = skills_dir / name
        d.mkdir()
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\nbody\n")
    config = SCRIPT_RULE + (SESSION_RULE if session_rule else "")
    (root / ".bully.yml").write_text(config)
    return root


def test_doctor_passes_fully_wired_project(tmp_path, home, capsys):
    root = _project(tmp_path)
    assert cmd_doctor(root) == 0
    out = capsys.readouterr().out
    assert "[FAIL]" not in out
    assert "[OK] PreToolUse hook wired" in out
    assert "[OK] skill bully-evaluator at" in out


def test_doctor_fails_without_pretooluse_hook(tmp_path, home, capsys):
    hooks = {"hooks": {k: v for k, v in FULL_HOOKS["hooks"].items() if k != "PreToolUse"}}
    root = _project(tmp_path, hooks=hooks)
    assert cmd_doctor(root) == 1
    assert "[FAIL] no PreToolUse hook" in capsys.readouterr().out


def test_doctor_fails_when_match_misses_an_edit_tool(tmp_path, home, capsys):
    hooks = json.loads(json.dumps(FULL_HOOKS))
    hooks["hooks"]["PreToolUse"][0]["match"] = "edit_file"
    root = _project(tmp_path, hooks=hooks)
    assert cmd_doctor(root) == 1
    assert "does not cover" in capsys.readouterr().out


def test_session_events_warn_without_session_rules_fail_with(tmp_path, home, capsys):
    pre_only = {"hooks": {"PreToolUse": FULL_HOOKS["hooks"]["PreToolUse"]}}
    root = _project(tmp_path, hooks=pre_only)
    assert cmd_doctor(root) == 0
    out = capsys.readouterr().out
    assert "[WARN] Stop hook not wired" in out
    assert "[WARN] UserPromptSubmit hook not wired" in out
    assert "[WARN] SessionStart hook not wired" in out
    assert "[WARN] SubagentStop hook not wired" in out

    parent = tmp_path / "with-session-rule"
    parent.mkdir()
    root2 = _project(parent, hooks=pre_only, session_rule=True)
    assert cmd_doctor(root2) == 1
    out = capsys.readouterr().out
    assert "[FAIL] Stop hook not wired" in out
    assert "[FAIL] UserPromptSubmit hook not wired" in out


def test_missing_evaluator_fails_missing_companion_warns(tmp_path, home, capsys):
    root = _project(tmp_path, skills=tuple(s for s in ALL_SKILLS if s != "bully-evaluator"))
    assert cmd_doctor(root) == 1
    assert "[FAIL] skill bully-evaluator missing" in capsys.readouterr().out

    parent = tmp_path / "evaluator-only"
    parent.mkdir()
    root2 = _project(parent, skills=("bully-evaluator",))
    assert cmd_doctor(root2) == 0
    assert "[WARN] skill bully missing" in capsys.readouterr().out


def test_skills_found_via_reasonix_toml_paths(tmp_path, home, capsys):
    root = _project(tmp_path, skills=())
    ext = tmp_path / "elsewhere" / "skills"
    for name in ALL_SKILLS:
        d = ext / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\nbody\n")
    (root / "reasonix.toml").write_text(f'[skills]\npaths = ["{ext}"]\n')
    assert cmd_doctor(root) == 0
    assert "[FAIL]" not in capsys.readouterr().out


def test_read_skills_paths_handles_missing_file_and_lists(tmp_path):
    assert read_skills_paths(tmp_path / "nope.toml") == []
    p = tmp_path / "reasonix.toml"
    p.write_text('[skills]\npaths = ["skills", "~/more-skills"]\n\n[agent]\n')
    assert read_skills_paths(p) == ["skills", "~/more-skills"]


def test_match_covers_edit_tools():
    assert match_covers_edit_tools(None) is True
    assert match_covers_edit_tools("edit_file|write_file|multi_edit") is True
    assert match_covers_edit_tools("edit_file") is False
    assert match_covers_edit_tools("(((") is False


def test_check_python_version():
    ok, msg = check_python_version((3, 10))
    assert ok and msg == "[OK] Python 3.10"
    ok, msg = check_python_version((3, 9))
    assert not ok and msg.startswith("[FAIL] Python 3.9 < 3.10")
