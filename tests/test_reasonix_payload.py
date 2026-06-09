# tests/test_reasonix_payload.py
from bully.harness.reasonix import edit_event_from_payload


def _payload(tool, args, cwd="/proj"):
    return {"event": "PreToolUse", "cwd": cwd, "toolName": tool, "toolArgs": args}


def test_edit_file_decodes_single_replace():
    ev = edit_event_from_payload(
        _payload("edit_file", {"path": "app.py", "old_string": "a", "new_string": "b"})
    )
    assert ev.tool == "edit_file"
    assert ev.file_path == "/proj/app.py"  # relative path resolved against cwd
    assert ev.is_write is False
    assert ev.content is None
    assert ev.edits == (("a", "b", False),)


def test_write_file_decodes_content():
    ev = edit_event_from_payload(_payload("write_file", {"path": "/abs/x.py", "content": "hi"}))
    assert ev.is_write is True
    assert ev.file_path == "/abs/x.py"  # absolute path left as-is
    assert ev.content == "hi"
    assert ev.edits == ()


def test_multi_edit_decodes_steps_with_replace_all():
    ev = edit_event_from_payload(
        _payload(
            "multi_edit",
            {
                "path": "m.py",
                "edits": [
                    {"old_string": "a", "new_string": "b"},
                    {"old_string": "c", "new_string": "d", "replace_all": True},
                ],
            },
        )
    )
    assert ev.edits == (("a", "b", False), ("c", "d", True))


def test_toolargs_accepts_json_string():
    # toolArgs is normally a nested object, but tolerate a JSON-encoded string.
    ev = edit_event_from_payload(
        _payload("edit_file", '{"path": "z.py", "old_string": "a", "new_string": "b"}')
    )
    assert ev.file_path == "/proj/z.py"


def test_non_edit_tool_returns_none():
    assert edit_event_from_payload(_payload("read_file", {"path": "z.py"})) is None


def test_missing_path_returns_none():
    assert (
        edit_event_from_payload(_payload("edit_file", {"old_string": "a", "new_string": "b"}))
        is None
    )
