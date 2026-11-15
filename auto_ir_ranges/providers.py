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
LEAD_STOP_KEYS = {"cloud", "hosting", "server", "servers", "network", "datacenter", "internet", "iranian", "company"}


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


def normalise_domain(domain: str) -> str:
    domain = domain.strip().lower()
    domain = domain[4:] if domain.startswith("www.") else domain
    if "." not in domain or any(ch in domain for ch in "/:") or re.search(r"\s", domain):
        raise ValueError(f"invalid domain {domain!r}")
    return domain


def add_company(ledger: dict, brand: str, domain: str, evidence: str | None = None, note: str = "") -> dict:
    """Append an unchecked company found by market research; it may have no network of its own."""
    brand = brand.strip()
    if not brand:
        raise ValueError("brand must not be empty")
    domain = normalise_domain(domain)
    companies = ledger["companies"]
    for existing in companies:
        if domain in [d.lower() for d in existing["domains"]] or existing["brand"].lower() == brand.lower():
            raise ValueError(f"already in the ledger: {existing['id']}")
    entry = {"id": slug(brand, {c["id"] for c in companies}), "brand": brand, "legal_names": [], "domains": [domain], "asns": [],
             "status": "unchecked", "last_checked": None, "evidence": [evidence] if evidence else [], "lists": [], "foreign_networks": [],
             "notes": note}
    companies.append(entry)
    return entry


def _find_company(ledger: Mapping, company_id: str) -> dict:
    found = next((c for c in ledger["companies"] if c["id"] == company_id), None)
    if found is None:
        raise ValueError(f"unknown company id {company_id!r}")
    return found


def set_company(ledger: dict, company_id: str, status: str, brand: str | None = None, domains: Iterable[str] = (),
                evidence: Iterable[str] = (), note: str | None = None, today: date | None = None) -> dict:
    """Record a research result for one company; list_in_use is set only by propose_list."""
    if status == "list_in_use":
        raise ValueError("status list_in_use is set only by propose-list")
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    entry = _find_company(ledger, company_id)
    if entry["status"] == "list_in_use":
        raise ValueError(f"{company_id} has a list in use; withdraw it with withdraw-list first")
    new_domains = []
    for raw in domains:
        domain = normalise_domain(raw)
        owner = next((c for c in ledger["companies"] if domain in [d.lower() for d in c["domains"]]), None)
        if owner is not None and owner is not entry:
            raise ValueError(f"domain {domain} already belongs to {owner['id']}")
        if owner is None and domain not in new_domains:
            new_domains.append(domain)
    links = [url for url in dict.fromkeys(evidence) if url not in entry["evidence"]]
    for url in links:
        _https(url, "evidence")
    if brand is not None:
        entry["brand"] = brand.strip()
    entry["domains"] += new_domains
    entry["evidence"] += links
    if note is not None:
        entry["notes"] = note
    entry["status"] = status
    entry["last_checked"] = None if status == "unchecked" else (today or date.today()).isoformat()
    return entry


def propose_list(policy: dict, ledger: dict, company_id: str, policy_id: str, url: str, evidence: str, rows: int, broadest: int,
                 reason: str, version: int = 4, today: date | None = None) -> dict:
    """Add a provider entry to the catalogue and mark the company's list in use; mutates both."""
    if not re.fullmatch(r"[a-z0-9_]+", policy_id):
        raise ValueError(f"invalid policy id {policy_id!r}; use a-z, 0-9 and _")
    if any(p["id"] == policy_id for p in policy["providers"]):
        raise ValueError(f"policy provider {policy_id} already exists")
    if rows < 1:
        raise ValueError("rows must be at least 1")
    if version not in (4, 6):
        raise ValueError("version must be 4 or 6")
    if not reason.strip():
        raise ValueError("reason must not be empty")
    _https(url, "url")
    _https(evidence, "evidence")
    entry = _find_company(ledger, company_id)
    day = (today or date.today()).isoformat()
    provider = {"id": policy_id, "version": version, "url": url, "evidence": evidence, "min_count": max(1, rows // 2),
                "max_count": min(10_000, rows * 3), "min_prefixlen": broadest, "state": "accepted", "reviewed_on": day,
                "review_reason": reason,
                "relationships": [{"type": "provider_inventory", "target": url, "evidence": list(dict.fromkeys([evidence, url])),
                                   "reviewed_on": day, "reason": reason}]}
    policy["providers"].append(provider)
    item = next((i for i in entry["lists"] if i["url"] == url), None)
    if item is None:
        item = {"url": url, "policy_id": policy_id, "state": "in_use", "note": ""}
        entry["lists"].append(item)
    item.update(state="in_use", policy_id=policy_id)
    entry["status"], entry["last_checked"] = "list_in_use", day
    if evidence not in entry["evidence"]:
        entry["evidence"].append(evidence)
    return provider


def withdraw_list(policy: dict, ledger: dict, policy_id: str, reason: str, today: date | None = None) -> dict:
    """Undo propose_list: drop the provider entry and reject its ledger list; mutates both."""
    if not any(p["id"] == policy_id for p in policy["providers"]):
        raise ValueError(f"unknown policy provider {policy_id!r}")
    holder = next(((c, i) for c in ledger["companies"] for i in c["lists"] if i["policy_id"] == policy_id), None)
    if holder is None:
        raise ValueError(f"policy provider {policy_id} has no list in the ledger")
    entry, item = holder
    policy["providers"] = [p for p in policy["providers"] if p["id"] != policy_id]
    item.update(state="rejected", policy_id=None, note=reason)
    if not any(i["state"] == "in_use" for i in entry["lists"]):
        entry["status"] = "no_list_found"
    entry["last_checked"] = (today or date.today()).isoformat()
    return entry


def looks_like_list(payload: bytes) -> dict | None:
    """Summarise a plain list of CIDR ranges, or None when the payload is something else."""
    try:
        lines = [line.strip() for line in payload.decode("ascii").splitlines()]
    except UnicodeDecodeError:
        return None
    lines = [line for line in lines if line and not line.startswith("#")]
    if not 3 <= len(lines) <= 10_000:
        return None
    nets = []
    for line in lines:
        try:
            nets.append(ipaddress.ip_network(line, strict=False))
        except ValueError:
            pass
    if len(nets) * 10 < len(lines) * 9:
        return None
    v4, v6 = [n.prefixlen for n in nets if n.version == 4], [n.prefixlen for n in nets if n.version == 6]
    return {"rows": len(nets), "ipv4": len(v4), "ipv6": len(v6), "broadest4": min(v4, default=None), "broadest6": min(v6, default=None)}


def format_list(info: Mapping) -> str:
    parts = [f"IPv{v} {info[f'ipv{v}']}, broadest /{info[f'broadest{v}']}" for v in (4, 6) if info[f"ipv{v}"]]
    return f"list of {info['rows']} rows ({'; '.join(parts)})"


def next_companies(ledger: Mapping, limit: int = 15, today: date | None = None) -> list[dict]:
    """Unchecked companies with a domain, other unchecked ones, then stale ones, oldest first."""
    today = today or date.today()
    unchecked = [c for c in ledger["companies"] if c["status"] == "unchecked"]
    stale = [c for c in ledger["companies"] if c["status"] != "unchecked" and (today - date.fromisoformat(c["last_checked"])).days > STALE_DAYS]
    return ([c for c in unchecked if c["domains"]] + [c for c in unchecked if not c["domains"]] + sorted(stale, key=lambda c: c["last_checked"]))[:limit]


def describe(company: Mapping) -> str:
    name = company["brand"] or (company["legal_names"][0] if company["legal_names"] else "")
    asns = ",".join(f"AS{a}" for a in company["asns"]) or "-"
    domain = company["domains"][0] if company["domains"] else "-"
    return f"{company['id']} | {name} | {domain} | {asns} | {company['status']} | {company['last_checked'] or '-'}"


def _lead_keys(company: Mapping) -> list[str]:
    raw = [company["brand"]] + [d.lower().split(".")[-2] for d in company["domains"] if d.count(".") >= 1] + list(company["legal_names"])
    keys = []
    for key in (re.sub(r"[^a-z0-9]+", "", item.lower()) for item in raw if item):
        if len(key) >= 5 and key not in LEAD_STOP_KEYS and key not in keys:
            keys.append(key)
    return keys


def foreign_leads(ledger: Mapping, text: str, only: str | None = None) -> list[dict]:
    """Non-IR networks in IPtoASN rows whose description names a ledger company; read-only."""
    companies = ledger["companies"]
    if only is not None and not any(c["id"] == only for c in companies):
        raise ValueError(f"unknown company id {only!r}")
    held = {a for c in companies for a in c["asns"]} | {f["asn"] for c in companies for f in c["foreign_networks"]}
    keyed = [(c["id"], _lead_keys(c)) for c in companies if only is None or c["id"] == only]
    found: dict[tuple[str, int], dict] = {}
    matches: dict[str, list[tuple[str, str]]] = {}
    for line in text.splitlines():
        parts = line.split("\t", 4)
        if len(parts) < 5 or not parts[2].isdigit() or int(parts[2]) == 0 or int(parts[2]) in held:
            continue
        country = parts[3].strip().upper()
        if country == "IR" or not re.fullmatch(r"[A-Z]{2}", country):
            continue
        try:
            size = int(ipaddress.ip_address(parts[1].strip())) - int(ipaddress.ip_address(parts[0].strip())) + 1
        except ValueError:
            continue
        description = parts[4].strip()
        if description not in matches:  # one network's rows repeat its description
            flat = re.sub(r"[^a-z0-9]+", "", description.lower())
            matches[description] = [(cid, key) for cid, keys in keyed for key in keys if key in flat]
        seen = set()
        for cid, key in matches[description]:
            if cid in seen:
                continue
            seen.add(cid)
            lead = found.setdefault((cid, int(parts[2])), {"company": cid, "asn": int(parts[2]), "country": country, "description": description, "addresses": 0, "matched": key})
            lead["addresses"] += size
    order = {c["id"]: i for i, c in enumerate(companies)}
    return sorted(found.values(), key=lambda lead: (order[lead["company"]], -lead["addresses"]))


def gate_summary(results: Iterable[bool]) -> str:
    results = list(results)
    return f"{sum(results)} of {len(results)} provider lists healthy"


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
