"""Refresh public discovery evidence; never edits the accepted catalogue or feed."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import ipaddress
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import catalogue, coverage, discovery
from .generator import SOURCE_URLS, _atomic_write, download


def save(path, value):
    raw = (json.dumps(value, sort_keys=True, ensure_ascii=False)+"\n").encode()
    _atomic_write(path, gzip.compress(raw, mtime=0) if path.suffix == ".gz" else raw)


def load(path, default):
    if not path.exists():
        return default
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


def refresh(args):
    now = datetime.now(timezone.utc)
    out, previous = args.output_dir, args.previous_dir or args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    policy = coverage.load_policy(args.policy)
    payloads = {v: (args.source_dir/f"iptoasn_ipv{v}").read_bytes() if args.source_dir else download(SOURCE_URLS[f"iptoasn_ipv{v}"]) for v in (4, 6)}
    nro = (args.source_dir/"nro_delegated").read_bytes() if args.source_dir else download(policy["registry_url"])
    _, ir_asns, _ = coverage.parse_delegated(nro, now=now)  # deliberately NO manual anchors
    index = discovery.asn_index(payloads, ir_asns)
    initial_leads = discovery.relationship_candidates(index, policy, version="pre-enrichment")
    rdap_cache = load(previous/"rdap-cache.json.gz", {})
    bootstrap = json.loads(download("https://data.iana.org/rdap/asn.json"))
    rdap = discovery.enrich(index, rdap_cache, bootstrap, lambda u: download(u, attempts=1, timeout=10), now=now, budget=args.rdap_budget, priority_asns={r["value"] for r in initial_leads})
    for operator in policy["operators"]:
        for asn in operator["asns"]:
            if asn in index:
                index[asn]["catalogue_aliases"] = operator.get("aliases", [])
                index[asn]["reviewed_websites"] = operator.get("websites", [])
    version = hashlib.sha256(b"".join(payloads.values())).hexdigest()
    leads = discovery.relationship_candidates(index, policy, version=version)
    sources, unsupported, errors = [], [], []
    # Preserve an incomplete source's previous candidates; failed adapters cannot erase leads.
    for repo, branch, license_id in (("v2fly/domain-list-community", "master", "MIT"), ("bootmortis/iran-hosted-domains", "main", "MIT"), ("Chocolate4U/Iran-v2ray-rules", "main", "GPL-3.0")):
        try:
            commit = json.loads(download(f"https://api.github.com/repos/{repo}/commits/{branch}"))["sha"]
            files = discovery.archive_files(download(f"https://codeload.github.com/{repo}/tar.gz/{commit}"))
            license_path = next(p for p in files if p.lower() in ("license", "license.md", "license.txt"))
            license_text = files[license_path].decode()
            if license_id == "MIT" and "Permission is hereby granted" not in license_text:
                raise ValueError("upstream MIT license changed; review required")
            if license_id == "GPL-3.0" and ("GNU GENERAL PUBLIC LICENSE" not in license_text or "Version 3" not in license_text):
                raise ValueError("upstream GPL license changed; review required")
            _atomic_write(out/"licenses"/(repo.split('/')[0]+".txt"), files[license_path])
            source = {"source": repo, "version": commit, "license": license_id, "license_url": f"https://github.com/{repo}/blob/{commit}/{license_path}", "url": f"https://github.com/{repo}/blob/{commit}", "lineage": "iran-community-domains" if repo.startswith(("v2fly/", "bootmortis/")) else "chocolate4u-provider-leads"}
            if repo.startswith("v2fly/"):
                rows, gaps = discovery.v2fly_rules(files, source)
                leads.extend(rows)
                unsupported.extend(gaps)
            elif repo.startswith("bootmortis/"):
                release = json.loads(download(f"https://api.github.com/repos/{repo}/releases/latest"))
                asset = next(a for a in release["assets"] if a["name"] == "domains.txt")
                raw = download(asset["browser_download_url"])
                source.update(release=release["tag_name"], asset_url=asset["browser_download_url"], sha256=hashlib.sha256(raw).hexdigest(), original_sources=["v2fly/domain-list-community", "ITO", "Enamad", "TCI", "IWMF", "bootmortis custom list"], lineage_note="Per-entry origin is not supplied; conservatively grouped with v2fly, not counted as independent confirmation.")
                for line in raw.decode().splitlines():
                    rule = discovery.domain_rule(line)
                    if not rule:
                        continue
                    # domains.txt entries are exact domain leads, except bare TLDs/patterns.
                    kind = "domain" if rule["kind"] == "suffix" and "." in rule["value"] else rule["kind"]
                    leads.append({"kind": kind, "value": rule["value"], "evidence": {"source": repo, "version": release["tag_name"], "url": asset["browser_download_url"], "lineage": source["lineage"], "license": license_id, "rule": line}})
                    if kind != "domain":
                        unsupported.append({**rule, "source": repo, "reason": "not an enumerable exact hostname"})
            else:
                # Discover provider inventories from the pinned upstream build script.
                # Fetch ONLY those provider files from a pinned release commit; the
                # compiled country list mixes private/injected/global-cloud inputs.
                script_path = "scripts/generate-domestic-cdn-ips.sh"
                script = files[script_path].decode()
                release_commit = json.loads(download(f"https://api.github.com/repos/{repo}/commits/release"))["sha"]
                source["release_commit"] = release_commit
                providers = {}
                for line in script.splitlines():
                    match = re.search(r"ir-cdn/([a-z0-9-]+)\.txt", line)
                    urls = re.findall(r"https://[^\s\"'<>]+", line)
                    if match and urls:
                        providers.setdefault(match[1], []).append(urls[0])
                if not providers:
                    raise ValueError("no provider inventories in upstream build script")
                for provider, origin_urls in providers.items():
                    path = f"text/{provider}.txt"
                    data = download(f"https://raw.githubusercontent.com/{repo}/{release_commit}/{path}")
                    proof = {**source, "version": release_commit, "url": f"https://github.com/{repo}/blob/{release_commit}/{path}", "path": path, "original_source_urls": origin_urls, "lineage": "provider-inventory:"+provider}
                    for origin in origin_urls:
                        leads.append({"kind": "provider", "value": origin, "evidence": proof})
                    for line in data.decode().splitlines():
                        line = line.split("#", 1)[0].strip()
                        if not line:
                            continue
                        try:
                            network = ipaddress.ip_network(line, strict=True)
                            if not network.is_global or network.is_multicast:
                                raise ValueError("non-public network lead")
                            leads.append({"kind": "network", "value": str(network), "evidence": proof})
                        except ValueError as exc:
                            unsupported.append({"source": repo, "path": path, "rule": line, "reason": str(exc)})
            sources.append(source)
        except Exception as exc:
            errors.append({"source": repo, "error": str(exc)})
    decisions = discovery.validate_decisions(load(args.decisions, {"schema": 1, "decisions": {}}))
    pool = discovery.update_pool(load(previous/"candidates.json.gz", {}), leads, decisions, now=now.isoformat())
    accepted = {"asn:"+a: o for o in policy["operators"] for a in o["asns"]}
    accepted.update({"domain:"+s["domain"]: s for s in policy["services"]})
    accepted.update({"provider:"+p["url"]: p for p in policy["providers"]})
    for ident, row in pool.items():
        if row.get("catalogue_reference") and ident not in accepted and row["state"] == "accepted":
            row["state"] = "pending"
            row["history"].append({"at": now.isoformat(), "reason": "removed from accepted collection catalogue"})
            row.pop("catalogue_reference", None)
    for ident, item in accepted.items():
        if ident in pool:
            pool[ident].update(state="accepted", catalogue_reference=item.get("id", item.get("domain")), catalogue_reviewed_on=item["reviewed_on"])
    report = {"schema": 1, "observed_at": now.isoformat(), "systematic_sources": {**{f"iptoasn_ipv{v}": {"url": SOURCE_URLS[f"iptoasn_ipv{v}"], "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)} for v, raw in payloads.items()}, "nro": {"url": policy["registry_url"], "sha256": hashlib.sha256(nro).hexdigest(), "bytes": len(nro)}}, "candidates": dict(Counter(r["state"] for r in pool.values())), "global_asns": len(index), "iran_anchors": sum(r["iran_anchor"] for r in index.values()), "sources": sources, "source_errors": errors, "rdap": rdap, "review_queue": discovery.review_queue(pool), "ageing_catalogue": catalogue.ageing(policy, today=now.date()), "unsupported_rules_count": len(unsupported), "universe_coverage_percent": None}
    for name, value in (("asn-index.json.gz", index), ("rdap-cache.json.gz", rdap_cache), ("candidates.json.gz", pool), ("unsupported-rules.json.gz", unsupported), ("report.json", report)):
        save(out/name, value)
    print(json.dumps({k: report[k] for k in ("candidates", "global_asns", "iran_anchors", "unsupported_rules_count", "source_errors")}))
    return 1 if errors else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("refresh")
    run.add_argument("--output-dir", type=Path, default=Path("build/discovery"))
    run.add_argument("--previous-dir", type=Path)
    run.add_argument("--source-dir", type=Path)
    run.add_argument("--policy", type=Path, default=coverage.POLICY_PATH)
    run.add_argument("--decisions", type=Path, default=Path("candidate-decisions.json"))
    run.add_argument("--rdap-budget", type=int, default=250)
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--directory", type=Path, default=Path("build/discovery"))
    search.add_argument("--limit", type=int, default=30)
    candidate = sub.add_parser("candidate")
    candidate.add_argument("id")
    candidate.add_argument("--directory", type=Path, default=Path("build/discovery"))
    args = parser.parse_args(argv)
    if args.command == "refresh":
        return refresh(args)
    if args.command == "candidate":
        print(json.dumps(load(args.directory/"candidates.json.gz", {})[args.id], indent=2))
    else:
        query = args.query.casefold()
        matches = [entry for entry in load(args.directory/"asn-index.json.gz", {}).values() if query in json.dumps(entry, ensure_ascii=False).casefold()]
        print(json.dumps({"total": len(matches), "results": matches[:args.limit]}, indent=2, ensure_ascii=False))
    return 0
