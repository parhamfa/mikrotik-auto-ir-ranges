#!/usr/bin/env python3
"""Collect once, project two catalogues against the same public observations."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auto_ir_ranges import coverage, dns
from auto_ir_ranges.generator import SOURCE_URLS, download, generate_from_sources, publish_artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-policy', type=Path, required=True)
    parser.add_argument('--candidate-policy', type=Path, default=coverage.POLICY_PATH)
    parser.add_argument('--output-dir', type=Path, default=Path('build/projection'))
    args = parser.parse_args()
    policies = [coverage.load_policy(p) for p in (args.baseline_policy, args.candidate_policy)]
    # Source keys can have different URLs in two policies: cache by URL, not key.
    urls = set(SOURCE_URLS.values()) | {u for p in policies for u in coverage.source_urls(p).values()}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        downloaded = dict(executor.map(lambda u: (u, download(u)), sorted(urls)))
    union = {'services': list({s['domain']: s for p in policies for s in p['services']}.values()), 'dns_resolvers': {}}
    for p in policies:
        for name, endpoint in p['dns_resolvers'].items():
            if name in union['dns_resolvers'] and union['dns_resolvers'][name] != endpoint:
                raise ValueError('resolver endpoint changed; use different resolver IDs for a comparable projection')
            union['dns_resolvers'][name] = endpoint
    snapshot = json.loads(dns.collect(union))
    when = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for name, policy in zip(('baseline', 'candidate'), policies):
        target = args.output_dir/name
        sources = target/'sources'
        sources.mkdir(parents=True, exist_ok=True)
        payloads = {k: downloaded[url] for k, url in {**SOURCE_URLS, **coverage.source_urls(policy)}.items()}
        expected = {(s['domain'], resolver, t) for s in policy['services'] for resolver in policy['dns_resolvers'] for t in (1, 28)}
        payloads['dns_snapshot'] = json.dumps({**snapshot, 'queries': [r for r in snapshot['queries'] if dns.key(r) in expected]}).encode()
        for key, data in payloads.items():
            (sources/key).write_bytes(data)
        artifact = generate_from_sources(payloads, policy=policy, generated_at=when)
        publish_artifacts(artifact, target/'feed')
    print(f"Compare with: python scripts/compare.py {args.output_dir}/baseline/feed {args.output_dir}/candidate/feed")


if __name__ == '__main__':
    main()
