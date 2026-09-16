"""Evidence layers and coverage accounting, independent of RouterOS transport."""
from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

from .errors import GenerationError

POLICY_PATH = Path(__file__).resolve().parents[1] / "coverage-policy.json"
REGISTRIES = {"afrinic", "apnic", "arin", "lacnic", "ripencc", "iana"}


def load_policy(path: Path = POLICY_PATH) -> dict:
    try:
        policy = json.loads(path.read_text())
        if policy["schema"] != 1:
            raise ValueError("unsupported policy schema")
        domains = [item["domain"] for item in policy["services"]]
        if len(set(domains)) != len(domains):
            raise ValueError("duplicate service domains")
        for domain in domains:
            if not re.fullmatch(r"(?=.{1,253}$)[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", domain):
                raise ValueError(f"invalid service domain: {domain}")
        provider_ids = [item["id"] for item in policy["providers"]]
        if len(set(provider_ids)) != len(provider_ids):
            raise ValueError("duplicate provider IDs")
        urls = [policy["registry_url"], *policy["dns_resolvers"].values()]
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
    return policy


def extra_asns(policy: Mapping) -> dict[int, str]:
    return {int(asn): prefix for item in policy["operators"] for asn, prefix in item["asns"].items()}


def source_urls(policy: Mapping) -> dict[str, str]:
    return {
        "nro_delegated": policy["registry_url"],
        **{f"provider_{p['id']}": p["url"] for p in policy["providers"]},
    }


def parse_delegated(payload: bytes, *, anchors: Iterable[int] = (), now: datetime | None = None) -> tuple[dict[int, list], set[int], dict]:
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
            if fields[6] not in ("allocated", "assigned"):
                continue
            anchor = fields[2] == "asn" and any(int(fields[3]) <= a < int(fields[3]) + int(fields[4]) for a in wanted_asns)
            if fields[1] == "IR" or anchor:
                selected.append(fields)
                if fields[7]:
                    trusted_holders.add((fields[0], fields[7]))
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


def parse_dns_answer(response: Mapping, domain: str, record_type: int) -> list[dict]:
    """Admit only public addresses reachable through this query's CNAME chain."""
    try:
        if response["Status"] != 0 or response.get("TC", False):
            raise ValueError(f"DNS status={response.get('Status')}, truncated={response.get('TC', False)}")
        questions = response["Question"]
        if len(questions) != 1 or questions[0]["name"].rstrip(".").lower() != domain or questions[0]["type"] != record_type:
            raise ValueError("DNS question does not match the requested domain/type")
        answers = response.get("Answer", [])
        reachable = {domain}
        for _ in range(16):
            more = {rr["data"].rstrip(".").lower() for rr in answers if rr["type"] == 5 and rr["name"].rstrip(".").lower() in reachable}
            if more <= reachable:
                break
            reachable.update(more)
        else:
            raise ValueError("excessively long CNAME chain")
        output = []
        alias_ttls = [int(rr["TTL"]) for rr in answers if rr["type"] == 5 and rr["name"].rstrip(".").lower() in reachable]
        for rr in answers:
            if rr["type"] != record_type or rr["name"].rstrip(".").lower() not in reachable:
                continue
            address = ipaddress.ip_address(rr["data"])
            if address.version != (4 if record_type == 1 else 6) or not address.is_global or address.is_multicast:
                raise ValueError(f"non-public or wrong-family DNS answer: {address}")
            ttl = min([int(rr["TTL"]), *alias_ttls])
            if ttl < 0:
                raise ValueError("negative DNS TTL")
            output.append({"address": str(address), "ttl": ttl, "owner": rr["name"].rstrip(".").lower()})
        if len(output) > 128:
            raise ValueError("too many DNS answers for one service")
        return output
    except (KeyError, ValueError, TypeError) as exc:
        raise GenerationError(f"invalid DNS answer for {domain}: {exc}") from exc


def collect_dns(policy: Mapping) -> bytes:
    observed_at = datetime.now(timezone.utc).isoformat()
    def query(job: tuple) -> dict:
        domain, resolver, endpoint, rrtype = job
        url = endpoint + "?" + urllib.parse.urlencode({"name": domain, "type": rrtype})
        error = None
        for _ in range(3):
            try:
                request = urllib.request.Request(url, headers={"Accept": "application/dns-json", "User-Agent": "mikrotik-auto-ir-ranges/coverage"})
                with urllib.request.urlopen(request, timeout=15) as response:
                    raw = response.read(262_145)
                if len(raw) > 262_144:
                    raise ValueError("DNS response too large")
                data = json.loads(raw)
                parse_dns_answer(data, domain, rrtype)
                return {"domain": domain, "resolver": resolver, "type": rrtype, "url": url, "response": data}
            except Exception as exc:
                error = exc
        raise GenerationError(f"DNS lookup failed for {domain} via {resolver}: {error}")

    jobs = [(item["domain"], resolver, endpoint, rrtype) for item in policy["services"] for resolver, endpoint in policy["dns_resolvers"].items() for rrtype in (1, 28)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(query, jobs))
    return (json.dumps({"schema": 1, "observed_at": observed_at, "queries": results}, sort_keys=True) + "\n").encode()


def parse_dns_snapshot(payload: bytes, policy: Mapping, *, now: datetime | None = None) -> tuple[dict[int, list], list]:
    try:
        snapshot = json.loads(payload)
        if snapshot["schema"] != 1:
            raise ValueError("unsupported DNS snapshot schema")
        observed_at = datetime.fromisoformat(snapshot["observed_at"].replace("Z", "+00:00"))
        current = now or datetime.now(timezone.utc)
        if observed_at.tzinfo is None or observed_at < current - timedelta(hours=4) or observed_at > current + timedelta(minutes=5):
            raise ValueError("DNS snapshot is stale or future-dated")
        observations = snapshot["queries"]
        expected = {(item["domain"], resolver, rrtype) for item in policy["services"] for resolver in policy["dns_resolvers"] for rrtype in (1, 28)}
        actual = [(r["domain"], r["resolver"], r["type"]) for r in observations]
        if set(actual) != expected or len(actual) != len(expected):
            raise ValueError("DNS snapshot is incomplete, duplicated, or contains unconfigured domains")
        addresses = {item["domain"]: set() for item in policy["services"]}
        records = []
        networks: dict[int, list] = {4: [], 6: []}
        for observation in observations:
            domain, rrtype = observation["domain"], observation["type"]
            for answer in parse_dns_answer(observation["response"], domain, rrtype):
                address = ipaddress.ip_address(answer["address"])
                addresses[domain].add(str(address))
                networks[address.version].append(ipaddress.ip_network(str(address)))
                records.append({"domain": domain, "resolver": observation["resolver"], "observed_at": snapshot["observed_at"], **answer})
        unresolved = [domain for domain, values in addresses.items() if not values]
        if unresolved:
            raise ValueError(f"configured services have no addresses: {', '.join(unresolved)}")
        return networks, records
    except (ValueError, TypeError, KeyError) as exc:
        raise GenerationError(f"invalid service DNS snapshot: {exc}") from exc


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
