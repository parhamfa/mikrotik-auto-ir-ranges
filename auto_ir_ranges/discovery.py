"""Untrusted leads, deliberately isolated from the accepted collection catalogue."""
from __future__ import annotations

import difflib
import gzip
import hashlib
import io
import ipaddress
import json
import re
import tarfile
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from .errors import GenerationError

GENERIC = set("as asn net isp pjs pjsc pte spa srl the and for com org www ir iran ae ltd llc inc co company corporation communications communication network networks internet telecom telecommunications technology technologies data services service cloud cdn hosting host abr trading limited fze gmbh".split())


def tokens(value):
    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold())
    result = set()
    for word in words:
        # Joined brand/suffix forms (e.g. BrandCloud) remain searchable as Brand.
        for suffix in ("cloud", "networks", "telecom", "hosting"):
            if word.endswith(suffix) and len(word) >= len(suffix)+4:
                word = word[:-len(suffix)]
        if len(word) >= 3 and word not in GENERIC and not word.isdigit() and not re.fullmatch(r"as[0-9]+", word):
            result.add(word)
    return result


def asn_index(payloads, ir_asns=()):
    result = {}
    for family, payload in payloads.items():
        for number, raw in enumerate(gzip.decompress(payload).decode("utf8").splitlines(), 1):
            parts = raw.split("\t", 4)
            try:
                if len(parts) != 5:
                    raise ValueError("wrong column count")
                start, end = ipaddress.ip_address(parts[0]), ipaddress.ip_address(parts[1])
                asn = int(parts[2])
                if start.version != family or end.version != family or start > end or not 0 <= asn < 2**32:
                    raise ValueError("invalid range or ASN")
                if asn == 0:
                    continue
                entry = result.setdefault(str(asn), {"asn": asn, "descriptions": set(), "countries": set(), "names": [], "websites": []})
                entry["descriptions"].add(parts[4])
                entry["countries"].add(parts[3])
            except (ValueError, IndexError) as exc:
                raise GenerationError(f"malformed global IPtoASN IPv{family} row {number}: {exc}") from exc
    for entry in result.values():
        entry["descriptions"] = sorted(entry["descriptions"])
        entry["countries"] = sorted(entry["countries"])
        entry["iran_anchor_sources"] = (["IPtoASN country IR"] if "IR" in entry["countries"] else []) + (["NRO IR registration or same-holder resource in this snapshot"] if entry["asn"] in ir_asns else [])
        entry["iran_anchor"] = bool(entry["iran_anchor_sources"])
    return result


def rdap_base(asn, bootstrap):
    for ranges, urls in bootstrap["services"]:
        for span in ranges:
            lo, _, hi = span.partition("-")
            if int(lo) <= asn <= int(hi or lo):
                return next(u for u in urls if u.startswith("https://")).rstrip("/") + f"/autnum/{asn}"
    raise GenerationError(f"no authoritative RDAP service for AS{asn}")


def organization_details(data):
    names, websites = set(), set()
    if data.get("name"):
        names.add(data["name"])
    # Exclude abuse/technical/person contact records and their private contact fields.
    for entity in data.get("entities", []):
        if "registrant" not in entity.get("roles", []):
            continue
        for field in entity.get("vcardArray", [None, []])[1]:
            if field[0] in ("fn", "org") and isinstance(field[3], str):
                names.add(field[3])
            if field[0] == "url" and isinstance(field[3], str) and field[3].startswith(("https://", "http://")):
                websites.add(field[3])
    return {"names": sorted(names), "websites": sorted(websites)}


def enrich(index, cache, bootstrap, fetch, *, now, budget=250, priority_asns=()):
    """Bounded, oldest-first RDAP refresh; gaps stay explicit in the index."""
    ordered = sorted(index, key=lambda k: (0 if index[k]["iran_anchor"] else (1 if k in priority_asns else 2), cache.get(k, {}).get("attempted_at", ""), int(k)))
    calls, errors = 0, []
    for k in ordered:
        prior = cache.get(k, {})
        due = not prior.get("observed_at") or datetime.fromisoformat(prior["observed_at"]) < now-timedelta(days=30)
        if due and calls < budget:
            calls += 1
            try:
                url = rdap_base(int(k), bootstrap)
                raw = fetch(url)
                data = json.loads(raw)
                if not int(data["startAutnum"]) <= int(k) <= int(data["endAutnum"]):
                    raise ValueError("RDAP ASN identity mismatch")
                prior = {**organization_details(data), "url": url, "sha256": hashlib.sha256(raw).hexdigest(), "observed_at": now.isoformat()}
            except Exception as exc:
                prior = {**prior, "error": str(exc)[:300]}
                errors.append({"asn": int(k), "error": str(exc)[:300]})
            prior["attempted_at"] = now.isoformat()
            cache[k] = prior
        index[k].update({field: prior.get(field, []) for field in ("names", "websites")})
        index[k]["rdap"] = prior or {"status": "pending_enrichment"}
    return {"requests": calls, "errors": errors, "pending_enrichment": sum(not v.get("rdap", {}).get("observed_at") for v in index.values())}


def relationship_candidates(index, policy, *, version):
    words, inverted, grams = {}, defaultdict(set), defaultdict(set)
    for k, entry in index.items():
        names = [*entry["descriptions"], *entry.get("names", [])]
        for operator in policy.get("operators", []):
            if k in operator["asns"]:
                names.extend(operator.get("aliases", []))
        words[k] = set().union(*(tokens(n) for n in names))
        for token in words[k]:
            inverted[token].add(k)
    # Common description words are not identifying brands. Keep their names in
    # the searchable index, but do not create thousands of weak affiliations.
    identifying = {token for token, asns in inverted.items() if len(asns) <= 100}
    for token in identifying:
        for i in range(len(token)-2):
            grams[token[i:i+3]].add(token)
    nominations = []
    for anchor, entry in index.items():
        if not entry["iran_anchor"]:
            continue
        scores = {}
        for token in words[anchor] & identifying:
            possible = set().union(*(grams[token[i:i+3]] for i in range(len(token)-2)))
            for other in possible:
                if token != other and min(len(token), len(other)) < 4:
                    continue
                score = 1.0 if token == other else difflib.SequenceMatcher(None, token, other).ratio()
                if score < .84:
                    continue
                for candidate in inverted[other]:
                    if candidate != anchor and not index[candidate]["iran_anchor"] and score > scores.get(candidate, (0,))[0]:
                        scores[candidate] = (score, token, other)
        for candidate, (score, left, right) in scores.items():
            nominations.append({"kind": "asn", "value": candidate, "evidence": {
                "source": "global-asn-name-match", "version": version, "lineage": "iptoasn-rdap",
                "url": index[candidate].get("rdap", {}).get("url", "https://iptoasn.com/"),
                "anchor_asn": int(anchor), "reason": f"Name similarity {left!r}/{right!r} ({score:.2f}); affiliation UNVERIFIED",
                "score": round(score, 3), "candidate_names": index[candidate]["descriptions"] + index[candidate].get("names", []),
            }})
    return nominations


def domain_rule(raw):
    """A suffix/pattern is a predicate, never an instruction to enumerate hosts."""
    rule = raw.split("#", 1)[0].strip()
    if not rule:
        return None
    rule, *attributes = rule.split()
    prefix, sep, value = rule.partition(":")
    kind = {"full": "domain", "domain": "suffix", "regexp": "regexp", "keyword": "keyword", "include": "include"}.get(prefix, "unsupported") if sep else "suffix"
    if not sep:
        value = rule
    if kind in ("domain", "suffix"):
        try:
            value = value.rstrip(".").encode("idna").decode().lower()
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value) or any(not label or len(label)>63 for label in value.split(".")):
                raise ValueError("invalid hostname")
        except (ValueError, UnicodeError):
            kind = "unsupported"
    return {"kind": kind, "value": value, "rule": raw, "attributes": attributes, "enumerable": kind == "domain" and "." in value}


def v2fly_rules(files, source, root="data/category-ir"):
    leads, unsupported = [], []
    def visit(path, stack):
        if path in stack or path not in files:
            unsupported.append({"source": source, "path": path, "reason": "missing or cyclic include"})
            return
        for number, raw in enumerate(files[path].decode().splitlines(), 1):
            rule = domain_rule(raw)
            if not rule:
                continue
            if rule["kind"] == "include":
                if rule["attributes"] or not re.fullmatch(r"[a-z0-9-]+", rule["value"]):
                    unsupported.append({**rule, "path": path, "line": number, "reason": "filtered include requires upstream semantics"})
                else:
                    visit("data/"+rule["value"], stack|{path})
                continue
            evidence = {**source, "path": path, "line": number, "rule": raw, "attributes": rule["attributes"], "url": source["url"]+"/"+path}
            leads.append({"kind": rule["kind"], "value": rule["value"], "evidence": evidence})
            if not rule["enumerable"]:
                unsupported.append({**rule, "path": path, "line": number, "reason": "not an enumerable exact hostname"})
    visit(root, set())
    return leads, unsupported


def archive_files(raw):
    result = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            path = member.name.partition("/")[2]
            if not path or member.size > 16*1024*1024 or ".." in path.split("/"):
                continue
            total += member.size
            if total > 128*1024*1024:
                raise GenerationError("community archive exceeds capacity")
            result[path] = archive.extractfile(member).read()
    return result


def fingerprint(evidence):
    # A new release containing the same assertion is not new corroboration.
    content = {k: v for k, v in evidence.items() if k not in {"version", "line", "observed_at", "url"}}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def update_pool(previous, leads, decisions, *, now):
    pool = {k: dict(v) for k, v in previous.items()}
    for row in pool.values():
        row["seen_this_run"] = False
    for lead in leads:
        ident = lead["kind"]+":"+lead["value"]
        row = pool.setdefault(ident, {"id": ident, "kind": lead["kind"], "value": lead["value"], "state": "pending", "first_seen": now, "evidence": {}, "history": []})
        proof = fingerprint(lead["evidence"])
        if proof not in row["evidence"] and row["state"] == "rejected":
            row["state"] = "pending"
            row["history"].append({"at": now, "reason": "new evidence reopened rejection"})
        row["evidence"][proof] = lead["evidence"]
        row.update(last_seen=now, seen_this_run=True)
    for ident, decision in decisions.items():
        if ident not in pool:
            raise GenerationError(f"review decision references unknown candidate {ident}")
        if decision["state"] not in {"pending", "accepted", "rejected"} or not decision.get("reason") or not decision.get("reviewed_on"):
            raise GenerationError(f"invalid candidate review: {ident}")
        row = pool[ident]
        if set(row["evidence"]) <= set(decision["evidence_fingerprints"]):
            row.update(state=decision["state"], review=decision)
        elif decision["state"] == "rejected":
            row["state"] = "pending"
    for row in pool.values():
        row["independent_evidence_groups"] = len({e["lineage"] for e in row["evidence"].values()})
    return pool


def validate_decisions(document):
    try:
        if document["schema"] != 1 or not isinstance(document["decisions"], dict):
            raise ValueError("unsupported decision schema")
        for ident, decision in document["decisions"].items():
            if ":" not in ident or decision["state"] not in {"pending", "accepted", "rejected"}:
                raise ValueError("invalid candidate identity/state")
            if not decision["reason"].strip():
                raise ValueError("missing review reason")
            date.fromisoformat(decision["reviewed_on"])
            proofs = decision["evidence_fingerprints"]
            if not isinstance(proofs, list) or not proofs or any(not re.fullmatch(r"[0-9a-f]{64}", p) for p in proofs):
                raise ValueError("review must identify the evidence fingerprints examined")
        return document["decisions"]
    except (KeyError, TypeError, ValueError) as exc:
        raise GenerationError(f"invalid candidate decisions: {exc}") from exc


def review_queue(pool, limit=50):
    """Reserve half the queue for oldest unreviewed leads, regardless of fame."""
    pending = [r for r in pool.values() if r["state"] == "pending"]
    oldest = sorted(pending, key=lambda r: (r.get("review", {}).get("reviewed_on", r["first_seen"]), r["id"]))[:max(1, limit//2)]
    selected = {r["id"] for r in oldest}
    rest = sorted((r for r in pending if r["id"] not in selected), key=lambda r: (-r["independent_evidence_groups"], r["first_seen"], r["id"]))[:limit-len(oldest)]
    return [r["id"] for r in oldest+rest]
