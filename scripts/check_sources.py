#!/usr/bin/env python3
"""Turn a published build red when a source was reused for too long or dropped.

Runs after publication, so routers already have the new feed when this fails.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auto_ir_ranges.generator import STALE_ALERT_DAYS  # noqa: E402


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def check(report: dict) -> tuple[list[str], list[str]]:
    """Return (GitHub warning lines, plain-language failures)."""
    observed = _time(report["observed_at"])
    notices, failures = [], []
    for name, entry in sorted(report.get("source_status", {}).items()):
        if entry["status"] == "fresh":
            continue
        stale = observed - _time(entry["last_fresh"]) if entry["last_fresh"] else None
        age = "no earlier fresh copy on record" if stale is None else f"last fresh {stale.days} day(s) ago ({entry['last_fresh']})"
        reason = entry["reason"] or "no reason recorded"
        if entry["status"] == "reused":
            notices.append(f"::warning::{name} reused from an earlier build; {age}; failure: {reason}")
            if stale > timedelta(days=STALE_ALERT_DAYS):
                failures.append(f"{name} has been stale for {stale.days} days and is still serving its old ranges (failure: {reason}).")
        else:
            notices.append(f"::warning::{name} dropped from the feed; {age}; failure: {reason}")
            failures.append(f"{name} is missing from the feed: no usable copy, {age} (failure: {reason}).")
    notices += [f"::warning::{warning}" for warning in report.get("warnings", [])]
    return notices, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage", type=Path, help="published coverage.json")
    args = parser.parse_args(argv)
    notices, failures = check(json.loads(args.coverage.read_bytes()))
    for line in notices:
        print(line)
    if failures:
        print("The feed was published, but these sources need attention:")
        for line in failures:
            print(f"- {line}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
