# tests/test_reasonix_hook.py
import textwrap

from bully.cli.reasonix_hook import handle_payload


def _proj(tmp_path):
    (tmp_path / ".bully.yml").write_text(textwrap.dedent("""\
        schema_version: 1
        rules:
          no-forbidden:
            description: "No FORBIDDEN marker."
            engine: script
            scope: ["*.py"]
            severity: error
            script: "grep -n FORBIDDEN {file} && exit 1 || exit 0"
    """))
    f = tmp_path / "app.py"
    f.write_text("x = 1\n")
    return tmp_path, f


def _pre(tmp_path, args, tool="edit_file"):
    return {"event": "PreToolUse", "cwd": str(tmp_path), "toolName": tool, "toolArgs": args}


def test_blocks_edit_that_introduces_violation(tmp_path):
    proj, f = _proj(tmp_path)
    code, msg = handle_payload(
        _pre(proj, {"path": "app.py", "old_string": "x = 1", "new_string": "x = 1  # FORBIDDEN"})
    )
    assert code == 2
    assert "no-forbidden" in msg

    # the bad edit never landed — the real file is untouched
    assert "FORBIDDEN" not in f.read_text()
    # and the materialized temp file was cleaned up (glob on a missing dir is also empty)
    assert not list((proj / ".bully" / "tmp").glob("pending-*"))


def test_passes_clean_edit(tmp_path):
    proj, _ = _proj(tmp_path)
    code, msg = handle_payload(
        _pre(proj, {"path": "app.py", "old_string": "x = 1", "new_string": "x = 2"})
    )
    assert code == 0
    assert msg == ""
    assert not list((proj / ".bully" / "tmp").glob("pending-*"))  # temp cleaned up on pass too


def test_write_file_new_file_is_evaluated(tmp_path):
    proj, _ = _proj(tmp_path)
    code, msg = handle_payload(
        _pre(proj, {"path": "new.py", "content": "y = 1  # FORBIDDEN\n"}, tool="write_file")
    )
    assert code == 2
    assert "no-forbidden" in msg
    assert not (proj / "new.py").exists()  # the write never happened


def test_multi_edit_blocks_when_a_step_introduces_violation(tmp_path):
    proj, f = _proj(tmp_path)
    code, msg = handle_payload(
        _pre(
            proj,
            {"path": "app.py", "edits": [
                {"old_string": "x = 1", "new_string": "x = 2"},
                {"old_string": "x = 2", "new_string": "x = 2  # FORBIDDEN"},
            ]},
            tool="multi_edit",
        )
    )
    assert code == 2
    assert "no-forbidden" in msg
    assert "FORBIDDEN" not in f.read_text()  # real file untouched


def test_non_pretooluse_event_is_noop(tmp_path):
    proj, _ = _proj(tmp_path)
    assert handle_payload({"event": "Stop", "cwd": str(proj)}) == (0, "")


def test_non_edit_tool_is_noop(tmp_path):
    proj, _ = _proj(tmp_path)
    assert handle_payload(_pre(proj, {"path": "app.py"}, tool="read_file")) == (0, "")


def test_no_config_is_noop(tmp_path):
    # no .bully.yml anywhere above the file -> no-op, no crash
    (tmp_path / "loose.py").write_text("z = 1\n")
    code, _ = handle_payload(_pre(tmp_path, {"path": "loose.py", "old_string": "z = 1", "new_string": "z = 2"}))
    assert code == 0


def test_malformed_payload_fails_open(tmp_path):
    assert handle_payload({}) == (0, "")
