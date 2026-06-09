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


def test_stop_event_dispatches(tmp_path):
    proj = _session_proj(tmp_path)
    _write_session(proj, ["src/auth.py"])
    code, msg = handle_payload({"event": "Stop", "cwd": str(proj)})
    assert code == 1
    assert "src-needs-tests" in msg


def test_prompt_submit_event_gates(tmp_path):
    proj = _session_proj(tmp_path)
    _write_session(proj, ["src/auth.py"])
    code, msg = handle_payload({"event": "UserPromptSubmit", "cwd": str(proj)})
    assert code == 2
    assert "src-needs-tests" in msg


def test_session_start_stamps_session_init(tmp_path):
    proj = _session_proj(tmp_path)
    assert handle_payload({"event": "SessionStart", "cwd": str(proj)})[0] == 0
    assert '"session_init"' in (proj / ".bully" / "log.jsonl").read_text()


def test_subagent_stop_stamps_record(tmp_path):
    proj = _session_proj(tmp_path)
    assert handle_payload({"event": "SubagentStop", "cwd": str(proj)}) == (0, "")
    assert '"subagent_stop"' in (proj / ".bully" / "log.jsonl").read_text()


def test_event_without_config_is_noop(tmp_path):
    assert handle_payload({"event": "Stop", "cwd": str(tmp_path)}) == (0, "")
    assert handle_payload({"event": "UserPromptSubmit", "cwd": str(tmp_path)}) == (0, "")


def test_full_session_gate_loop(tmp_path):
    proj = _session_proj(tmp_path)
    (proj / "src").mkdir()
    (proj / "tests").mkdir()
    (proj / "src" / "auth.py").write_text("a = 1\n")
    (proj / "tests" / "test_auth.py").write_text("t = 1\n")

    def edit(path, old, new):
        return handle_payload({"event": "PreToolUse", "cwd": str(proj), "toolName": "edit_file",
                               "toolArgs": {"path": path, "old_string": old, "new_string": new}})

    # turn 1: edit src only -> recorded
    assert edit("src/auth.py", "a = 1", "a = 2")[0] == 0
    # Stop: violation -> notify, keep the set
    code, msg = handle_payload({"event": "Stop", "cwd": str(proj)})
    assert code == 1 and "src-needs-tests" in msg
    # next prompt: gated
    code, msg = handle_payload({"event": "UserPromptSubmit", "cwd": str(proj)})
    assert code == 2 and "src-needs-tests" in msg
    # the agent satisfies the rule by editing a test file
    assert edit("tests/test_auth.py", "t = 1", "t = 2")[0] == 0
    # prompt now passes; Stop is clean and resets the set
    assert handle_payload({"event": "UserPromptSubmit", "cwd": str(proj)}) == (0, "")
    assert handle_payload({"event": "Stop", "cwd": str(proj)}) == (0, "")
    assert not (proj / ".bully" / "session.jsonl").exists()


def test_nested_config_edit_still_feeds_cwd_session(tmp_path):
    # An edit under a nested .bully.yml must still land in the cwd config's
    # changed-set — the one Stop/UserPromptSubmit read (regression: split-brain).
    proj = _session_proj(tmp_path)
    sub = proj / "sub"
    (sub / "src").mkdir(parents=True)
    (sub / ".bully.yml").write_text("schema_version: 1\nrules: {}\n")
    (sub / "src" / "x.py").write_text("a = 1\n")
    code, _ = handle_payload({"event": "PreToolUse", "cwd": str(proj), "toolName": "edit_file",
                              "toolArgs": {"path": "sub/src/x.py", "old_string": "a = 1", "new_string": "a = 2"}})
    assert code == 0
    assert _recorded(proj) == [str(sub / "src" / "x.py")]
    assert not (sub / ".bully" / "session.jsonl").exists()
    code, msg = handle_payload({"event": "Stop", "cwd": str(proj)})
    assert code == 1 and "src-needs-tests" in msg


def test_write_file_is_recorded(tmp_path):
    proj, _ = _det_proj(tmp_path)
    code, _ = handle_payload(_pre(proj, {"path": "new.py", "content": "y = 1\n"}, tool="write_file"))
    assert code == 0
    assert _recorded(proj) == [str(proj / "new.py")]
