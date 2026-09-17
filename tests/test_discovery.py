import copy
import gzip
import json
import unittest
from pathlib import Path

from auto_ir_ranges.catalogue import validate
from auto_ir_ranges.coverage import load_policy
from auto_ir_ranges.discovery import asn_index, relationship_candidates, domain_rule, v2fly_rules, update_pool, review_queue, fingerprint, organization_details, validate_decisions
from auto_ir_ranges.errors import GenerationError


class DiscoveryTests(unittest.TestCase):
    def test_checked_in_review_decisions_are_well_formed(self):
        document = json.loads((Path(__file__).resolve().parents[1]/"candidate-decisions.json").read_bytes())
        validate_decisions(document)
        with self.assertRaises(GenerationError):
            validate_decisions({"schema": 1, "decisions": {"asn:1": {"state": "accepted"}}})

    def test_foreign_affiliates_are_nominated_without_manual_arvan_seeds(self):
        raw = "\n".join([
            "2.144.0.0\t2.144.0.255\t202468\tIR\tABRARVAN-AS Noyan Abr Arvan Co.",
            "185.215.232.0\t185.215.235.255\t208006\tAE\tARVANCLOUD-CDN ArvanCloud",
            "5.1.1.0\t5.1.1.255\t57568\tTR\tTR_ARVANCLOUD",
            "8.8.8.0\t8.8.8.255\t15169\tUS\tGOOGLE",
        ])
        index = asn_index({4: gzip.compress(raw.encode())})
        leads = relationship_candidates(index, {"operators": []}, version="fixture")
        self.assertEqual({r["value"] for r in leads}, {"208006", "57568"})
        pool = update_pool({}, leads, {}, now="2026-09-17")
        self.assertTrue(all(r["state"] == "pending" for r in pool.values()))

    def test_unfamiliar_operator_and_naming_collision_remain_unverified(self):
        raw = "\n".join([
            "2.144.0.0\t2.144.0.255\t100\tIR\tSepanta Network",
            "5.1.1.0\t5.1.1.255\t200\tAE\tSepantaCloud",
            "6.1.1.0\t6.1.1.255\t300\tUS\tSepanta Unrelated Retail",
            "7.1.1.0\t7.1.1.255\t400\tGB\tNetwork Cloud Hosting Ltd",
        ])
        leads = relationship_candidates(asn_index({4: gzip.compress(raw.encode())}), {"operators": []}, version="x")
        self.assertEqual({r["value"] for r in leads}, {"200", "300"})
        self.assertTrue(all("UNVERIFIED" in r["evidence"]["reason"] for r in leads))

    def test_suffix_patterns_and_filtered_includes_are_not_hostnames(self):
        source = {"source": "v2fly", "version": "abc", "url": "https://example.com/abc", "lineage": "community"}
        files = {"data/category-ir": b"ir\nexample.com\nfull:host.example\nregexp:.*example.*\ninclude:child\ninclude:filtered @ir\n", "data/child": b"full:child.example\ninclude:category-ir\n"}
        leads, gaps = v2fly_rules(files, source)
        self.assertEqual([r["value"] for r in leads if r["kind"] == "domain"], ["host.example", "child.example"])
        self.assertGreaterEqual(len(gaps), 5)
        self.assertEqual(domain_rule("example.com")["kind"], "suffix")
        self.assertFalse(domain_rule("ir")["enumerable"])

    def test_duplicate_lineages_do_not_corroborate_or_reopen_on_new_release(self):
        first = {"kind": "domain", "value": "small.example", "evidence": {"source": "v2fly", "version": "1", "lineage": "shared", "rule": "small.example", "url": "https://example.com/1"}}
        copied = copy.deepcopy(first)
        copied["evidence"]["source"] = "bootmortis"
        pool = update_pool({}, [first, copied], {}, now="2026-01-01")
        row = pool["domain:small.example"]
        self.assertEqual(row["independent_evidence_groups"], 1)
        decision = {"domain:small.example": {"state": "rejected", "reason": "unverified", "reviewed_on": "2026-01-01", "evidence_fingerprints": list(row["evidence"])}}
        pool = update_pool(pool, [], decision, now="2026-01-02")
        first["evidence"].update(version="2", url="https://example.com/2")
        pool = update_pool(pool, [first], decision, now="2026-01-03")
        self.assertEqual(pool[row["id"]]["state"], "rejected")
        new = copy.deepcopy(first)
        new["evidence"].update(source="official-provider", lineage="official", rule="official affiliation")
        pool = update_pool(pool, [new], decision, now="2026-01-04")
        self.assertEqual(pool[row["id"]]["state"], "pending")
        self.assertEqual(pool[row["id"]]["first_seen"], "2026-01-01")

    def test_queue_reserves_old_leads_and_rdap_omits_contact_details(self):
        leads = [{"kind": "domain", "value": f"small{i}.example", "evidence": {"source": "s", "lineage": "s", "url": "https://example.com"}} for i in range(10)]
        pool = update_pool({}, leads, {}, now="2020-01-01")
        pool = update_pool(pool, [{"kind": "asn", "value": "100", "evidence": {"source": "new", "lineage": "new", "url": "https://example.com"}}], {}, now="2026-01-01")
        self.assertTrue(all(k.startswith("domain:") for k in review_queue(pool, 4)[:2]))
        data = {"name": "NET", "entities": [{"roles": ["abuse"], "vcardArray": ["vcard", [["fn", {}, "text", "Person"]]]}, {"roles": ["registrant"], "vcardArray": ["vcard", [["org", {}, "text", "Company"], ["email", {}, "text", "private@example.com"]]]}]}
        self.assertEqual(organization_details(data), {"names": ["Company", "NET"], "websites": []})

    def test_conflicting_ownership_and_shared_hosting_asn_are_rejected(self):
        policy = load_policy()
        duplicate = copy.deepcopy(policy["operators"][0])
        duplicate["id"] = "unrelated"
        policy["operators"].append(duplicate)
        with self.assertRaisesRegex(GenerationError, "conflicting"):
            validate(policy)
        policy = load_policy()
        policy["services"][0]["asns"] = {"13335": "CLOUDFLARE"}
        with self.assertRaisesRegex(GenerationError, "hosting"):
            validate(policy)


if __name__ == "__main__":
    unittest.main()
