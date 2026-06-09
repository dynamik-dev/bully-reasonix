# tests/test_verdict_cache.py
from bully.state.telemetry import append_record, telemetry_path
from bully.state.verdict_cache import cached_verdict, diff_id


def test_diff_id_stable_and_normalized():
    d1 = diff_id("/p/app.py", "+x = 1   \n+y = 2\n")   # trailing ws on line 1
    d2 = diff_id("/p/app.py", "+x = 1\n+y = 2\n")
    assert d1 == d2                      # trailing whitespace normalized away
    assert diff_id("/p/app.py", "+x = 1\n") != d1       # different content -> different id
    assert diff_id("/p/OTHER.py", "+x = 1\n+y = 2\n") != d1  # path participates


def test_cached_verdict_none_when_absent(tmp_path):
    (tmp_path / ".bully.yml").write_text("schema_version: 1\nrules: {}\n")
    assert cached_verdict(str(tmp_path / ".bully.yml"), "abc123", "rule-x") is None


def test_cached_verdict_returns_latest(tmp_path):
    cfg = tmp_path / ".bully.yml"
    cfg.write_text("schema_version: 1\nrules: {}\n")
    log = telemetry_path(str(cfg))
    append_record(log, {"type": "semantic_verdict", "diff_id": "d1", "rule": "r1", "verdict": "violation"})
    append_record(log, {"type": "semantic_verdict", "diff_id": "d1", "rule": "r1", "verdict": "pass"})
    append_record(log, {"type": "semantic_verdict", "diff_id": "d1", "rule": "r2", "verdict": "violation"})
    assert cached_verdict(str(cfg), "d1", "r1") == "pass"        # latest wins
    assert cached_verdict(str(cfg), "d1", "r2") == "violation"
    assert cached_verdict(str(cfg), "d2", "r1") is None          # diff_id must match
