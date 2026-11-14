from __future__ import annotations

import gzip
import ipaddress
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from auto_ir_ranges.coverage import (
    coverage_report,
    load_policy,
    parse_delegated,
    parse_provider,
    subtract_networks,
)
from auto_ir_ranges.generator import (
    GenerationError,
    generate_from_sources,
    parse_iptoasn_rows,
    publish_artifacts,
)

NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)
LIMITS = {v: {"min_count": 1, "max_count": 100, "max_bytes": 10_000} for v in (4, 6)}
PROVIDER = {"id": "arvancloud", "version": 4, "url": "https://example.com/ips", "min_count": 1, "max_count": 100, "min_prefixlen": 16}


def delegated(*rows: str, count: int | None = None, date: str = "20260916") -> bytes:
    header = f"2|nro|1|{len(rows) if count is None else count}|20200101|{date}|+0000"
    return ("\n".join((header, *rows)) + "\n").encode()


def generation_inputs() -> tuple[dict, dict]:
    policy = {
        "registry_url": "https://example.com/nro", "registry_min_asns": 1,
        "operators": [{"asns": {"208006": "ARVANCLOUD-CDN"}}],
        "providers": [PROVIDER],
    }
    payloads = {
        "ipdeny_ipv4": b"2.144.0.0/14\n",
        "ipdeny_ipv6": b"2a0c:a7c0::/29\n",
        "iptoasn_ipv4": gzip.compress(b"2.144.0.0\t2.147.255.255\t100\tIR\tIR-EXAMPLE\n185.215.232.0\t185.215.235.255\t208006\tAE\tARVANCLOUD-CDN\n"),
        "iptoasn_ipv6": gzip.compress(b"2a0c:a7c0::\t2a0c:a7c7:ffff:ffff:ffff:ffff:ffff:ffff\t100\tIR\tIR-EXAMPLE\n"),
        "nro_delegated": delegated("ripencc|IR|asn|100|1|20200101|assigned|ir", "ripencc|AE|asn|208006|1|20200101|assigned|arvan"),
        "provider_arvancloud": b"185.215.232.0/22\n193.24.119.0/29\n",
    }
    return payloads, policy


class CoverageTests(unittest.TestCase):
    def test_foreign_registered_operator_is_selected_without_including_unrelated_ae_asn(self):
        rows = "185.215.232.0\t185.215.235.255\t208006\tAE\tARVANCLOUD-CDN\n8.8.8.0\t8.8.8.255\t15169\tUS\tGOOGLE\n"
        networks, asns = parse_iptoasn_rows(rows, 4, include_asns={208006}, expected_descriptions={208006: "ARVANCLOUD-CDN"})
        self.assertEqual([str(n) for n in networks], ["185.215.232.0/22"])
        self.assertEqual(asns, {208006})

    def test_reviewed_asn_transfer_is_left_out_and_recorded(self):
        rows = "2.144.0.0\t2.147.255.255\t100\tIR\tIR-EXAMPLE\n185.215.232.0\t185.215.235.255\t208006\tAE\tUNRELATED\n"
        mismatches = {}
        networks, asns = parse_iptoasn_rows(rows, 4, include_asns={208006}, expected_descriptions={208006: "ARVANCLOUD-CDN"}, mismatches=mismatches)
        self.assertEqual([str(n) for n in networks], ["2.144.0.0/14"])
        self.assertEqual(asns, {100})
        self.assertEqual(mismatches, {208006: "UNRELATED"})

    def test_registry_country_and_holder_identity_are_independent(self):
        payload = delegated(
            "ripencc|IR|asn|100|2|20200101|assigned|iran",
            "ripencc|GB|ipv4|5.1.1.0|256|20200101|allocated|iran",
            "ripencc|AE|asn|208006|1|20200101|assigned|arvan",
            "ripencc|AE|ipv4|185.215.232.0|1024|20200101|allocated|arvan",
            "ripencc|AE|asn|57568|1|20200101|assigned|arvan",
            "arin|US|ipv4|8.8.8.0|256|20200101|allocated|arvan",
            "ripencc|IR|ipv4|9.9.9.0|256|20200101|reserved|iran",
        )
        networks, asns, _ = parse_delegated(payload, anchors={208006}, now=NOW)
        self.assertEqual(asns, {100, 101, 208006, 57568})
        self.assertEqual({str(n) for n in networks[4]}, {"5.1.1.0/24", "185.215.232.0/22"})

    def test_truncated_registry_fails_instead_of_silently_losing_operators(self):
        with self.assertRaisesRegex(GenerationError, "truncated registry"):
            parse_delegated(delegated("ripencc|IR|asn|100|1|20200101|assigned|ir", count=2), now=NOW)

    def test_stale_registry_fails(self):
        with self.assertRaisesRegex(GenerationError, "stale"):
            parse_delegated(delegated(date="20260901"), now=NOW)

    def test_provider_html_wrong_family_private_and_default_route_fail(self):
        for data in [b"<html>maintenance</html>\n", b"::/0\n", b"10.0.0.0/24\n", b"0.0.0.0/0\n", b"185.215.232.0/22\n185.215.232.0/22\n"]:
            with self.subTest(data=data), self.assertRaises(GenerationError):
                parse_provider(data, PROVIDER)

    def test_exact_subtraction_handles_several_smaller_covering_prefixes(self):
        network = ipaddress.ip_network
        self.assertEqual(subtract_networks([network("185.215.232.0/22")], [network("185.215.232.0/23"), network("185.215.234.0/23")], 4), [])
        self.assertEqual([str(n) for n in subtract_networks([network("185.215.232.0/22")], [network("185.215.232.0/23")], 4)], ["185.215.234.0/23"])

    def test_policy_with_website_dns_keys_is_rejected(self):
        for key, value in (("services", []), ("dns_resolvers", {})):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "policy.json"
                path.write_text(json.dumps({"schema": 2, "registry_url": "https://example.com/nro", "registry_min_asns": 1, "operators": [], "providers": [], key: value}))
                with self.assertRaisesRegex(GenerationError, f"website DNS tracking was removed; delete the '{key}' key"):
                    load_policy(path)

    def test_coverage_gate_catches_lost_provider_subnet(self):
        n = ipaddress.ip_network("185.215.232.0/22")
        with self.assertRaisesRegex(GenerationError, "coverage gap"):
            coverage_report({"provider": (4, [n])}, {4: [], 6: []}, {4: [], 6: []})

    def test_both_arvan_regressions_are_fixed_end_to_end(self):
        payloads, policy = generation_inputs()
        artifacts = generate_from_sources(payloads, policy=policy, generated_at="2026-09-16T00:00:00Z", limits=LIMITS)
        self.assertIn(b"185.215.232.0/22\n", artifacts.ipv4.data)
        self.assertIn(b"193.24.119.0/29\n", artifacts.ipv4.data)
        self.assertNotIn(b"8.8.8.0/24", artifacts.ipv4.data)
        report = json.loads(artifacts.coverage)
        for key in ("universe_coverage_percent", "known_source_completeness_percent", "measured_lab_recall"):
            self.assertNotIn(key, report)
        self.assertEqual(report["layers"]["provider_arvancloud"]["missing_prefixes"], [])

    def test_coverage_report_has_no_website_dns_or_fixed_accuracy_keys(self):
        payloads, policy = generation_inputs()
        artifacts = generate_from_sources(payloads, policy=policy, generated_at="2026-09-16T00:00:00Z", limits=LIMITS)
        report = json.loads(artifacts.coverage)
        removed = {"service_domains", "dns_records", "dns", "discovery", "universe_coverage_percent", "known_source_completeness_percent", "measured_lab_recall"}
        self.assertEqual(removed & set(report), set())
        self.assertEqual([name for name in report["layers"] if name.startswith("service_dns")], [])
        self.assertNotIn("services", json.loads(artifacts.manifest)["coverage"])

    def test_missing_provider_payload_without_previous_build_drops_only_that_layer(self):
        payloads, policy = generation_inputs()
        del payloads["provider_arvancloud"]
        artifacts = generate_from_sources(payloads, policy=policy, generated_at="2026-09-16T00:00:00Z", limits=LIMITS, source_failures={"provider_arvancloud": "HTTP Error 403"})
        self.assertNotIn(b"193.24.119.0/29", artifacts.ipv4.data)
        self.assertIn(b"2.144.0.0/14\n", artifacts.ipv4.data)
        report = json.loads(artifacts.coverage)
        self.assertEqual(report["source_status"]["provider_arvancloud"], {"status": "dropped", "last_fresh": None, "reason": "HTTP Error 403"})
        self.assertNotIn("provider_arvancloud", json.loads(artifacts.manifest)["sources"])

    def test_provider_shrink_no_longer_blocks_publication(self):
        payloads, policy = generation_inputs()
        artifacts = generate_from_sources(payloads, policy=policy, generated_at="2026-09-16T00:00:00Z", limits=LIMITS)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            old_report = json.loads(artifacts.coverage)
            old_report["layers"]["provider_arvancloud"]["unique_addresses"] = "4096"
            (target / "coverage.json").write_text(json.dumps(old_report))
            self.assertTrue(publish_artifacts(artifacts, target))
            self.assertEqual((target / "ir-ipv4.zone").read_bytes(), artifacts.ipv4.data)


if __name__ == "__main__":
    unittest.main()
