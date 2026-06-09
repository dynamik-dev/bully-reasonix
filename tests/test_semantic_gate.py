# tests/test_semantic_gate.py
import re
import textwrap

from bully.cli.log_verdict import cmd_log_verdict
from bully.cli.reasonix_hook import handle_payload


def _proj(tmp_path):
    # a SEMANTIC rule; an edit adding a real code line dispatches it (passes can't-match)
    (tmp_path / ".bully.yml").write_text(textwrap.dedent("""\
        schema_version: 1
        rules:
          no-bare-except:
            description: "Avoid bare 'except:'; catch specific exceptions."
            engine: semantic
            scope: ["*.py"]
            severity: error
    """))
    f = tmp_path / "app.py"
    f.write_text("def f():\n    return 1\n")
    return tmp_path, f


def _edit(proj):
    return {
        "event": "PreToolUse", "cwd": str(proj), "toolName": "edit_file",
        "toolArgs": {"path": "app.py", "old_string": "    return 1",
                     "new_string": "    try:\n        return 1\n    except:\n        pass"},
    }


def test_semantic_no_verdict_requests_eval(tmp_path):
    proj, _ = _proj(tmp_path)
    code, msg = handle_payload(_edit(proj))
    assert code == 2
    assert "SEMANTIC EVALUATION REQUIRED" in msg          # the evaluator payload is included
    assert "no-bare-except" in msg
    assert "run_skill" in msg and "bully-evaluator" in msg  # how to evaluate
    assert "--log-verdict" in msg and "--diff-id" in msg    # how to record


def test_semantic_loop_breaks_after_pass_verdict(tmp_path):
    proj, _ = _proj(tmp_path)
    code, msg = handle_payload(_edit(proj))
    assert code == 2
    did = re.search(r"--diff-id (\w+)", msg).group(1)
    # model evaluates -> all clean -> logs pass for the rule
    cmd_log_verdict(str(proj / ".bully.yml"), "no-bare-except", "pass", str(proj / "app.py"), diff_id=did)
    # re-issued identical edit is now allowed
    assert handle_payload(_edit(proj)) == (0, "")


def test_semantic_cached_violation_blocks(tmp_path):
    proj, _ = _proj(tmp_path)
    code, msg = handle_payload(_edit(proj))
    did = re.search(r"--diff-id (\w+)", msg).group(1)
    cmd_log_verdict(str(proj / ".bully.yml"), "no-bare-except", "violation", str(proj / "app.py"), diff_id=did)
    code2, msg2 = handle_payload(_edit(proj))
    assert code2 == 2
    assert "prior verdict" in msg2 and "no-bare-except" in msg2
