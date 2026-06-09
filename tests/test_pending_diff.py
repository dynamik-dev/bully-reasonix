# tests/test_pending_diff.py
from bully.diff.pending import build_pending_diff_from, compute_after
from bully.harness.reasonix import EditEvent


def _edit(old, new, replace_all=False):
    return EditEvent("edit_file", "/x.py", False, None, ((old, new, replace_all),))


def test_compute_after_single_replace():
    assert compute_after("x = 1\n", _edit("x = 1", "x = 2")) == "x = 2\n"


def test_compute_after_write_uses_content():
    ev = EditEvent("write_file", "/x.py", True, "brand new\n", ())
    assert compute_after("old\n", ev) == "brand new\n"


def test_compute_after_multi_edit_in_order_and_replace_all():
    ev = EditEvent("multi_edit", "/x.py", False, None, (("a", "b", False), ("z", "Z", True)))
    assert compute_after("a a z z\n", ev) == "b a Z Z\n"  # first a->b once; all z->Z


def test_build_pending_diff_is_unified_with_real_paths():
    diff = build_pending_diff_from("/proj/app.py", "x = 1\n", "x = 2\n", is_write=False)
    assert "--- /proj/app.py.before" in diff
    assert "+++ /proj/app.py.after" in diff
    assert "-x = 1" in diff and "+x = 2" in diff


def test_build_pending_diff_write_returns_line_numbered_content():
    out = build_pending_diff_from("/proj/new.py", "", "line one\nline two\n", is_write=True)
    assert "1: line one" in out and "2: line two" in out
