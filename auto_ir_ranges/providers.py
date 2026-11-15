"""Provider lists: the outside-space cap, the provider ledger and the provider gate.

The daily build uses only the cap. The ledger is a research worklist and the
build never reads it.
"""
from __future__ import annotations

import ipaddress
import json
import re
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Mapping
from urllib.parse import urlparse

from . import coverage
from .errors import GenerationError

LEDGER_PATH = Path(__file__).resolve().parents[1] / "provider-ledger.json"
STATUSES = ("unchecked", "no_list_found", "unreachable", "list_in_use", "candidate", "rejected")
LIST_STATES = ("in_use", "proposed", "rejected")
FOREIGN_STATES = ("proposed", "accepted", "rejected")
# Addresses a provider list may add outside known Iranian space; for IPv6, 4096 /64 blocks.
MAX_OUTSIDE = {4: 4096, 6: 4096 * 2**64}
COMPANY_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
HOSTING_WORDS = re.compile(r"cloud|host|server|data ?cent|datacenter|idc|cdn|abr|mizban|vps|dade|pardaz", re.IGNORECASE)
# Fields whose change sends a provider entry through the networked gate again.
GATED_FIELDS = ("url", "version", "min_count", "max_count", "min_prefixlen", "anchor_waiver")
STALE_DAYS = 90


# --- the outside-space cap (daily build and gate) ---

def outside_addresses(networks: Iterable, anchor: Iterable, version: int) -> int:
    return sum(net.num_addresses for net in coverage.subtract_networks(networks, anchor, version))


def in_units(n: int, version: int) -> tuple[int, str]:
    """Addresses for IPv4; /64 blocks, rounded up, for IPv6."""
    return (n, "addresses") if version == 4 else (-(-n // 2**64), "/64 blocks")


def check_outside(provider_id: str, networks: Iterable, anchor: Iterable, version: int) -> None:
    n, limit = outside_addresses(networks, anchor, version), MAX_OUTSIDE[version]
    if n > limit:
        count, unit = in_units(n, version)
        raise GenerationError(f"{provider_id} provider feed adds {count} {unit} outside known Iranian space; limit {in_units(limit, version)[0]}")


# --- the ledger ---

def load_ledger(path: Path = LEDGER_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GenerationError(f"invalid provider ledger: {exc}") from exc


def dump_ledger(ledger: Mapping) -> str:
    return json.dumps(ledger, indent=2, ensure_ascii=False) + "\n"


COMPANY_KEYS = {"id": str, "brand": str, "legal_names": list, "domains": list, "asns": list, "status": str,
                "evidence": list, "lists": list, "foreign_networks": list, "notes": str}
LIST_KEYS = {"url": str, "state": str, "note": str}
FOREIGN_KEYS = {"asn": int, "label": str, "state": str, "evidence": list, "note": str}


def _typed(item, keys: Mapping, where: str) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"{where} must be an object")
    for key, kind in keys.items():
        if key not in item:
            raise ValueError(f"{where} lacks {key!r}")
        if not isinstance(item[key], kind) or (kind is int and isinstance(item[key], bool)):
            raise ValueError(f"{where}: {key!r} must be {kind.__name__}")


def _asn(value, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value < 2**32:
        raise ValueError(f"{where}: invalid ASN {value!r}")
    return value


def _https(url, where: str) -> None:
    if not isinstance(url, str) or urlparse(url).scheme != "https" or not urlparse(url).hostname:
        raise ValueError(f"{where}: URL must use https: {url!r}")


def validate_ledger(ledger: Mapping, policy: Mapping) -> None:
    try:
        if not isinstance(ledger, dict) or ledger.get("schema") != 1 or not isinstance(ledger.get("companies"), list):
            raise ValueError("expected schema 1 with a companies list")
        providers = {p["id"]: p for p in policy["providers"]}
        operator_asns = {int(asn) for item in policy["operators"] for asn in item["asns"]}
        ids, owners, used = set(), {}, {}
        for company in ledger["companies"]:
            _typed(company, COMPANY_KEYS, "company")
            cid = company["id"]
            if not COMPANY_ID.match(cid) or cid in ids:
                raise ValueError(f"company id {cid!r} is malformed or repeated")
            ids.add(cid)
            if "last_checked" not in company:
                raise ValueError(f"{cid} lacks 'last_checked'")
            if company["status"] not in STATUSES:
                raise ValueError(f"{cid}: unknown status {company['status']!r}")
            checked = company["last_checked"]
            if company["status"] == "unchecked":
                if checked is not None:
                    raise ValueError(f"{cid}: an unchecked company has last_checked null")
            elif not isinstance(checked, str):
                raise ValueError(f"{cid}: last_checked must be an ISO date")
            else:
                date.fromisoformat(checked)
            for field in ("legal_names", "domains"):
                if not all(isinstance(value, str) and value for value in company[field]):
                    raise ValueError(f"{cid}: {field} must be non-empty strings")
            for url in company["evidence"]:
                _https(url, cid)
            asns = [_asn(a, cid) for a in company["asns"]]
            for entry in company["foreign_networks"]:
                _typed(entry, FOREIGN_KEYS, f"{cid} foreign network")
                asns.append(_asn(entry["asn"], cid))
                if entry["state"] not in FOREIGN_STATES:
                    raise ValueError(f"{cid}: unknown foreign network state {entry['state']!r}")
                for url in entry["evidence"]:
                    _https(url, cid)
                if (entry["state"] == "accepted") != (entry["asn"] in operator_asns):
                    raise ValueError(f"{cid}: AS{entry['asn']} is accepted if and only if a policy operator lists it")
            for asn in asns:
                if asn in owners:
                    raise ValueError(f"AS{asn} belongs to both {owners[asn]} and {cid}")
                owners[asn] = cid
            in_use = 0
            for item in company["lists"]:
                _typed(item, LIST_KEYS, f"{cid} list")
                if "policy_id" not in item:
                    raise ValueError(f"{cid} list lacks 'policy_id'")
                _https(item["url"], cid)
                host = urlparse(item["url"]).hostname
                if not any(host == d or host.endswith("." + d) for d in company["domains"]):
                    raise ValueError(f"{cid}: list host {host} is not one of the company's domains")
                if item["state"] not in LIST_STATES:
                    raise ValueError(f"{cid}: unknown list state {item['state']!r}")
                if item["state"] != "in_use":
                    if item["policy_id"] is not None:
                        raise ValueError(f"{cid}: only an in_use list carries a policy_id")
                    continue
                in_use += 1
                provider = providers.get(item["policy_id"])
                if provider is None or provider["url"] != item["url"]:
                    raise ValueError(f"{cid}: in_use list {item['url']} has no matching policy provider")
                if item["policy_id"] in used:
                    raise ValueError(f"policy provider {item['policy_id']} appears twice in the ledger")
                used[item["policy_id"]] = cid
            if (company["status"] == "list_in_use") != bool(in_use):
                raise ValueError(f"{cid}: status list_in_use if and only if it has an in_use list")
        missing = sorted(set(providers) - set(used))
        if missing:
            raise ValueError(f"policy providers missing from the ledger: {', '.join(missing)}")
    except (KeyError, TypeError, ValueError) as exc:
        raise GenerationError(f"invalid provider ledger: {exc}") from exc


def split_holder(name: str) -> tuple[str, str]:
    """IPtoASN-style 'HANDLE Legal Name' -> (handle, legal name)."""
    handle, _, legal = name.strip().partition(" ")
    return handle, legal.strip()


def slug(text: str, taken: set) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "company"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def new_company(handle: str, legal: str, asn: int, taken: set) -> dict:
    return {"id": slug(handle, taken), "brand": "", "legal_names": [legal] if legal else [], "domains": [], "asns": [asn],
            "status": "unchecked", "last_checked": None, "evidence": [], "lists": [], "foreign_networks": [], "notes": ""}


def add_network(ledger: dict, asn: int, name: str) -> bool:
    """Attach an ASN to the company with the identical legal name, else append an unchecked company.

    Returns False when the ASN already belongs to a company.
    """
    companies = ledger["companies"]
    if any(asn in c["asns"] or asn in [f["asn"] for f in c["foreign_networks"]] for c in companies):
        return False
    handle, legal = split_holder(name)
    same = next((c for c in companies if legal and legal in c["legal_names"]), None)
    if same is not None:
        same["asns"].append(asn)
    else:
        companies.append(new_company(handle, legal, asn, {c["id"] for c in companies}))
    return True


def sync_iptoasn(ledger: dict, text: str) -> int:
    """Add IR-labelled hosting-like networks from IPtoASN rows that no company holds yet."""
    added = 0
    for line in text.splitlines():
        parts = line.split("\t", 4)
        if len(parts) < 5 or not parts[2].isdigit() or parts[3].strip().upper() != "IR" or not HOSTING_WORDS.search(parts[4]):
            continue
        asn = int(parts[2])
        if asn and add_network(ledger, asn, parts[4]):
            added += 1
    return added


def stats(ledger: Mapping) -> dict[str, int]:
    return {status: sum(c["status"] == status for c in ledger["companies"]) for status in STATUSES}


def next_companies(ledger: Mapping, limit: int = 15, today: date | None = None) -> list[dict]:
    today = today or date.today()
    unchecked = [c for c in ledger["companies"] if c["status"] == "unchecked"]
    stale = [c for c in ledger["companies"] if c["status"] != "unchecked" and (today - date.fromisoformat(c["last_checked"])).days > STALE_DAYS]
    return (unchecked + sorted(stale, key=lambda c: c["last_checked"]))[:limit]


def describe(company: Mapping) -> str:
    name = company["brand"] or (company["legal_names"][0] if company["legal_names"] else "")
    asns = ",".join(f"AS{a}" for a in company["asns"]) or "-"
    return f"{company['id']} | {name} | {asns} | {company['status']} | {company['last_checked'] or '-'}"


# --- the provider gate ---

def anchor_from_provenance(provenance: Mapping) -> dict[int, list]:
    """Known Iranian space: every published layer except provider lists, by family."""
    anchor = {4: [], 6: []}
    for name, cidrs in provenance["layers"].items():
        if not name.startswith("provider_"):
            for cidr in cidrs:
                net = ipaddress.ip_network(cidr)
                anchor[net.version].append(net)
    return {v: list(ipaddress.collapse_addresses(nets)) for v, nets in anchor.items()}


def check_provider(provider: Mapping, fetch: Callable[[str], bytes], anchor: Mapping[int, list]) -> tuple[bool, str]:
    """One gate verdict line for a policy provider entry."""
    pid, version = provider["id"], provider["version"]
    try:
        dropped = []
        networks = coverage.parse_provider(fetch(provider["url"]), provider, dropped)
        known = anchor[version]
        if not provider.get("anchor_waiver") and not any(outside_addresses([net], known, version) < net.num_addresses for net in networks):
            raise GenerationError("no kept row overlaps known Iranian space")
        check_outside(pid, networks, known, version)
        outside = outside_addresses(networks, known, version)
    except (GenerationError, OSError) as exc:
        return False, f"FAIL {pid}: {exc}"
    reasons = sorted({item["reason"] for item in dropped})
    note = f" ({', '.join(reasons)})" if reasons else ""
    count, unit = in_units(outside, version)
    return True, f"OK   {pid}: {len(networks)} rows kept, {len(dropped)} dropped{note}, {count} {unit} outside known Iranian space"


def changed_providers(policy: Mapping, old_policy: Mapping | None) -> list[dict]:
    """Providers that are new or whose gated fields changed; all of them without an old policy."""
    if old_policy is None:
        return list(policy["providers"])
    old = {p["id"]: p for p in old_policy.get("providers", [])}
    return [p for p in policy["providers"] if p["id"] not in old or any(p.get(f) != old[p["id"]].get(f) for f in GATED_FIELDS)]
