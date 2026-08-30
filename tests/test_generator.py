from __future__ import annotations

import gzip
import hashlib
import ipaddress
import json
import tempfile
import unittest
from pathlib import Path

from auto_ir_ranges.generator import (
    Feed,
    GeneratedArtifacts,
    GenerationError,
    collapse_networks,
    generate_from_sources,
    parse_and_validate_zone,
    parse_country_zone,
    parse_iptoasn_rows,
    publish_artifacts,
    serialize_zone,
    validate_shrink,
)


FIXTURES = Path(__file__).parent / "fixtures"
TEST_LIMITS = {
    4: {"min_count": 1, "max_count": 100, "max_bytes": 10_000},
    6: {"min_count": 1, "max_count": 100, "max_bytes": 10_000},
}


class GeneratorTests(unittest.TestCase):
    def test_iptoasn_filters_country_and_summarizes_ranges(self) -> None:
        networks, asns = parse_iptoasn_rows(
            (FIXTURES / "iptoasn-v4.tsv").read_text(), 4
        )
        rendered = {str(network) for network in networks}
        self.assertEqual(asns, {100, 300})
        self.assertIn("20.0.0.0/24", rendered)
        self.assertIn("40.0.0.1/32", rendered)
        self.assertIn("40.0.0.2/31", rendered)
        self.assertIn("40.0.0.4/31", rendered)
        self.assertIn("40.0.0.6/32", rendered)
        self.assertNotIn("30.0.0.0/24", rendered)

    def test_collapse_is_numeric_and_deterministic(self) -> None:
        networks = parse_country_zone((FIXTURES / "ipdeny-v4.zone").read_bytes(), 4)
        forward = serialize_zone(collapse_networks(networks, 4))
        reverse = serialize_zone(collapse_networks(reversed(networks), 4))
        self.assertEqual(forward, b"10.0.0.0/23\n")
        self.assertEqual(forward, reverse)

    def test_invalid_country_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(GenerationError, "invalid country CIDR"):
            parse_country_zone("192.0.2.0/24\nnot-a-prefix\n", 4)

    def test_invalid_relevant_iptoasn_row_is_rejected(self) -> None:
        with self.assertRaisesRegex(GenerationError, "invalid Iranian IPtoASN row"):
            parse_iptoasn_rows("bad\t192.0.2.1\t123\tIR\tbroken\n", 4)

    def test_zone_validation_rejects_noncanonical_and_wrong_family(self) -> None:
        with self.assertRaisesRegex(GenerationError, "invalid generated CIDR"):
            parse_and_validate_zone(b"192.0.2.1/24\n", 4, limits=TEST_LIMITS)
        with self.assertRaisesRegex(GenerationError, "wrong family"):
            parse_and_validate_zone(b"2001:db8::/32\n", 4, limits=TEST_LIMITS)

    def test_shrink_guard(self) -> None:
        validate_shrink(50, 100, 4)
        with self.assertRaisesRegex(GenerationError, "shrank"):
            validate_shrink(49, 100, 4)

    def test_fixture_generation_has_valid_manifest_and_hashes(self) -> None:
        payloads = {
            "ipdeny_ipv4": (FIXTURES / "ipdeny-v4.zone").read_bytes(),
            "ipdeny_ipv6": (FIXTURES / "ipdeny-v6.zone").read_bytes(),
            "iptoasn_ipv4": gzip.compress((FIXTURES / "iptoasn-v4.tsv").read_bytes()),
            "iptoasn_ipv6": gzip.compress((FIXTURES / "iptoasn-v6.tsv").read_bytes()),
        }
        artifacts = generate_from_sources(
            payloads,
            generated_at="2026-01-02T03:04:05Z",
            limits=TEST_LIMITS,
        )
        manifest = json.loads(artifacts.manifest)
        self.assertEqual(manifest["schema"], 1)
        self.assertEqual(manifest["generated_at"], "2026-01-02T03:04:05Z")
        self.assertEqual(manifest["ipv4"]["sha512"], hashlib.sha512(artifacts.ipv4.data).hexdigest())
        self.assertEqual(manifest["ipv6"]["bytes"], len(artifacts.ipv6.data))
        self.assertEqual(artifacts.ipv4.data.splitlines()[0], b"10.0.0.0/23")

    def test_unchanged_publish_preserves_manifest(self) -> None:
        v4_networks = [
            ipaddress.IPv4Network((int(ipaddress.IPv4Address("10.0.0.0")) + index * 256, 24))
            for index in range(1_000)
        ]
        v6_networks = [
            ipaddress.IPv6Network((int(ipaddress.IPv6Address("2001:db8::")) + index * (1 << 80), 48))
            for index in range(300)
        ]
        v4 = Feed(serialize_zone(v4_networks), len(v4_networks))
        v6 = Feed(serialize_zone(v6_networks), len(v6_networks))
        first = GeneratedArtifacts(v4, v6, b'{"generated_at":"first"}\n')
        second = GeneratedArtifacts(v4, v6, b'{"generated_at":"second"}\n')
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            self.assertTrue(publish_artifacts(first, output))
            self.assertFalse(publish_artifacts(second, output))
            self.assertEqual((output / "manifest.json").read_bytes(), first.manifest)


if __name__ == "__main__":
    unittest.main()
