#!/usr/bin/env python3
"""Exact projected address-space changes between two validated feed generations."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auto_ir_ranges.coverage import subtract_networks
from auto_ir_ranges.delivery import validate
from auto_ir_ranges.generator import parse_country_zone


def read(directory):
    if (directory/'manifest-v2.json').exists():
        return validate((directory/'manifest-v2.json').read_bytes(), lambda name: (directory/name).read_bytes())
    return {v: parse_country_zone((directory/f'ir-ipv{v}.zone').read_bytes(), v) for v in (4, 6)}


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('before', type=Path)
parser.add_argument('after', type=Path)
args = parser.parse_args()
before, after = read(args.before), read(args.after)
report = {}
for v in (4, 6):
    added = subtract_networks(after[v], before[v], v)
    removed = subtract_networks(before[v], after[v], v)
    report[f'ipv{v}'] = {'before_prefixes': len(before[v]), 'after_prefixes': len(after[v]), 'added_ranges': [str(n) for n in added], 'removed_ranges': [str(n) for n in removed], 'added_addresses': str(sum(n.num_addresses for n in added)), 'removed_addresses': str(sum(n.num_addresses for n in removed))}
print(json.dumps(report, indent=2))
