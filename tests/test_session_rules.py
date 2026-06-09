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


from bully.cli.stop import reasonix_prompt_gate, reasonix_stop


def _session_proj(tmp_path):
    # NOTE: when/require must be block-style nested mappings — the stdlib
    # mini-YAML parser does not accept flow mappings.
    (tmp_path / ".bully.yml").write_text(textwrap.dedent("""\
        schema_version: 1
        rules:
          src-needs-tests:
            description: "Changes under src/ require a test change."
            engine: session
            severity: error
            when:
              changed_any: ['src/**']
            require:
              changed_any: ['tests/**']
    """))
    return tmp_path


def _write_session(proj, files):
    bd = proj / ".bully"
    bd.mkdir(exist_ok=True)
    (bd / "session.jsonl").write_text("".join(json.dumps({"file": f}) + "\n" for f in files))


def _warning_variant(proj):
    cfg = (proj / ".bully.yml").read_text().replace("severity: error", "severity: warning")
    (proj / ".bully.yml").write_text(cfg)


def test_stop_no_session_file_is_silent(tmp_path):
    proj = _session_proj(tmp_path)
    assert reasonix_stop(str(proj / ".bully.yml")) == (0, "")


def test_stop_satisfied_resets_changed_set(tmp_path):
    proj = _session_proj(tmp_path)
    _write_session(proj, ["src/auth.py", "tests/test_auth.py"])
    assert reasonix_stop(str(proj / ".bully.yml")) == (0, "")
    assert not (proj / ".bully" / "session.jsonl").exists()


def test_stop_error_violation_notifies_and_keeps_set(tmp_path):
    proj = _session_proj(tmp_path)
    _write_session(proj, ["src/auth.py"])
    code, msg = reasonix_stop(str(proj / ".bully.yml"))
    assert code == 1                                   # notify -- Stop can't block in Reasonix
    assert "src-needs-tests" in msg
    assert "gate the next prompt" in msg
    assert (proj / ".bully" / "session.jsonl").exists()  # kept for the prompt gate


def test_stop_warning_only_notifies_and_resets(tmp_path):
    proj = _session_proj(tmp_path)
    _warning_variant(proj)
    _write_session(proj, ["src/auth.py"])
    code, msg = reasonix_stop(str(proj / ".bully.yml"))
    assert code == 1
    assert "src-needs-tests" in msg
    assert not (proj / ".bully" / "session.jsonl").exists()  # warnings don't gate


def test_prompt_gate_blocks_on_unsatisfied_error_rule(tmp_path):
    proj = _session_proj(tmp_path)
    _write_session(proj, ["src/auth.py"])
    code, msg = reasonix_prompt_gate(str(proj / ".bully.yml"))
    assert code == 2
    assert "src-needs-tests" in msg


def test_prompt_gate_clean_passes(tmp_path):
    proj = _session_proj(tmp_path)
    _write_session(proj, ["src/auth.py", "tests/test_auth.py"])
    assert reasonix_prompt_gate(str(proj / ".bully.yml")) == (0, "")


def test_prompt_gate_warning_only_passes(tmp_path):
    proj = _session_proj(tmp_path)
    _warning_variant(proj)
    _write_session(proj, ["src/auth.py"])
    assert reasonix_prompt_gate(str(proj / ".bully.yml")) == (0, "")
