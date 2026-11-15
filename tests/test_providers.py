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

    def test_next_puts_unchecked_with_a_domain_first(self):
        ledger = self.ledger()
        company(ledger, "b")["domains"] = ["b.example"]
        picked = providers.next_companies(ledger, 15, today=date(2026, 11, 15))
        self.assertEqual([c["id"] for c in picked], ["b", "a", "older", "old"])

    def test_describe_with_and_without_a_domain(self):
        entry = {**self.ledger()["companies"][2], "asns": [1, 2]}
        self.assertEqual(providers.describe(entry), "a | a LLC | - | AS1,AS2 | unchecked | -")
        self.assertEqual(providers.describe({**entry, "domains": ["a.example", "x.example"], "asns": []}), "a | a LLC | a.example | - | unchecked | -")

    def test_add_company(self):
        ledger = copy.deepcopy(LEDGER)
        entry = providers.add_company(ledger, "New Host", "  WWW.NewHost.IR ", "https://newhost.ir/about", "found by search")
        self.assertEqual(entry, {"id": "new-host", "brand": "New Host", "legal_names": [], "domains": ["newhost.ir"], "asns": [], "status": "unchecked",
                                 "last_checked": None, "evidence": ["https://newhost.ir/about"], "lists": [], "foreign_networks": [], "notes": "found by search"})
        self.assertIs(ledger["companies"][-1], entry)
        providers.validate_ledger(ledger, POLICY)
        bare = providers.add_company(ledger, "Other", "other.ir")
        self.assertEqual((bare["evidence"], bare["notes"]), ([], ""))
        self.assertEqual(providers.add_company(ledger, "New  Host", "third.ir")["id"], "new-host-2")

    def test_add_company_refuses_duplicates_and_bad_domains(self):
        ledger = copy.deepcopy(LEDGER)
        for domain in ("parspack.com", "WWW.ParsPack.com", " ParsPack.COM "):
            with self.assertRaisesRegex(ValueError, "already in the ledger: parspack"):
                providers.add_company(ledger, "Brand New", domain)
        with self.assertRaisesRegex(ValueError, "already in the ledger: parspack"):
            providers.add_company(ledger, "PARSPACK", "different.ir")
        providers.add_company(ledger, "Sub", "a.parspack.com")
        for domain in ("nodot", "a.com/x", "https://a.com", "a.com:80", "a b.com", ""):
            with self.assertRaises(ValueError, msg=domain):
                providers.add_company(ledger, "Fine Brand", domain)

    def test_foreign_leads(self):
        ledger = {"schema": 1, "companies": [
            {**self.ledger()["companies"][0], "id": "sotoon", "brand": "Sotoon", "domains": ["sotoon.ir"], "legal_names": ["Hezardastan Unit Cloud Computing PJSC"], "asns": [100]},
            {**self.ledger()["companies"][0], "id": "tiny", "brand": "Tiny", "domains": ["host.cloud", "api.derak.cloud"], "legal_names": ["Network"]},
        ]}
        rows = "\n".join([
            "1.0.0.0\t1.0.0.255\t200\tNL\tSotoon-Cloud-Infrastructure Insightometrics B.V.",
            "1.0.1.0\t1.0.1.255\t200\tNL\tSotoon-Cloud-Infrastructure Insightometrics B.V.",
            "1.0.2.0\t1.0.2.255\t201\tIR\tSotoon Iranian",
            "1.0.3.0\t1.0.3.255\t100\tDE\tSotoon held asn",
            "1.0.4.0\t1.0.4.255\t202\tNone\tSotoon nowhere",
            "1.0.5.0\t1.0.5.255\t203\tDE\tTiny Network Cloud Hosting",
            "1.0.6.0\t1.0.6.255\t204\tFR\tDerak-Edge Derak SAS",
            "1.0.7.0\t1.0.7.15\t205\tFR\tHezardastan Unit Cloud Computing Ltd",
            "1.0.8.0\t1.0.8.255\t0\tFR\tSotoon zero",
        ])
        leads = providers.foreign_leads(ledger, rows)
        self.assertEqual(leads[0], {"company": "sotoon", "asn": 200, "country": "NL", "description": "Sotoon-Cloud-Infrastructure Insightometrics B.V.",
                                    "addresses": 512, "matched": "sotoon"})
        self.assertEqual([(lead["company"], lead["asn"]) for lead in leads], [("sotoon", 200), ("tiny", 204)])
        self.assertEqual(leads[1]["matched"], "derak")
        self.assertEqual([lead["asn"] for lead in providers.foreign_leads(ledger, rows, only="tiny")], [204])
        with self.assertRaisesRegex(ValueError, "unknown company id"):
            providers.foreign_leads(ledger, rows, only="nope")

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
            self.assertIn(f"{'list_in_use':<14} {providers.stats(LEDGER)['list_in_use']}", lines)
            self.assertIn(f"{'total':<14} {len(LEDGER['companies'])}", lines)
            self.assertEqual([line for line in lines if line.count(" | ") == 5], [providers.describe(c) for c in providers.next_companies(LEDGER, 2)])
            self.assertEqual(lines[-1], "added 1 networks (1 new companies)")
            self.assertEqual(company(json.loads(path.read_text()), "new-host")["asns"], [64602])


class LedgerCliTests(unittest.TestCase):
    def run_cli(self, module, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module.main(list(argv))
        return code, out.getvalue().splitlines(), err.getvalue()

    def test_add_and_leads(self):
        module = load_script("ledger")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text(providers.dump_ledger(LEDGER), encoding="utf-8")
            feed = Path(tmp) / "ip2asn-v4.tsv.gz"
            feed.write_bytes(gzip.compress(b"1.0.0.0\t1.0.0.255\t64700\tNL\tSOTOON-X Sotoon Europe B.V.\n"))
            self.assertEqual(self.run_cli(module, "--ledger", str(path), "add", "--brand", "Zed Host", "--domain", "WWW.zedhost.ir", "--evidence", "https://zedhost.ir/", "--note", "n")[:2], (0, ["added zed-host"]))
            code, lines, err = self.run_cli(module, "--ledger", str(path), "add", "--brand", "Other", "--domain", "zedhost.ir")
            self.assertEqual((code, lines), (1, []))
            self.assertIn("already in the ledger: zed-host", err)
            self.assertEqual(company(json.loads(path.read_text()), "zed-host")["domains"], ["zedhost.ir"])
            before = path.read_text()
            self.assertEqual(self.run_cli(module, "--ledger", str(path), "leads", "--iptoasn", str(feed))[:2],
                             (0, ["sotoon | AS64700 | NL | SOTOON-X Sotoon Europe B.V. | 256 addresses | matched \"sotoon\""]))
            self.assertEqual(self.run_cli(module, "--ledger", str(path), "leads", "--iptoasn", str(feed), "--only", "zed-host")[:2], (0, ["no leads"]))
            self.assertEqual(self.run_cli(module, "--ledger", str(path), "leads", "--iptoasn", str(feed), "--only", "nope")[0], 1)
            self.assertEqual(path.read_text(), before)


def free_company(ledger):
    """A company without a list and with a domain: where a test list can go, whatever research added."""
    return next(c for c in ledger["companies"] if c["domains"] and not c["lists"])


class SetCompanyTests(unittest.TestCase):
    def test_set_updates_fields_and_dates(self):
        ledger = copy.deepcopy(LEDGER)
        first, second = [c["id"] for c in ledger["companies"] if c["status"] == "unchecked"][:2]
        entry = providers.set_company(ledger, first, "no_list_found", brand=" Fresh ", domains=["WWW.Fresh-Host.IR", "fresh-host.ir"],
                                      evidence=["https://fresh-host.ir/a", "https://fresh-host.ir/a"], note="looked", today=date(2026, 11, 15))
        self.assertEqual((entry["brand"], entry["domains"][-1:], entry["status"], entry["last_checked"], entry["notes"]),
                         ("Fresh", ["fresh-host.ir"], "no_list_found", "2026-11-15", "looked"))
        self.assertEqual(entry["evidence"].count("https://fresh-host.ir/a"), 1)
        self.assertEqual(entry["domains"].count("fresh-host.ir"), 1)
        providers.validate_ledger(ledger, POLICY)
        again = providers.set_company(ledger, first, "candidate", domains=["fresh-host.ir"], evidence=["https://fresh-host.ir/a"], today=date(2026, 11, 16))
        self.assertEqual((again["domains"].count("fresh-host.ir"), again["evidence"].count("https://fresh-host.ir/a"), again["brand"], again["notes"]),
                         (1, 1, "Fresh", "looked"))
        self.assertIsNotNone(providers.set_company(ledger, second, "rejected")["last_checked"])
        self.assertIsNone(providers.set_company(ledger, second, "unchecked")["last_checked"])
        providers.validate_ledger(ledger, POLICY)

    def test_set_refusals_leave_the_company_untouched(self):
        ledger = copy.deepcopy(LEDGER)
        used = next(c for c in ledger["companies"] if c["status"] == "list_in_use")
        other = next(c for c in ledger["companies"] if c["status"] != "list_in_use" and c["domains"])
        before = copy.deepcopy(ledger)
        for args, message in (((used["id"], "rejected"), "has a list in use"), ((other["id"], "list_in_use"), "only by propose-list"),
                              (("nope", "rejected"), "unknown company id"), ((other["id"], "maybe"), "unknown status")):
            with self.assertRaisesRegex(ValueError, message):
                providers.set_company(ledger, *args)
        with self.assertRaisesRegex(ValueError, "already belongs to " + used["id"]):
            providers.set_company(ledger, other["id"], "rejected", brand="X", domains=["new-one.ir", used["domains"][0]])
        with self.assertRaisesRegex(ValueError, "must use https"):
            providers.set_company(ledger, other["id"], "rejected", brand="X", evidence=["http://x.ir/"])
        with self.assertRaisesRegex(ValueError, "invalid domain"):
            providers.set_company(ledger, other["id"], "rejected", domains=["nodot"])
        self.assertEqual(ledger, before)


PROPOSE = dict(policy_id="zed_host", url="https://{domain}/ips.txt", evidence="https://{domain}/help/ips", rows=10, broadest=28,
               reason="Zed publishes this list from its own domain.", today=date(2026, 11, 16))


def propose(policy, ledger, **change):
    target = free_company(ledger)
    args = {key: value.format(domain=target["domains"][0]) if isinstance(value, str) and "{domain}" in value else value
            for key, value in {**PROPOSE, **change}.items()}
    return target["id"], providers.propose_list(policy, ledger, target["id"], args.pop("policy_id"), args.pop("url"), args.pop("evidence"),
                                                args.pop("rows"), args.pop("broadest"), args.pop("reason"), **args)


class ProposeWithdrawTests(unittest.TestCase):
    def test_propose_writes_a_valid_entry_and_marks_the_list(self):
        policy, ledger = copy.deepcopy(POLICY), copy.deepcopy(LEDGER)
        cid, entry = propose(policy, ledger)
        domain = company(ledger, cid)["domains"][0]
        chabokan = next(p for p in POLICY["providers"] if p["id"] == "chabokan")
        self.assertEqual(list(entry), list(chabokan))
        self.assertEqual((entry["min_count"], entry["max_count"], entry["min_prefixlen"], entry["state"], entry["reviewed_on"], entry["version"]),
                         (5, 30, 28, "accepted", "2026-11-16", 4))
        self.assertEqual(entry["relationships"], [{"type": "provider_inventory", "target": f"https://{domain}/ips.txt",
                                                   "evidence": [f"https://{domain}/help/ips", f"https://{domain}/ips.txt"], "reviewed_on": "2026-11-16",
                                                   "reason": PROPOSE["reason"]}])
        self.assertIs(policy["providers"][-1], entry)
        self.assertEqual(company(ledger, cid)["lists"][-1], {"url": f"https://{domain}/ips.txt", "policy_id": "zed_host", "state": "in_use", "note": ""})
        self.assertEqual((company(ledger, cid)["status"], company(ledger, cid)["last_checked"]), ("list_in_use", "2026-11-16"))
        self.assertIn(f"https://{domain}/help/ips", company(ledger, cid)["evidence"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps(policy, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            load_policy(path)
        providers.validate_ledger(ledger, policy)

    def test_propose_bounds_and_evidence_dedupe(self):
        policy, ledger = copy.deepcopy(POLICY), copy.deepcopy(LEDGER)
        target = free_company(ledger)
        url = f"https://{target['domains'][0]}/ips.txt"
        entry = providers.propose_list(policy, ledger, target["id"], "tiny", url, url, 1, 24, "reason", version=6)
        self.assertEqual((entry["min_count"], entry["max_count"], entry["version"], entry["relationships"][0]["evidence"]), (1, 3, 6, [url]))
        self.assertEqual(providers.propose_list(copy.deepcopy(policy), ledger, target["id"], "big", url, url, 9000, 24, "r")["max_count"], 10_000)

    def test_propose_reuses_an_existing_proposed_list(self):
        policy, ledger = copy.deepcopy(POLICY), copy.deepcopy(LEDGER)
        target = free_company(ledger)
        url = f"https://{target['domains'][0]}/ips.txt"
        target["lists"].append({"url": url, "policy_id": None, "state": "proposed", "note": "seen"})
        providers.propose_list(policy, ledger, target["id"], "zed_host", url, url, 10, 28, "r")
        self.assertEqual(len(target["lists"]), 1)
        self.assertEqual((target["lists"][0]["state"], target["lists"][0]["policy_id"], target["lists"][0]["note"]), ("in_use", "zed_host", "seen"))
        providers.validate_ledger(ledger, policy)

    def test_propose_refusals(self):
        policy, ledger = copy.deepcopy(POLICY), copy.deepcopy(LEDGER)
        before = copy.deepcopy((policy, ledger))
        for change, message in (({"policy_id": POLICY["providers"][0]["id"]}, "already exists"), ({"policy_id": "Bad-Id"}, "invalid policy id"),
                                ({"rows": 0}, "rows must be"), ({"url": "http://{domain}/ips.txt"}, "must use https"), ({"version": 5}, "version")):
            with self.assertRaisesRegex(ValueError, message):
                propose(policy, ledger, **change)
        with self.assertRaisesRegex(ValueError, "unknown company id"):
            providers.propose_list(policy, ledger, "nope", "x", "https://a.ir/i", "https://a.ir/h", 5, 24, "r")
        self.assertEqual((policy, ledger), before)

    def test_withdraw_round_trip(self):
        policy, ledger = copy.deepcopy(POLICY), copy.deepcopy(LEDGER)
        cid, _ = propose(policy, ledger)
        entry = providers.withdraw_list(policy, ledger, "zed_host", "list went away", today=date(2026, 11, 17))
        self.assertEqual(policy, POLICY)
        self.assertEqual((entry["status"], entry["last_checked"]), ("no_list_found", "2026-11-17"))
        item = company(ledger, cid)["lists"][-1]
        self.assertEqual((item["state"], item["policy_id"], item["note"]), ("rejected", None, "list went away"))
        providers.validate_ledger(ledger, policy)
        with self.assertRaisesRegex(ValueError, "unknown policy provider"):
            providers.withdraw_list(policy, ledger, "zed_host", "again")

    def test_withdraw_keeps_status_while_another_list_is_in_use(self):
        policy, ledger = copy.deepcopy(POLICY), copy.deepcopy(LEDGER)
        cid, _ = propose(policy, ledger)
        domain = company(ledger, cid)["domains"][0]
        providers.propose_list(policy, ledger, cid, "second", f"https://{domain}/cdnips.txt", f"https://{domain}/h", 4, 24, "r")
        self.assertEqual(providers.withdraw_list(policy, ledger, "zed_host", "gone")["status"], "list_in_use")
        providers.validate_ledger(ledger, policy)


class ListProbeTests(unittest.TestCase):
    def test_looks_like_list(self):
        plain = b"1.2.3.0/24\n1.2.4.5\n1.2.8.0/22\n"
        self.assertEqual(providers.looks_like_list(plain), {"rows": 3, "ipv4": 3, "ipv6": 0, "broadest4": 22, "broadest6": None})
        noisy = b"# header\n\n1.2.3.0/24\n  1.2.4.0/24  \n# mid\n1.2.5.0/24\n\n"
        self.assertEqual(providers.looks_like_list(noisy)["rows"], 3)
        mixed = providers.looks_like_list(b"1.2.3.0/24\n2001:db8::/32\n2001:db8:1::/48\n1.2.4.0/23\n")
        self.assertEqual(mixed, {"rows": 4, "ipv4": 2, "ipv6": 2, "broadest4": 23, "broadest6": 32})
        self.assertIsNone(providers.looks_like_list(b"<!doctype html>\n<html>\n<body>hi</body>\n</html>\n"))
        self.assertIsNone(providers.looks_like_list(b"1.2.3.0/24\n1.2.4.0/24\n"))
        self.assertIsNone(providers.looks_like_list("1.2.3.0/24\n1.2.4.0/24\n1.2.5.0/24\nا\n".encode()))
        self.assertIsNone(providers.looks_like_list(b"1.2.3.0/24\n1.2.4.0/24\n1.2.5.0/24\nnope\n"))
        self.assertEqual(providers.looks_like_list(b"\n".join([b"1.2.3.0/24"] * 9 + [b"nope"]))["rows"], 9)
        self.assertIsNone(providers.looks_like_list(b"1.1.1.1\n" * 10_001))
        self.assertEqual(providers.looks_like_list(b"1.1.1.1\n" * 10_000)["rows"], 10_000)

    def test_format_list(self):
        both = {"rows": 4, "ipv4": 2, "ipv6": 2, "broadest4": 23, "broadest6": 32}
        self.assertEqual(providers.format_list(both), "list of 4 rows (IPv4 2, broadest /23; IPv6 2, broadest /32)")
        self.assertEqual(providers.format_list({**both, "ipv6": 0, "broadest6": None}), "list of 4 rows (IPv4 2, broadest /23)")


class LedgerWriteCommandTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script("ledger")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.policy, self.ledger = Path(self.tmp.name) / "policy.json", Path(self.tmp.name) / "ledger.json"
        self.policy.write_text((ROOT / "coverage-policy.json").read_text(encoding="utf-8"), encoding="utf-8")
        self.ledger.write_text(providers.dump_ledger(LEDGER), encoding="utf-8")
        self.target = free_company(LEDGER)
        self.domain = self.target["domains"][0]

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.module.main(["--ledger", str(self.ledger), "--policy", str(self.policy), *argv])
        return code, out.getvalue().splitlines(), err.getvalue()

    def propose_args(self, **change):
        args = {"--policy-id": "zed_host", "--url": f"https://{self.domain}/ips.txt", "--evidence": f"https://{self.domain}/help", "--rows": "10",
                "--broadest": "28", "--reason": "Published by the provider.", "--date": "2026-11-16", **change}
        return ["propose-list", self.target["id"], *[part for pair in args.items() for part in pair]]

    def test_the_real_policy_survives_a_rewrite_byte_for_byte(self):
        text = (ROOT / "coverage-policy.json").read_text(encoding="utf-8")
        self.assertEqual(json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n", text)

    def test_set_command(self):
        code, lines, err = self.run_cli("set", self.target["id"], "--status", "no_list_found", "--domain", "extra-" + self.domain, "--evidence",
                                        "https://x.ir/e", "--note", "none", "--date", "2026-11-16")
        self.assertEqual((code, lines, err), (0, [f"updated {self.target['id']}"], ""))
        saved = company(json.loads(self.ledger.read_text()), self.target["id"])
        self.assertEqual((saved["status"], saved["last_checked"], saved["notes"], saved["domains"][-1]), ("no_list_found", "2026-11-16", "none", "extra-" + self.domain))
        before = self.ledger.read_text()
        for argv in (["set", self.target["id"], "--status", "list_in_use"], ["set", "nope", "--status", "rejected"]):
            code, _, err = self.run_cli(*argv)
            self.assertEqual(code, 1)
            self.assertTrue(err.startswith("ledger: "))
        self.assertEqual(self.ledger.read_text(), before)

    def test_propose_then_withdraw_through_the_script(self):
        original = (self.policy.read_bytes(), self.ledger.read_bytes())
        code, lines, _ = self.run_cli(*self.propose_args())
        self.assertEqual((code, lines), (0, [f"proposed zed_host for {self.target['id']}; now run: python3 scripts/check_providers.py --only zed_host"]))
        self.assertEqual(len(load_policy(self.policy)["providers"]), len(POLICY["providers"]) + 1)
        self.assertEqual(self.run_cli(*self.propose_args())[0], 1)  # duplicate id
        self.assertEqual(self.run_cli("withdraw-list", "--policy-id", "zed_host", "--reason", "gone", "--date", "2026-11-17")[:2],
                         (0, [f"withdrew zed_host from {self.target['id']}"]))
        self.assertEqual(self.policy.read_bytes(), original[0])
        self.assertEqual(self.run_cli("withdraw-list", "--policy-id", "zed_host", "--reason", "gone")[0], 1)

    def test_failed_validation_restores_both_files(self):
        original = (self.policy.read_bytes(), self.ledger.read_bytes())
        # the list host is not one of the company's domains, which only the ledger validation after the write notices
        code, lines, err = self.run_cli(*self.propose_args(**{"--url": "https://elsewhere.example/ips.txt"}))
        self.assertEqual((code, lines), (1, []))
        self.assertIn("not one of the company's domains", err)
        self.assertEqual((self.policy.read_bytes(), self.ledger.read_bytes()), original)

    def test_probe_needs_no_ledger_and_reports_each_url(self):
        shown = "https://fresh.ir/ips.txt: list of 3 rows (IPv4 2, broadest /24; IPv6 1, broadest /32)"
        pages = {"https://fresh.ir/ips.txt": b"1.2.3.0/24\n1.2.4.0/24\n2001:db8::/32\n", "https://fresh.ir/cdnips.txt": b"<html>\n"}
        self.module.fetch = lambda url: pages[url]
        self.ledger.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.run_cli("probe", "  WWW.Fresh.IR ")[:2], (0, [shown, "https://fresh.ir/cdnips.txt: no list (not a list of ranges)"]))
        self.assertEqual(self.run_cli("probe", "https://fresh.ir/ips.txt")[:2], (0, [shown]))

        def broken(url):
            raise GenerationError("download failed after 3 attempts: HTTP 404")
        self.module.fetch = broken
        failed = "no list (download failed after 3 attempts: HTTP 404)"
        self.assertEqual(self.run_cli("probe", "fresh.ir")[:2], (0, [f"https://fresh.ir/ips.txt: {failed}", f"https://fresh.ir/cdnips.txt: {failed}"]))
        self.assertEqual(self.run_cli("probe", "not a domain")[0], 1)


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
        self.assertEqual(lines[0], f"OK ledger and catalogue agree ({len(LEDGER['companies'])} companies, {len(POLICY['providers'])} provider lists)")
        self.assertTrue(lines[2].startswith("OK   chabokan: 21 rows kept, 1 dropped (duplicate)"))
        self.assertEqual(lines[3], "1 of 1 provider lists healthy")
        self.assertTrue(lines[5].startswith("FAIL parspack: invalid parspack provider feed"))
        self.assertEqual(lines[6], "0 of 1 provider lists healthy")
        self.assertEqual(providers.gate_summary([True, False, True]), "2 of 3 provider lists healthy")
        self.assertEqual(lines[-1], "FAIL unknown provider ids: nope")


if __name__ == "__main__":
    unittest.main()
