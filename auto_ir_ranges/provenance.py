"""Public, generation-specific evidence. Never consumes evaluation-lab captures."""
import argparse
import gzip
import ipaddress
import json
from pathlib import Path

from .delivery import validate


def build_provenance(policy, layers, registry, origins, dns, manifest):
    document = {"schema": 1, "generated_at": manifest["generated_at"], "policy": policy,
                "sources": manifest["sources"], "layers": {k: [str(n) for n in ns] for k, (_, ns) in layers.items()},
                "registry_records": registry, "asn_origins": origins, "dns_records": dns,
                "feed_hashes": {str(v): manifest[f"ipv{v}"]["sha512"] for v in (4, 6)}}
    return gzip.compress((json.dumps(document, sort_keys=True)+"\n").encode(), mtime=0)


def explain(query, directory):
    document = json.loads(gzip.decompress((directory/"provenance.json.gz").read_bytes()))
    manifest = json.loads((directory/"manifest-v2.json").read_bytes())
    if document["generated_at"] != manifest["generated_at"]:
        raise ValueError("provenance belongs to a different generation time")
    if any(document["feed_hashes"][str(v)] != manifest[f"ipv{v}"]["sha512"] for v in (4, 6)):
        raise ValueError("provenance belongs to a different generation")
    policy = document["policy"]
    if query.upper().startswith("AS") or query.isdigit():
        asn = int(query.upper().removeprefix("AS"))
        operators = [o for o in policy["operators"] if str(asn) in o["asns"]]
        registrations = [r for r in document["registry_records"] if r["type"] == "asn" and int(r["start"]) <= asn < int(r["start"])+int(r["value"])]
        origins = [r for r in document["asn_origins"] if r["asn"] == asn]
        return {"query": f"AS{asn}", "generation": manifest["generation"], "selected": bool(operators or registrations or origins), "reviewed_operators": operators, "registry_evidence": registrations, "admitted_origins": origins, "source_snapshots": document["sources"], "note": "A service hosted on this ASN does not select the ASN."}
    address = ipaddress.ip_address(query)
    networks = validate((directory/"manifest-v2.json").read_bytes(), lambda p: (directory/p).read_bytes())
    layers = {name: [n for n in ns if ipaddress.ip_network(n).version == address.version and address in ipaddress.ip_network(n)] for name, ns in document["layers"].items()}
    layers = {k: v for k, v in layers.items() if v}
    origins = [r for r in document["asn_origins"] if ipaddress.ip_address(r["first"]).version == address.version and int(ipaddress.ip_address(r["first"])) <= int(address) <= int(ipaddress.ip_address(r["last"]))]
    registry = []
    for r in document["registry_records"]:
        if r["type"] == f"ipv{address.version}":
            start = ipaddress.ip_address(r["start"])
            size = int(r["value"]) if address.version == 4 else 2**(128-int(r["value"]))
            if int(start) <= int(address) < int(start)+size:
                registry.append(r)
    return {"query": str(address), "generation": manifest["generation"], "included": any(address in n for n in networks[address.version]), "covering_feed_prefixes": [str(n) for n in networks[address.version] if address in n], "contributing_layers": layers, "registry_evidence": registry, "asn_origins": origins, "reviewed_operators": [o for o in policy["operators"] if any(str(r["asn"]) in o["asns"] for r in origins)], "provider_evidence": [p for p in policy["providers"] if "provider_"+p["id"] in layers], "service_observations": [r for r in document["dns_records"] if r["address"] == str(address)], "service_evidence": [s for s in policy["services"] if any(r["domain"] == s["domain"] and r["address"] == str(address) for r in document["dns_records"])], "source_snapshots": document["sources"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="IP address or AS number")
    parser.add_argument("--directory", type=Path, default=Path("build/feed"))
    args = parser.parse_args()
    print(json.dumps(explain(args.query, args.directory), indent=2))
