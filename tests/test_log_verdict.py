# tests/test_log_verdict.py
from bully.cli.log_verdict import cmd_log_verdict
from bully.state.verdict_cache import cached_verdict


def test_log_verdict_writes_diff_id_record(tmp_path):
    cfg = tmp_path / ".bully.yml"
    cfg.write_text("schema_version: 1\nrules: {}\n")
    rc = cmd_log_verdict(str(cfg), "r1", "pass", str(tmp_path / "app.py"), diff_id="deadbeef")
    assert rc == 0
    # the verdict cache can now find it by (diff_id, rule)
    assert cached_verdict(str(cfg), "deadbeef", "r1") == "pass"


def test_log_verdict_without_diff_id_still_works(tmp_path):
    cfg = tmp_path / ".bully.yml"
    cfg.write_text("schema_version: 1\nrules: {}\n")
    assert cmd_log_verdict(str(cfg), "r1", "violation", None) == 0
