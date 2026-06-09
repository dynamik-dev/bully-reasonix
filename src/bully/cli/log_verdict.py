"""`--log-verdict`: append a semantic_verdict telemetry record."""

from __future__ import annotations

from datetime import datetime, timezone

from bully.state.telemetry import append_record, telemetry_path


def cmd_log_verdict(
    config_path: str | None,
    rule_id: str,
    verdict: str,
    file_path: str | None,
    diff_id: str | None = None,
) -> int:
    path = config_path or ".bully.yml"
    log_path = telemetry_path(path)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "type": "semantic_verdict",
        "rule": rule_id,
        "verdict": verdict,
    }
    if file_path:
        record["file"] = file_path
    if diff_id:
        record["diff_id"] = diff_id
    append_record(log_path, record)
    return 0
