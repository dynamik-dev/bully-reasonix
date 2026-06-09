# tests/test_session_rules.py
"""M3: session changed-set recording, Stop notify, UserPromptSubmit gate."""
import json
import textwrap

from bully.cli.reasonix_hook import handle_payload


def _det_proj(tmp_path, severity="error"):
    (tmp_path / ".bully.yml").write_text(textwrap.dedent(f"""\
        schema_version: 1
        rules:
          no-forbidden:
            description: "No FORBIDDEN marker."
            engine: script
            scope: ["*.py"]
            severity: {severity}
            script: "grep -n FORBIDDEN {{file}} && exit 1 || exit 0"
    """))
    f = tmp_path / "app.py"
    f.write_text("x = 1\n")
    return tmp_path, f


def _pre(proj, args, tool="edit_file"):
    return {"event": "PreToolUse", "cwd": str(proj), "toolName": tool, "toolArgs": args}


def _recorded(proj):
    sf = proj / ".bully" / "session.jsonl"
    if not sf.exists():
        return []
    return [json.loads(line)["file"] for line in sf.read_text().splitlines() if line.strip()]


def test_allowed_edit_is_recorded(tmp_path):
    proj, f = _det_proj(tmp_path)
    code, _ = handle_payload(_pre(proj, {"path": "app.py", "old_string": "x = 1", "new_string": "x = 2"}))
    assert code == 0
    assert _recorded(proj) == [str(f)]


def test_blocked_edit_is_not_recorded(tmp_path):
    proj, _ = _det_proj(tmp_path)
    code, _ = handle_payload(
        _pre(proj, {"path": "app.py", "old_string": "x = 1", "new_string": "x = 1  # FORBIDDEN"})
    )
    assert code == 2
    assert _recorded(proj) == []


def test_warned_edit_is_recorded(tmp_path):
    # warning severity -> exit 1 -> the edit still lands, so it is part of the changed-set
    proj, f = _det_proj(tmp_path, severity="warning")
    code, _ = handle_payload(
        _pre(proj, {"path": "app.py", "old_string": "x = 1", "new_string": "x = 1  # FORBIDDEN"})
    )
    assert code == 1
    assert _recorded(proj) == [str(f)]
