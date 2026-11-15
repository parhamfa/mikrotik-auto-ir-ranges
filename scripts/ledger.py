#!/usr/bin/env python3
"""Work the provider ledger: Iranian hosting companies and whether they publish a server list.

  stats                 companies per status
  next [N]              the companies to research next (default 15)
  sync --iptoasn PATH   add IR hosting-like networks from an ip2asn-v4.tsv.gz no company holds yet
  add --brand B --domain D [--evidence URL] [--note TEXT]
                        add a company found by market research
  leads --iptoasn PATH [--only ID]
                        print non-IR networks whose name matches a company (read-only)
  set ID --status S [--brand B] [--domain D]... [--evidence URL]... [--note TEXT] [--date YYYY-MM-DD]
                        record a research result (not list_in_use)
  propose-list ID --policy-id PID --url URL --evidence URL --rows N --broadest P --reason TEXT [--version 4|6] [--date D]
                        add the catalogue entry and mark the list in use, in both files
  withdraw-list --policy-id PID --reason TEXT [--date D]
                        undo propose-list
  probe TARGET          fetch https://DOMAIN/ips.txt and /cdnips.txt (or one https URL) and say whether it is a list
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auto_ir_ranges import coverage, providers  # noqa: E402
from auto_ir_ranges.errors import GenerationError  # noqa: E402
from auto_ir_ranges.generator import download  # noqa: E402

fetch = lambda url: download(url, attempts=1, timeout=30)  # noqa: E731  (one request per address; tests replace this)


def probe(target: str) -> None:
    urls = [target] if target.startswith("https://") else [f"https://{providers.normalise_domain(target)}/{name}" for name in ("ips.txt", "cdnips.txt")]
    for url in urls:
        try:
            info = providers.looks_like_list(fetch(url))
            print(f"{url}: {providers.format_list(info)}" if info else f"{url}: no list (not a list of ranges)")
        except (GenerationError, OSError, ValueError) as exc:
            reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            print(f"{url}: no list ({reason[reason.find('HTTP Error'):] if 'HTTP Error' in reason else reason})")


def write_both(args, policy: dict, ledger: dict) -> None:
    """Write catalogue and ledger, re-validate them, and restore both exact old bytes on any failure."""
    old = {path: path.read_bytes() for path in (args.policy, args.ledger)}
    try:
        args.policy.write_text(json.dumps(policy, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        args.ledger.write_text(providers.dump_ledger(ledger), encoding="utf-8")
        providers.validate_ledger(providers.load_ledger(args.ledger), coverage.load_policy(args.policy))
    except (GenerationError, OSError, ValueError):
        for path, data in old.items():
            path.write_bytes(data)
        raise


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
    add = commands.add_parser("add")
    add.add_argument("--brand", required=True)
    add.add_argument("--domain", required=True)
    add.add_argument("--evidence")
    add.add_argument("--note", default="")
    leads = commands.add_parser("leads")
    leads.add_argument("--iptoasn", type=Path, required=True)
    leads.add_argument("--only")
    setp = commands.add_parser("set")
    setp.add_argument("company_id")
    setp.add_argument("--status", required=True)
    setp.add_argument("--brand")
    setp.add_argument("--domain", action="append", default=[])
    setp.add_argument("--evidence", action="append", default=[])
    setp.add_argument("--note")
    setp.add_argument("--date", type=date.fromisoformat)
    propose = commands.add_parser("propose-list")
    propose.add_argument("company_id")
    propose.add_argument("--policy-id", required=True)
    propose.add_argument("--url", required=True)
    propose.add_argument("--evidence", required=True)
    propose.add_argument("--rows", type=int, required=True)
    propose.add_argument("--broadest", type=int, required=True)
    propose.add_argument("--reason", required=True)
    propose.add_argument("--version", type=int, choices=(4, 6), default=4)
    propose.add_argument("--date", type=date.fromisoformat)
    withdraw = commands.add_parser("withdraw-list")
    withdraw.add_argument("--policy-id", required=True)
    withdraw.add_argument("--reason", required=True)
    withdraw.add_argument("--date", type=date.fromisoformat)
    probe_parser = commands.add_parser("probe")
    probe_parser.add_argument("target")
    args = parser.parse_args(argv)
    try:
        if args.command == "probe":  # needs neither file, so it works while the ledger is mid-edit
            probe(args.target)
            return 0
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
        elif args.command == "add":
            entry = providers.add_company(ledger, args.brand, args.domain, args.evidence, args.note)
            providers.validate_ledger(ledger, policy)
            args.ledger.write_text(providers.dump_ledger(ledger), encoding="utf-8")
            print(f"added {entry['id']}")
        elif args.command == "set":
            entry = providers.set_company(ledger, args.company_id, args.status, args.brand, args.domain, args.evidence, args.note, args.date)
            providers.validate_ledger(ledger, policy)
            args.ledger.write_text(providers.dump_ledger(ledger), encoding="utf-8")
            print(f"updated {entry['id']}")
        elif args.command == "propose-list":
            providers.propose_list(policy, ledger, args.company_id, args.policy_id, args.url, args.evidence, args.rows, args.broadest,
                                   args.reason, args.version, args.date)
            write_both(args, policy, ledger)
            print(f"proposed {args.policy_id} for {args.company_id}; now run: python3 scripts/check_providers.py --only {args.policy_id}")
        elif args.command == "withdraw-list":
            entry = providers.withdraw_list(policy, ledger, args.policy_id, args.reason, args.date)
            write_both(args, policy, ledger)
            print(f"withdrew {args.policy_id} from {entry['id']}")
        elif args.command == "leads":
            found = providers.foreign_leads(ledger, gzip.decompress(args.iptoasn.read_bytes()).decode("utf-8"), args.only)
            for lead in found:
                print(f"{lead['company']} | AS{lead['asn']} | {lead['country']} | {lead['description']} | {lead['addresses']} addresses | matched \"{lead['matched']}\"")
            if not found:
                print("no leads")
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
