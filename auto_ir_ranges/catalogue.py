"""Review constraints shared by the generator and investigator tooling."""
from datetime import date
from urllib.parse import urlparse

from .errors import GenerationError

RELATIONS = {"operator_affiliation", "asn_ownership", "provider_inventory", "service_dns"}


def validate(policy):
    if policy.get("schema") == 1:
        return  # migration reader; new edits must use schema 2
    if policy.get("schema") != 2:
        raise GenerationError("unsupported catalogue schema")
    owners = {}
    try:
        for group in ("operators", "providers", "services"):
            for item in policy[group]:
                if item["state"] != "accepted":
                    raise ValueError("only accepted entries belong in the collection catalogue")
                date.fromisoformat(item["reviewed_on"])
                if not item["review_reason"].strip():
                    raise ValueError("missing review reason")
                relations = item["relationships"]
                if not relations:
                    raise ValueError("missing typed evidence")
                for relation in relations:
                    if relation["type"] not in RELATIONS or not relation["evidence"] or not relation["reason"].strip():
                        raise ValueError("invalid relationship evidence")
                    for url in relation["evidence"]:
                        if urlparse(url).scheme != "https" or not urlparse(url).netloc:
                            raise ValueError("evidence URL must use HTTPS")
                    date.fromisoformat(relation["reviewed_on"])
                kinds = {r["type"] for r in relations}
                required = {"operators": "asn_ownership", "providers": "provider_inventory", "services": "service_dns"}[group]
                if required not in kinds:
                    raise ValueError(f"{group} needs {required} evidence")
                if group == "operators":
                    for asn in item["asns"]:
                        if asn in owners:
                            raise ValueError(f"conflicting operator identities for AS{asn}")
                        owners[asn] = item["id"]
                        if not any(r["type"] == "asn_ownership" and str(r.get("target")) == f"AS{asn}" for r in relations):
                            raise ValueError(f"missing ownership evidence for AS{asn}")
                    if not isinstance(item["aliases"], list):
                        raise ValueError("aliases must be a list")
                elif "asns" in item:
                    raise ValueError("hosting or provider inventory does not admit its hosting ASN")
    except (KeyError, TypeError, ValueError) as exc:
        raise GenerationError(f"invalid reviewed catalogue: {exc}") from exc


def ageing(policy, today=None, max_days=90):
    today = today or date.today()
    return [{"kind": group, "id": item.get("id", item.get("domain")), "reviewed_on": item.get("reviewed_on"), "age_days": (today-date.fromisoformat(item.get("reviewed_on", "1970-01-01"))).days}
            for group in ("operators", "providers", "services") for item in policy[group]
            if (today-date.fromisoformat(item.get("reviewed_on", "1970-01-01"))).days >= max_days]
