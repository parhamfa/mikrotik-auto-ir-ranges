#!/usr/bin/env python3
"""Work the provider ledger: Iranian hosting companies and whether they publish a server list.

  stats                 companies per status
  next [N]              the companies to research next (default 15)
  sync --iptoasn PATH   add IR hosting-like networks from an ip2asn-v4.tsv.gz no company holds yet
"""
from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auto_ir_ranges import coverage, providers  # noqa: E402
from auto_ir_ranges.errors import GenerationError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger", type=Path, default=providers.LEDGER_PATH)
    parser.add_argument("--policy", type=Path, default=coverage.POLICY_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("stats")
    nxt = commands.add_parser("next")
    nxt.add_argument("count", nargs="?", type=int, default=15)
    sync = commands.add_parser("sync")
    sync.add_argument("--iptoasn", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        policy = coverage.load_policy(args.policy)
        ledger = providers.load_ledger(args.ledger)
        providers.validate_ledger(ledger, policy)
        if args.command == "stats":
            for status, count in providers.stats(ledger).items():
                print(f"{status:<14} {count}")
            print(f"{'total':<14} {len(ledger['companies'])}")
        elif args.command == "next":
            for company in providers.next_companies(ledger, args.count):
                print(providers.describe(company))
        else:
            before = len(ledger["companies"])
            added = providers.sync_iptoasn(ledger, gzip.decompress(args.iptoasn.read_bytes()).decode("utf-8"))
            providers.validate_ledger(ledger, policy)
            args.ledger.write_text(providers.dump_ledger(ledger), encoding="utf-8")
            print(f"added {added} networks ({len(ledger['companies']) - before} new companies)")
    except (GenerationError, OSError, EOFError, ValueError) as exc:
        print(f"ledger: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
