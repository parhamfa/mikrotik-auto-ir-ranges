#!/usr/bin/env python3
"""Test provider catalogue entries with plain code before they reach the daily build.

--offline checks that the ledger and the catalogue agree. Without it, each
selected provider list is also downloaded and must parse, overlap known
Iranian space (unless the entry carries an anchor_waiver) and stay under the
outside-space cap.
"""
from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))
from auto_ir_ranges import coverage, providers  # noqa: E402
from auto_ir_ranges.errors import GenerationError  # noqa: E402
from auto_ir_ranges.generator import download  # noqa: E402

PROVENANCE_URL = "https://raw.githubusercontent.com/parhamfa/mikrotik-auto-ir-ranges/data/provenance.json.gz"


def old_policy(ref: str) -> dict | None:
    """The catalogue at REF, or None when the ref or the file is missing."""
    try:
        shown = subprocess.run(["git", "show", f"{ref}:coverage-policy.json"], cwd=REPOSITORY_ROOT, capture_output=True, check=True)
        return json.loads(shown.stdout)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="only check that ledger and catalogue agree")
    parser.add_argument("--only", help="comma-separated provider ids")
    parser.add_argument("--changed-against", metavar="REF", help="only providers new or changed since this git ref")
    parser.add_argument("--anchor", type=Path, help="provenance.json.gz to take known Iranian space from")
    parser.add_argument("--policy", type=Path, default=coverage.POLICY_PATH)
    parser.add_argument("--ledger", type=Path, default=providers.LEDGER_PATH)
    args = parser.parse_args(argv)
    try:
        policy = coverage.load_policy(args.policy)
        ledger = providers.load_ledger(args.ledger)
        providers.validate_ledger(ledger, policy)
    except GenerationError as exc:
        print(f"FAIL {exc}")
        return 1
    print(f"OK ledger and catalogue agree ({len(ledger['companies'])} companies, {len(policy['providers'])} provider lists)")
    if args.offline:
        return 0

    selected = list(policy["providers"])
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        unknown = wanted - {p["id"] for p in selected}
        if unknown:
            print(f"FAIL unknown provider ids: {', '.join(sorted(unknown))}")
            return 1
        selected = [p for p in selected if p["id"] in wanted]
    if args.changed_against:
        changed = {p["id"] for p in providers.changed_providers(policy, old_policy(args.changed_against))}
        selected = [p for p in selected if p["id"] in changed]
    if not selected:
        print("OK no provider entries changed")
        return 0

    try:
        raw = args.anchor.read_bytes() if args.anchor else download(PROVENANCE_URL, timeout=60)
        anchor = providers.anchor_from_provenance(json.loads(gzip.decompress(raw)))
    except (GenerationError, OSError, EOFError, ValueError, KeyError) as exc:
        print(f"FAIL cannot load known Iranian space: {exc}")
        return 1
    results = []
    for provider in selected:
        ok, line = providers.check_provider(provider, lambda url: download(url, timeout=60), anchor)
        print(line)
        results.append(ok)
    print(providers.gate_summary(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
