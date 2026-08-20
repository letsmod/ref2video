"""The running spend log: one JSON line per successful generation."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_COST_LOG = Path("cost_log.jsonl")
LEGACY_COST_LOG = Path("seedance_cost_log.jsonl")


def append_cost_log(log_path: Path, entry: dict) -> float:
    """Append one JSON line to the cost log and return the new running total."""
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Carry the v1 log forward so the running total does not reset on upgrade.
    if not log_path.exists() and log_path == DEFAULT_COST_LOG and LEGACY_COST_LOG.exists():
        try:
            log_path.write_text(LEGACY_COST_LOG.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass

    # Running total is derived from the file rather than stored, so the log stays
    # correct if lines are edited or removed by hand.
    previous_total = 0.0
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                previous_total += float(json.loads(line).get("cost_usd") or 0.0)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue  # skip a corrupt line rather than lose the whole log

    entry["running_total_usd"] = round(previous_total + (entry.get("cost_usd") or 0.0), 6)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return entry["running_total_usd"]
