"""Per-question DNS observations. Failure never refreshes an observation's age."""
from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .coverage import parse_dns_answer
from .errors import GenerationError

MAX_AGE = timedelta(hours=48)


def stamp(now=None):
    return (now or datetime.now(timezone.utc)).isoformat()


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp needs a timezone")
    return result


def key(row):
    return row["domain"], row["resolver"], row["type"]


def query(domain, resolver, endpoint, rrtype):
    url = endpoint + "?" + urllib.parse.urlencode({"name": domain, "type": rrtype})
    error = None
    for _ in range(3):
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/dns-json", "User-Agent": "mikrotik-auto-ir-ranges/2"})
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read(262145)
            if len(raw) > 262144:
                raise ValueError("DNS response too large")
            result = json.loads(raw)
            parse_dns_answer(result, domain, rrtype)
            return result
        except Exception as exc:
            error = exc
    raise GenerationError(str(error))


def collect(policy, previous=None, *, lookup=query, clock=lambda: datetime.now(timezone.utc)):
    """Keep the most recent *successful* response for this resolver/question only."""
    prior = {}
    if previous:
        old = json.loads(previous)
        if old.get("schema") not in (1, 2):
            raise GenerationError("unsupported DNS cache schema")
        for row in old["queries"]:
            endpoint = policy["dns_resolvers"].get(row["resolver"])
            previous_endpoint = row.get("endpoint", row.get("url", "").split("?", 1)[0])
            if "response" in row and endpoint == previous_endpoint:
                prior[key(row)] = {"response": row["response"], "observed_at": row.get("observed_at", old.get("observed_at"))}

    def run(job):
        domain, resolver, endpoint, rrtype = job
        row = {"domain": domain, "resolver": resolver, "endpoint": endpoint, "type": rrtype}
        try:
            response = lookup(*job)
            parse_dns_answer(response, domain, rrtype)
            row.update(status="success", response=response, observed_at=stamp(clock()))
        except Exception as exc:
            row.update(status="failure", error=str(exc)[:500])
            # The timestamp is copied verbatim, even after successive outages.
            row.update(prior.get(key(row), {}))
        row["attempted_at"] = stamp(clock())
        return row

    jobs = [(s["domain"], r, endpoint, t) for s in policy["services"] for r, endpoint in policy["dns_resolvers"].items() for t in (1, 28)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        rows = list(executor.map(run, jobs))
    return (json.dumps({"schema": 2, "collected_at": stamp(clock()), "queries": rows}, sort_keys=True) + "\n").encode()


def evaluate(payload, policy, *, now=None):
    """Evaluate freshness at generation time, independently from collection time."""
    current = now or datetime.now(timezone.utc)
    try:
        envelope = json.loads(payload)
        if envelope["schema"] != 2:
            raise ValueError("unsupported DNS snapshot schema")
        expected = {(s["domain"], r, t) for s in policy["services"] for r in policy["dns_resolvers"] for t in (1, 28)}
        rows = envelope["queries"]
        actual = [key(row) for row in rows]
        if set(actual) != expected or len(actual) != len(expected):
            raise ValueError("incomplete or duplicated DNS question outcomes")
        networks, records, outcomes, answers = {4: [], 6: []}, [], [], {}
        for row in rows:
            domain, resolver, rrtype = key(row)
            attempted = parse_time(row["attempted_at"])
            if attempted > current + timedelta(minutes=5) or row["status"] not in ("success", "failure"):
                raise ValueError("invalid DNS attempt status/time")
            outcome = {k: row[k] for k in ("domain", "resolver", "type", "status", "attempted_at")}
            outcome["error"] = row.get("error")
            outcome["freshness"] = "missing"
            found = []
            if "response" in row:
                observed = parse_time(row["observed_at"])
                if observed > attempted + timedelta(minutes=5) or observed > current + timedelta(minutes=5):
                    raise ValueError("future DNS observation")
                parsed = parse_dns_answer(row["response"], domain, rrtype)
                outcome.update(observed_at=row["observed_at"], age_seconds=max(0, int((current-observed).total_seconds())))
                if current - observed <= MAX_AGE:
                    outcome["freshness"] = "fresh" if row["status"] == "success" and current - observed <= timedelta(hours=4) else "cached"
                    for answer in parsed:
                        address = ipaddress.ip_address(answer["address"])
                        networks[address.version].append(ipaddress.ip_network(str(address)))
                        found.append(str(address))
                        records.append({**answer, "domain": domain, "resolver": resolver, "observed_at": row["observed_at"], "freshness": outcome["freshness"]})
                else:
                    outcome["freshness"] = "expired"
            elif row["status"] == "success":
                raise ValueError("successful DNS lookup lacks a response")
            outcome["addresses"] = sorted(set(found))
            outcomes.append(outcome)
            answers.setdefault((domain, rrtype), {})[resolver] = outcome["addresses"]
        unresolved = sorted(s["domain"] for s in policy["services"] if not any(r["domain"] == s["domain"] for r in records))
        disagreements = [{"domain": d, "type": t, "answers": values} for (d, t), values in answers.items() if len({tuple(v) for v in values.values()}) > 1]
        return networks, records, {"outcomes": outcomes, "unresolved_services": unresolved, "resolver_disagreements": disagreements, "max_cache_age_hours": 48}
    except (ValueError, KeyError, TypeError) as exc:
        raise GenerationError(f"invalid DNS observation cache: {exc}") from exc
