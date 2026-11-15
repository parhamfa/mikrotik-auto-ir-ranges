import contextlib
import copy
import gzip
import importlib.util
import io
import ipaddress
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from auto_ir_ranges import providers
from auto_ir_ranges.coverage import load_policy
from auto_ir_ranges.errors import GenerationError
from test_coverage import CHABOKAN

ROOT = Path(__file__).resolve().parents[1]
POLICY = load_policy()
LEDGER = providers.load_ledger()
net = ipaddress.ip_network


def company(ledger, cid):
    return next(c for c in ledger["companies"] if c["id"] == cid)


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OutsideCapTests(unittest.TestCase):
    def test_counts_addresses_not_covered_by_the_anchor(self):
        self.assertEqual(providers.outside_addresses([net("2.144.0.0/24"), net("8.8.8.0/24")], [net("2.144.0.0/14")], 4), 256)
        providers.check_outside("x", [net("8.8.0.0/20")], [], 4)
        with self.assertRaisesRegex(GenerationError, "x provider feed adds 4097 addresses outside known Iranian space; limit 4096"):
            providers.check_outside("x", [net("8.8.0.0/20"), net("9.9.9.9/32")], [], 4)
        providers.check_outside("v6", [net("2001:db8::/52")], [], 6)
        with self.assertRaisesRegex(GenerationError, "limit"):
            providers.check_outside("v6", [net("2001:db8::/51")], [], 6)


class LedgerTests(unittest.TestCase):
    def test_committed_ledger_matches_committed_policy(self):
        providers.validate_ledger(LEDGER, POLICY)
        in_use = sorted(item["policy_id"] for c in LEDGER["companies"] for item in c["lists"] if item["state"] == "in_use")
        self.assertEqual(in_use, sorted(p["id"] for p in POLICY["providers"]))

    def assertRejected(self, change, message):
        ledger = copy.deepcopy(LEDGER)
        change(ledger)
        with self.assertRaisesRegex(GenerationError, "invalid provider ledger: .*" + message):
            providers.validate_ledger(ledger, POLICY)

    def test_rule_1_schema_ids_keys_and_types(self):
        self.assertRejected(lambda l: l.update(schema=2), "schema 1")
        self.assertRejected(lambda l: company(l, "parspack").update(id="arvancloud"), "malformed or repeated")
        self.assertRejected(lambda l: company(l, "parspack").update(id="Pars_Pack"), "malformed or repeated")
        self.assertRejected(lambda l: company(l, "parspack").pop("notes"), "lacks 'notes'")
        self.assertRejected(lambda l: company(l, "parspack").pop("last_checked"), "lacks 'last_checked'")
        self.assertRejected(lambda l: company(l, "parspack").update(asns=["60631"]), "invalid ASN")
        self.assertRejected(lambda l: company(l, "parspack").update(domains="parspack.com"), "'domains' must be list")
        self.assertRejected(lambda l: company(l, "parspack")["lists"][0].pop("policy_id"), "lacks 'policy_id'")
        self.assertRejected(lambda l: company(l, "sotoon")["foreign_networks"][0].update(asn=True), "'asn' must be int")

    def test_rule_2_status_and_last_checked(self):
        self.assertRejected(lambda l: company(l, "sotoon").update(status="maybe"), "unknown status")
        self.assertRejected(lambda l: company(l, "sotoon").update(last_checked=None), "last_checked must be an ISO date")
        self.assertRejected(lambda l: company(l, "sotoon").update(last_checked="2026-13-01"), "month must be")
        first_unchecked = next(c for c in LEDGER["companies"] if c["status"] == "unchecked")["id"]
        self.assertRejected(lambda l: company(l, first_unchecked).update(last_checked="2026-11-15"), "unchecked company has last_checked null")

    def test_rule_3_urls_use_https(self):
        self.assertRejected(lambda l: company(l, "sotoon")["evidence"].append("http://sotoon.ir/"), "must use https")
        self.assertRejected(lambda l: company(l, "sotoon")["foreign_networks"][0]["evidence"].append("ftp://x"), "must use https")
        self.assertRejected(lambda l: company(l, "parspack")["lists"][0].update(url="http://parspack.com/cdnips.txt"), "must use https")

    def test_rule_4_ledger_and_policy_lists_match(self):
        def drop_chabokan_list(l):
            company(l, "chabokan").update(lists=[], status="candidate")
        self.assertRejected(drop_chabokan_list, "missing from the ledger: chabokan")
        self.assertRejected(lambda l: company(l, "parspack")["lists"][0].update(url="https://parspack.com/other.txt"), "no matching policy provider")
        self.assertRejected(lambda l: company(l, "parspack")["lists"][0].update(policy_id="mizbancloud"), "no matching policy provider")
        self.assertRejected(lambda l: company(l, "parspack")["lists"].append(dict(company(l, "parspack")["lists"][0])), "appears twice")
        self.assertRejected(lambda l: company(l, "parspack")["lists"].append({"url": "https://parspack.com/new.txt", "policy_id": "parspack", "state": "proposed", "note": ""}), "only an in_use list carries a policy_id")
        self.assertRejected(lambda l: company(l, "parspack").update(status="candidate"), "list_in_use if and only if")
        self.assertRejected(lambda l: company(l, "sotoon").update(status="list_in_use"), "list_in_use if and only if")
        self.assertRejected(lambda l: company(l, "sotoon")["lists"].append({"url": "https://sotoon.ir/ips.txt", "policy_id": None, "state": "maybe", "note": ""}), "unknown list state")

    def test_rule_5_list_host_belongs_to_the_company(self):
        self.assertRejected(lambda l: company(l, "chabokan").update(domains=["example.com"]), "not one of the company's domains")
        self.assertRejected(lambda l: company(l, "chabokan").update(domains=["bokan.net"]), "not one of the company's domains")
        ledger = copy.deepcopy(LEDGER)
        company(ledger, "sotoon")["lists"].append({"url": "https://cdn.sotoon.ir/ips.txt", "policy_id": None, "state": "proposed", "note": ""})
        providers.validate_ledger(ledger, POLICY)

    def test_rule_6_an_asn_has_one_company(self):
        self.assertRejected(lambda l: company(l, "parspack")["asns"].append(202468), "AS202468 belongs to both")
        self.assertRejected(lambda l: company(l, "parspack")["asns"].append(41227), "AS41227 belongs to both")

    def test_rule_7_accepted_foreign_network_iff_policy_operator(self):
        self.assertRejected(lambda l: company(l, "sotoon")["foreign_networks"][0].update(state="accepted"), "AS41227 is accepted if and only if")
        self.assertRejected(lambda l: company(l, "arvancloud")["foreign_networks"][0].update(state="proposed"), "AS208006 is accepted if and only if")
        self.assertRejected(lambda l: company(l, "sotoon")["foreign_networks"][0].update(state="maybe"), "unknown foreign network state")


class LedgerToolTests(unittest.TestCase):
    def ledger(self):
        def entry(cid, status, checked):
            return {"id": cid, "brand": "", "legal_names": [f"{cid} LLC"], "domains": [], "asns": [], "status": status, "last_checked": checked,
                    "evidence": [], "lists": [], "foreign_networks": [], "notes": ""}
        return {"schema": 1, "companies": [entry("fresh", "candidate", "2026-11-01"), entry("old", "rejected", "2026-01-01"),
                                           entry("a", "unchecked", None), entry("older", "no_list_found", "2025-06-01"), entry("b", "unchecked", None)]}

    def test_next_lists_unchecked_then_stale_oldest_first(self):
        picked = providers.next_companies(self.ledger(), 15, today=date(2026, 11, 15))
        self.assertEqual([c["id"] for c in picked], ["a", "b", "older", "old"])
        self.assertEqual([c["id"] for c in providers.next_companies(self.ledger(), 3, today=date(2026, 11, 15))], ["a", "b", "older"])
        self.assertEqual(providers.describe({**self.ledger()["companies"][2], "asns": [1, 2]}), "a | a LLC | AS1,AS2 | unchecked | -")

    def test_sync_attaches_by_legal_name_or_appends_unchecked(self):
        ledger = copy.deepcopy(LEDGER)
        rows = "\n".join([
            "1.0.0.0\t1.0.0.255\t64601\tIR\tSotoon-Extra Hezardastan Unit Cloud Computing PJSC",
            "1.0.1.0\t1.0.1.255\t64602\tIR\tNew-Host Example Host Co",
            "1.0.2.0\t1.0.2.255\t64603\tIR\tNew-Host-2 Example Host Co",
            "1.0.3.0\t1.0.3.255\t64604\tIR\tTELECOM Plain Telecom Co",
            "1.0.4.0\t1.0.4.255\t64605\tDE\tSOME-CLOUD German Cloud GmbH",
            "1.0.5.0\t1.0.5.255\t49801\tIR\tSotoon-Cloud-Infrastructure Hezardastan Unit Cloud Computing PJSC",
            "1.0.6.0\t1.0.6.255\t64602\tIR\tNew-Host Example Host Co",
        ])
        before = len(ledger["companies"])
        self.assertEqual(providers.sync_iptoasn(ledger, rows), 3)
        self.assertIn(64601, company(ledger, "sotoon")["asns"])
        added = ledger["companies"][before:]
        self.assertEqual([(c["id"], c["legal_names"], c["asns"], c["status"], c["last_checked"]) for c in added], [("new-host", ["Example Host Co"], [64602, 64603], "unchecked", None)])
        providers.validate_ledger(ledger, POLICY)

    def test_cli_stats_next_and_sync(self):
        module = load_script("ledger")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text(providers.dump_ledger(LEDGER), encoding="utf-8")
            feed = Path(tmp) / "ip2asn-v4.tsv.gz"
            feed.write_bytes(gzip.compress(b"1.0.1.0\t1.0.1.255\t64602\tIR\tNew-Host Example Host Co\n"))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(module.main(["--ledger", str(path), "stats"]), 0)
                self.assertEqual(module.main(["--ledger", str(path), "next", "2"]), 0)
                self.assertEqual(module.main(["--ledger", str(path), "sync", "--iptoasn", str(feed)]), 0)
            lines = out.getvalue().splitlines()
            self.assertIn("list_in_use    6", lines)
            self.assertEqual(len([line for line in lines if line.count(" | ") == 4]), 2)
            self.assertEqual(lines[-1], "added 1 networks (1 new companies)")
            self.assertEqual(company(json.loads(path.read_text()), "new-host")["asns"], [64602])


ANCHOR = {4: [net("91.107.0.0/16"), net("94.182.0.0/16"), net("185.24.252.0/22")], 6: []}
CHABOKAN_ENTRY = {"id": "chabokan", "version": 4, "url": "https://chabokan.net/ips.txt", "min_count": 8, "max_count": 64, "min_prefixlen": 28}


class GateTests(unittest.TestCase):
    def check(self, payload, **extra):
        return providers.check_provider({**CHABOKAN_ENTRY, **extra}, lambda url: payload, ANCHOR)

    def test_good_provider_passes(self):
        ok, line = self.check(CHABOKAN)
        self.assertTrue(ok)
        self.assertEqual(line, "OK   chabokan: 21 rows kept, 1 dropped (duplicate), 29 addresses outside known Iranian space")

    def test_provider_without_an_anchored_row_fails_unless_waived(self):
        foreign = b"".join(f"65.109.{i}.1/32\n".encode() for i in range(10))
        self.assertEqual(self.check(foreign), (False, "FAIL chabokan: no kept row overlaps known Iranian space"))
        ok, line = self.check(foreign, anchor_waiver="All servers abroad; reviewed by hand.")
        self.assertTrue(ok, line)

    def test_provider_over_cap_or_unparseable_or_unreachable_fails(self):
        ok, line = self.check(CHABOKAN + b"".join(f"8.{i}.0.0/28\n".encode() for i in range(256)), max_count=1000)
        self.assertFalse(ok)
        self.assertIn("outside known Iranian space; limit 4096", line)
        self.assertEqual(self.check(b"<html>\n")[0], False)
        def unreachable(url):
            raise GenerationError("download failed after 3 attempts")
        self.assertEqual(providers.check_provider(CHABOKAN_ENTRY, unreachable, ANCHOR), (False, "FAIL chabokan: download failed after 3 attempts"))

    def test_anchor_comes_from_non_provider_layers(self):
        anchor = providers.anchor_from_provenance({"layers": {"ipdeny_ipv4": ["2.144.0.0/15", "2.146.0.0/15"], "provider_x": ["8.8.8.0/24"], "selected_asn_ipv6": ["2a0c:a7c0::/29"]}})
        self.assertEqual(anchor, {4: [net("2.144.0.0/14")], 6: [net("2a0c:a7c0::/29")]})

    def test_changed_against_selection(self):
        policy = {"providers": [dict(CHABOKAN_ENTRY), {**CHABOKAN_ENTRY, "id": "other"}]}
        ids = lambda old: [p["id"] for p in providers.changed_providers(policy, old)]
        self.assertEqual(ids(None), ["chabokan", "other"])
        self.assertEqual(ids(copy.deepcopy(policy)), [])
        self.assertEqual(ids({"providers": [dict(CHABOKAN_ENTRY)]}), ["other"])
        for field, value in (("url", "https://chabokan.net/v2.txt"), ("version", 6), ("min_count", 1), ("max_count", 9), ("min_prefixlen", 24), ("anchor_waiver", "why")):
            with self.subTest(field=field):
                old = copy.deepcopy(policy)
                old["providers"][0][field] = value
                self.assertEqual(ids(old), ["chabokan"])
        old = copy.deepcopy(policy)
        old["providers"][0]["review_reason"] = "reworded"
        self.assertEqual(ids(old), [])

    def test_script_offline_and_injected_runs(self):
        module = load_script("check_providers")
        self.assertIsNone(module.old_policy("no-such-ref-for-tests"))
        with tempfile.TemporaryDirectory() as tmp:
            anchor = Path(tmp) / "provenance.json.gz"
            anchor.write_bytes(gzip.compress(json.dumps({"layers": {"ipdeny_ipv4": ["91.107.0.0/16", "94.182.0.0/16"]}}).encode()))
            module.download = lambda url, timeout=60: CHABOKAN
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(module.main(["--offline"]), 0)
                self.assertEqual(module.main(["--only", "chabokan", "--anchor", str(anchor)]), 0)
                self.assertEqual(module.main(["--only", "parspack", "--anchor", str(anchor)]), 1)
                self.assertEqual(module.main(["--only", "nope"]), 1)
            lines = out.getvalue().splitlines()
        self.assertRegex(lines[0], r"^OK ledger and catalogue agree \(\d+ companies, 7 provider lists\)$")
        self.assertTrue(lines[2].startswith("OK   chabokan: 21 rows kept, 1 dropped (duplicate)"))
        self.assertTrue(lines[4].startswith("FAIL parspack: invalid parspack provider feed"))
        self.assertEqual(lines[-1], "FAIL unknown provider ids: nope")


if __name__ == "__main__":
    unittest.main()
