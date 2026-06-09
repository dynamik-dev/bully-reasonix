"""Per-project telemetry log writer (`.bully/log.jsonl`).

Telemetry is mandatory plumbing, not a feature. Every project with a
`.bully.yml` gets a `.bully/` directory provisioned on first access; the
directory ignores itself via its own `.gitignore` so this local runtime state
never needs an entry in the repo root `.gitignore` and never lands in a
commit. The log is byte-capped so always-on growth stays bounded for every
user.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# Cap the append-only log so mandatory always-on telemetry can't grow without
# bound. On overflow we keep the newest records that fit under half the cap and
# drop the oldest -- recent history is what `bully-review` needs, and trimming
# to a low-water mark means rotation runs occasionally, not on every append.
MAX_LOG_BYTES = 5_000_000


def ensure_bully_dir(config_path: str) -> Path:
    """Provision `.bully/` next to the config and make it self-ignoring.

    Idempotent and cheap on the hot path: a `mkdir(exist_ok=True)` plus a
    `.gitignore` existence check, with a single write the first time. The
    directory carries its own `.gitignore` (`*`) so its contents stay out of
    git without touching the repo's root `.gitignore`.
    """
    tel_dir = Path(config_path).resolve().parent / ".bully"
    tel_dir.mkdir(exist_ok=True)
    gitignore = tel_dir / ".gitignore"
    if not gitignore.exists():
        try:
            gitignore.write_text("*\n", encoding="utf-8")
        except OSError:
            pass
    return tel_dir


def telemetry_path(config_path: str) -> Path:
    """Return the telemetry log path, provisioning `.bully/` if absent."""
    return ensure_bully_dir(config_path) / "log.jsonl"


def _rotate_if_needed(log_path: Path) -> None:
    """Trim the log to the newest records under half the cap, oldest-first.

    The newest record is always retained even if it alone exceeds the
    low-water mark, so a single large record can never blank the log.
    """
    cap = MAX_LOG_BYTES
    low_water = cap // 2
    try:
        if log_path.stat().st_size <= cap:
            return
        lines = log_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        line_bytes = len(line.encode("utf-8"))
        if kept and total + line_bytes > low_water:
            break
        total += line_bytes
        kept.append(line)
    kept.reverse()
    try:
        log_path.write_text("".join(kept), encoding="utf-8")
    except OSError:
        pass


def _append(log_path: Path, record: dict) -> None:
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        return
    _rotate_if_needed(Path(log_path))


def append_telemetry(
    log_path: Path,
    file_path: str,
    status: str,
    rule_records: list[dict],
    latency_ms: int,
) -> None:
    _append(
        log_path,
        {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "file": file_path,
            "status": status,
            "latency_ms": latency_ms,
            "rules": rule_records,
        },
    )


def append_record(log_path: Path, record: dict) -> None:
    _append(log_path, record)
