"""Evidence layers and coverage accounting, independent of RouterOS transport."""
from __future__ import annotations

import ipaddress
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

from .errors import GenerationError

POLICY_PATH = Path(__file__).resolve().parents[1] / "coverage-policy.json"
REGISTRIES = {"afrinic", "apnic", "arin", "lacnic", "ripencc", "iana"}


def load_policy(path: Path = POLICY_PATH) -> dict:
    try:
        policy = json.loads(path.read_text())
        if policy["schema"] not in (1, 2):
            raise ValueError("unsupported policy schema")
        for key in ("services", "dns_resolvers"):
            if key in policy:
                raise ValueError(f"website DNS tracking was removed; delete the {key!r} key")
        provider_ids = [item["id"] for item in policy["providers"]]
        if len(set(provider_ids)) != len(provider_ids):
            raise ValueError("duplicate provider IDs")
        urls = [policy["registry_url"]]
        urls += [item["url"] for item in policy["providers"]]
        if any(not url.startswith("https://") for url in urls):
            raise ValueError("source URLs must use HTTPS")
        for provider in policy["providers"]:
            if provider["version"] not in (4, 6):
                raise ValueError("invalid provider address family")
            if not 1 <= provider["min_count"] <= provider["max_count"] <= 10_000:
                raise ValueError("invalid provider count bounds")
        for operator in policy["operators"]:
            if not operator["evidence"] or not operator["asns"]:
                raise ValueError("operator needs ASN ownership evidence")
            for asn, prefix in operator["asns"].items():
                if not 1 <= int(asn) < 2**32 or not prefix:
                    raise ValueError("invalid reviewed ASN")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GenerationError(f"invalid coverage policy: {exc}") from exc
    from .catalogue import validate
    validate(policy)
    return policy


def extra_asns(policy: Mapping) -> dict[int, str]:
    return {int(asn): prefix for item in policy["operators"] for asn, prefix in item["asns"].items()}


def source_urls(policy: Mapping) -> dict[str, str]:
    return {
        "nro_delegated": policy["registry_url"],
        **{f"provider_{p['id']}": p["url"] for p in policy["providers"]},
    }


def parse_delegated(payload: bytes, *, anchors: Iterable[int] = (), now: datetime | None = None, details: list | None = None) -> tuple[dict[int, list], set[int], dict]:
    """Select IR resources, plus resources held by the same registered operators.

    Opaque holder IDs are used only within this one snapshot and registry.
    Never persist them as stable organization identifiers.
    """
    try:
        lines = payload.decode("ascii").splitlines()
        header = next(line for line in lines if line and not line.startswith("#")).split("|")
        if header[0] not in ("2", "2.3") or header[1] != "nro":
            raise ValueError("expected an NRO extended statistics header")
        expected_records = int(header[3])
        snapshot = datetime.strptime(header[5], "%Y%m%d").replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if snapshot < current - timedelta(days=3) or snapshot > current + timedelta(days=1):
            raise ValueError(f"registry snapshot is stale or future-dated: {header[5]}")
        wanted_asns = set(anchors)
        trusted_holders: set[tuple[str, str]] = set()
        holder_seeds = {}
        count = 0
        selected = []
        for line in lines:
            if not line or line.startswith("#"):
                continue
            fields = line.split("|")
            if fields[0] in ("2", "2.3") or fields[-1] == "summary":
                continue
            if len(fields) < 8 or fields[0] not in REGISTRIES or fields[2] not in ("asn", "ipv4", "ipv6"):
                raise ValueError("malformed delegated statistics record")
            count += 1
            # Validate the entire core dataset, including rows we do not select.
            first_value, size_value = fields[3], int(fields[4])
            if fields[2] == "asn":
                if not 0 <= int(first_value) < 2**32 or not 1 <= size_value <= 2**32 or int(first_value)+size_value > 2**32:
                    raise ValueError("invalid global ASN allocation")
            elif fields[2] == "ipv4":
                first_address = ipaddress.IPv4Address(first_value)
                if size_value <= 0 or int(first_address)+size_value > 2**32:
                    raise ValueError("invalid global IPv4 allocation")
            else:
                ipaddress.IPv6Network(f"{first_value}/{size_value}", strict=True)
            if fields[6] not in ("allocated", "assigned"):
                continue
            anchor = fields[2] == "asn" and any(int(fields[3]) <= a < int(fields[3]) + int(fields[4]) for a in wanted_asns)
            if fields[1] == "IR" or anchor:
                selected.append(fields)
                if fields[7]:
                    trusted_holders.add((fields[0], fields[7]))
                    holder_seeds.setdefault((fields[0], fields[7]), {"country": fields[1], "type": fields[2], "start": fields[3], "value": fields[4], "reason": "IR label" if fields[1] == "IR" else "reviewed ASN anchor"})
        if count != expected_records:
            raise ValueError(f"truncated registry snapshot: {count} records, expected {expected_records}")
        selected_keys = {tuple(fields) for fields in selected}
        for line in lines:
            fields = line.split("|")
            if len(fields) >= 8 and fields[6] in ("allocated", "assigned"):
                if (fields[0], fields[7]) in trusted_holders and tuple(fields) not in selected_keys:
                    selected.append(fields)
        networks: dict[int, list] = {4: [], 6: []}
        asns: set[int] = set()
        for fields in selected:
            if details is not None:
                details.append({"registry": fields[0], "country": fields[1], "type": fields[2], "start": fields[3], "value": fields[4], "snapshot": header[5], "holder_id_in_snapshot": fields[7], "holder_selection_evidence": holder_seeds.get((fields[0], fields[7])), "selection": "IR registration" if fields[1] == "IR" else "selected ASN or same holder within this registry and snapshot"})
            start, value = fields[3:5]
            if fields[2] == "asn":
                first, size = int(start), int(value)
                if first <= 0 or not 1 <= size <= 100_000 or first + size > 2**32:
                    raise ValueError("invalid ASN allocation")
                asns.update(range(first, first + size))
            elif fields[2] == "ipv4":
                first = ipaddress.IPv4Address(start)
                size = int(value)
                if size <= 0:
                    raise ValueError("invalid IPv4 allocation size")
                networks[4].extend(ipaddress.summarize_address_range(first, first + size - 1))
            else:
                networks[6].append(ipaddress.IPv6Network(f"{start}/{value}", strict=True))
        if any(not net.is_global or net.is_multicast for family in networks.values() for net in family):
            raise ValueError("selected registry resources contain non-public address space")
        return networks, asns, {"snapshot_date": header[5], "selected_asns": len(asns), "selected_holders": len(trusted_holders), "records": count}
    except (ValueError, IndexError, StopIteration, UnicodeError) as exc:
        raise GenerationError(f"invalid NRO delegated statistics: {exc}") from exc


def parse_provider(payload: bytes, provider: Mapping) -> list:
    try:
        networks = [ipaddress.ip_network(line.strip(), strict=True) for line in payload.decode("ascii").splitlines() if line.strip() and not line.startswith("#")]
        if len(set(networks)) != len(networks):
            raise ValueError("duplicate provider CIDRs")
        if not provider["min_count"] <= len(networks) <= provider["max_count"]:
            raise ValueError(f"provider count {len(networks)} outside configured bounds")
        for net in networks:
            if net.version != provider["version"] or net.prefixlen < provider["min_prefixlen"]:
                raise ValueError(f"unexpected family or overly broad prefix: {net}")
            if not net.is_global or net.is_multicast:
                raise ValueError(f"non-public provider prefix: {net}")
        return networks
    except (ValueError, UnicodeError) as exc:
        raise GenerationError(f"invalid {provider['id']} provider feed: {exc}") from exc


def subtract_networks(networks: Iterable, covered: Iterable, version: int) -> list:
    """Exact interval subtraction, including coverage by several smaller CIDRs."""
    wanted = list(ipaddress.collapse_addresses(networks))
    spans = [(int(n.network_address), int(n.broadcast_address)) for n in ipaddress.collapse_addresses(covered)]
    address = ipaddress.IPv4Address if version == 4 else ipaddress.IPv6Address
    result = []
    index = 0
    for net in wanted:
        start, end = int(net.network_address), int(net.broadcast_address)
        while index < len(spans) and spans[index][1] < start:
            index += 1
        scan = index
        while scan < len(spans) and spans[scan][0] <= end:
            a, b = spans[scan]
            if a > start:
                result.extend(ipaddress.summarize_address_range(address(start), address(a - 1)))
            start = max(start, b + 1)
            if start > end:
                break
            scan += 1
        if start <= end:
            result.extend(ipaddress.summarize_address_range(address(start), address(end)))
    return result


def coverage_report(layers: Mapping[str, tuple[int, list]], final: Mapping[int, list], baseline: Mapping[int, list]) -> dict:
    report = {}
    for name, (version, networks) in layers.items():
        collapsed = list(ipaddress.collapse_addresses(networks))
        missing = subtract_networks(collapsed, final[version], version)
        if missing:
            raise GenerationError(f"known-source coverage gap in {name}: {missing[:5]}")
        additions = subtract_networks(collapsed, baseline[version], version)
        report[name] = {
            "version": version,
            "input_prefixes": len(networks),
            "unique_addresses": str(sum(net.num_addresses for net in collapsed)),
            "missing_prefixes": [],
            "outside_legacy_baseline": [str(net) for net in additions],
        }
    return report
