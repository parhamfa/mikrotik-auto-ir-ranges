import contextlib
import gzip
import importlib.util
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from auto_ir_ranges.generator import GenerationError, build_live_artifacts, generate_from_sources, parse_iptoasn_rows
from test_coverage import LIMITS, delegated, generation_inputs

DAY1 = datetime(2026, 9, 16, tzinfo=timezone.utc)
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_sources.py"


def stamp(when: datetime) -> str:
    return when.isoformat().replace("+00:00", "Z")


def inputs_at(when: datetime) -> tuple[dict, dict]:
    payloads, policy = generation_inputs()
    payloads["nro_delegated"] = delegated("ripencc|IR|asn|100|1|20200101|assigned|ir", "ripencc|AE|asn|208006|1|20200101|assigned|arvan", date=when.strftime("%Y%m%d"))
    return payloads, policy


def build(when: datetime, previous=None, drop=(), replace=None, policy_change=None, failures=None):
    payloads, policy = inputs_at(when)
    for name in drop:
        del payloads[name]
    payloads.update(replace or {})
    if policy_change:
        policy_change(policy)
    artifacts = generate_from_sources(payloads, policy=policy, generated_at=stamp(when), limits=LIMITS, previous_provenance=previous, source_failures=failures)
    return artifacts, json.loads(artifacts.coverage), json.loads(gzip.decompress(artifacts.provenance))


class SourceFallbackTests(unittest.TestCase):
    def test_provider_download_failure_reuses_previous_layer_without_advancing_freshness(self):
        _, _, first = build(DAY1)
        artifacts, report, second = build(DAY1 + timedelta(days=1), first, drop=["provider_arvancloud"], failures={"provider_arvancloud": "HTTP Error 403"})
        self.assertIn(b"193.24.119.0/29\n", artifacts.ipv4.data)
        self.assertEqual(report["source_status"]["provider_arvancloud"], {"status": "reused", "last_fresh": stamp(DAY1), "reason": "HTTP Error 403"})
        self.assertEqual(report["source_status"]["ipdeny_ipv4"]["last_fresh"], stamp(DAY1 + timedelta(days=1)))
        self.assertEqual(second["layers"]["provider_arvancloud"], first["layers"]["provider_arvancloud"])
        self.assertEqual(json.loads(artifacts.manifest)["sources"]["provider_arvancloud"], first["sources"]["provider_arvancloud"])
        # A second failed day reuses the reused copy and still reports the first day.
        _, report, _ = build(DAY1 + timedelta(days=2), second, drop=["provider_arvancloud"])
        self.assertEqual(report["source_status"]["provider_arvancloud"]["last_fresh"], stamp(DAY1))

    def test_provider_payload_failing_sanity_check_is_reused(self):
        _, _, first = build(DAY1)
        artifacts, report, _ = build(DAY1 + timedelta(days=1), first, replace={"provider_arvancloud": b"10.0.0.0/24\n"})
        self.assertIn(b"193.24.119.0/29\n", artifacts.ipv4.data)
        self.assertNotIn(b"10.0.0.0/24", artifacts.ipv4.data)
        status = report["source_status"]["provider_arvancloud"]
        self.assertEqual((status["status"], status["last_fresh"]), ("reused", stamp(DAY1)))
        self.assertIn("non-public provider prefix", status["reason"])

    def test_reuse_older_than_thirty_days_drops_the_layer(self):
        _, _, first = build(DAY1)
        artifacts, report, provenance = build(DAY1 + timedelta(days=31), first, drop=["provider_arvancloud"])
        self.assertNotIn(b"193.24.119.0/29", artifacts.ipv4.data)
        self.assertEqual(report["source_status"]["provider_arvancloud"]["status"], "dropped")
        self.assertEqual(report["source_status"]["provider_arvancloud"]["last_fresh"], stamp(DAY1))
        self.assertEqual(provenance["layers"]["provider_arvancloud"], [])
        _, report, _ = build(DAY1 + timedelta(days=30), first, drop=["provider_arvancloud"])
        self.assertEqual(report["source_status"]["provider_arvancloud"]["status"], "reused")

    def test_registry_failure_still_aborts(self):
        payloads, policy = inputs_at(DAY1)
        payloads["nro_delegated"] = b"<html>maintenance</html>\n"
        with self.assertRaisesRegex(GenerationError, "invalid NRO delegated statistics"):
            generate_from_sources(payloads, policy=policy, generated_at=stamp(DAY1), limits=LIMITS)
        del payloads["nro_delegated"]
        with self.assertRaisesRegex(GenerationError, "missing source payloads: nro_delegated"):
            generate_from_sources(payloads, policy=policy, generated_at=stamp(DAY1), limits=LIMITS)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
            for name, payload in payloads.items():
                (Path(tmp) / name).write_bytes(payload)
            with self.assertRaises(FileNotFoundError):
                build_live_artifacts(source_dir=Path(tmp), policy=policy, generated_at=stamp(DAY1))

    def test_iptoasn_failure_reuses_asn_layer_and_skips_identity_checks(self):
        _, _, first = build(DAY1)
        def renamed(policy):
            policy["operators"] = [{"asns": {"208006": "SOMEONE-ELSE"}}]
        artifacts, report, provenance = build(DAY1 + timedelta(days=1), first, replace={"iptoasn_ipv4": b"not gzip"}, policy_change=renamed)
        self.assertEqual(report["source_status"]["selected_asn_ipv4"]["status"], "reused")
        self.assertEqual(report["source_status"]["selected_asn_ipv6"]["status"], "fresh")
        self.assertEqual(report["warnings"], [])
        self.assertEqual(provenance["layers"]["selected_asn_ipv4"], first["layers"]["selected_asn_ipv4"])
        self.assertIn(208006, {r["asn"] for r in provenance["asn_origins"]})
        self.assertIn(b"2.144.0.0/14\n", artifacts.ipv4.data)

    def test_catalogue_name_prefix_accepts_suffixes(self):
        for observed in ("ARVANCLOUD-CDN-IR", "ARVANCLOUD-CDN", "arvancloud-cdn-ir"):
            with self.subTest(observed=observed):
                mismatches = {}
                _, asns = parse_iptoasn_rows(f"185.215.232.0\t185.215.235.255\t205585\tAE\t{observed}\n", 4, include_asns={205585}, expected_descriptions={205585: "ARVANCLOUD-CDN-"}, mismatches=mismatches)
                self.assertEqual((asns, mismatches), ({205585}, {}))

    def test_different_operator_label_excludes_asn_with_warning(self):
        replace = {"iptoasn_ipv4": gzip.compress(b"2.144.0.0\t2.147.255.255\t100\tIR\tIR-EXAMPLE\n185.215.232.0\t185.215.235.255\t208006\tAE\tUNRELATED\n")}
        _, report, provenance = build(DAY1, replace=replace)
        self.assertNotIn("185.215.232.0/22", provenance["layers"]["selected_asn_ipv4"])
        self.assertEqual(report["warnings"], ["reviewed operator AS208006 identity changed to UNRELATED; its ranges not labelled IR were left out"])
        self.assertEqual(report["source_status"]["selected_asn_ipv4"]["status"], "fresh")

    def test_absent_operator_is_a_warning(self):
        replace = {"iptoasn_ipv4": gzip.compress(b"2.144.0.0\t2.147.255.255\t100\tIR\tIR-EXAMPLE\n")}
        _, report, _ = build(DAY1, replace=replace)
        self.assertEqual(report["warnings"], ["reviewed operator AS208006 is absent from IPtoASN"])

    def test_old_format_provenance_uses_its_generation_time(self):
        _, _, first = build(DAY1)
        del first["source_status"]
        _, report, provenance = build(DAY1 + timedelta(days=2), first, drop=["ipdeny_ipv6"])
        self.assertEqual(report["source_status"]["ipdeny_ipv6"], {"status": "reused", "last_fresh": stamp(DAY1), "reason": "source payload missing"})
        self.assertEqual(provenance["source_status"]["ipdeny_ipv6"]["last_fresh"], stamp(DAY1))


class CheckSourcesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("check_sources", SCRIPT)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def run_check(self, status, last_fresh_days):
        last_fresh = None if last_fresh_days is None else stamp(DAY1 - timedelta(days=last_fresh_days))
        report = {"observed_at": stamp(DAY1), "warnings": [], "source_status": {
            "nro_delegated": {"status": "fresh", "last_fresh": stamp(DAY1), "reason": None},
            "provider_parspack": {"status": status, "last_fresh": last_fresh, "reason": None if status == "fresh" else "HTTP Error 403"},
        }}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coverage.json"
            path.write_text(json.dumps(report))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = self.module.main([str(path)])
        return code, output.getvalue()

    def test_exit_codes(self):
        self.assertEqual(self.run_check("fresh", 0), (0, ""))
        code, output = self.run_check("reused", 1)
        self.assertEqual(code, 0)
        self.assertIn("::warning::provider_parspack reused", output)
        code, output = self.run_check("reused", 4)
        self.assertEqual(code, 1)
        self.assertIn("provider_parspack has been stale for 4 days", output)
        code, output = self.run_check("dropped", None)
        self.assertEqual(code, 1)
        self.assertIn("provider_parspack is missing from the feed", output)


if __name__ == "__main__":
    unittest.main()
