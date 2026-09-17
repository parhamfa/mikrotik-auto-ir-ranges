import copy
import hashlib
import ipaddress
import json
import tempfile
import unittest
from pathlib import Path

from auto_ir_ranges.delivery import build, validate, preflight, MAX_ENTRIES, dns_shrink_proof
from auto_ir_ranges.generator import Feed, GeneratedArtifacts, publish_artifacts
from auto_ir_ranges.errors import GenerationError


def sample(count=100):
    return {v: Feed(("\n".join(str(ipaddress.ip_network((int(ipaddress.ip_address("8.0.0.0" if v == 4 else "2606:4700::"))+i*2, 32 if v == 4 else 128))) for i in range(count))+"\n").encode(), count) for v in (4, 6)}


class DeliveryTests(unittest.TestCase):
    def test_many_pages_and_exact_membership(self):
        feeds = sample()
        raw, pages = build(feeds, generated_at="2026-09-17", generator_version="test", page_bytes=256)
        result = validate(raw, pages.__getitem__)
        self.assertGreater(len(json.loads(raw)["ipv6"]["pages"]), 1)
        self.assertEqual({str(n) for n in result[6]}, set(feeds[6].data.decode().splitlines()))

    def test_missing_corrupt_reordered_and_mixed_generation_pages_fail(self):
        raw, pages = build(sample(), generated_at="2026-09-17", generator_version="test", page_bytes=256)
        manifest = json.loads(raw)
        first = manifest["ipv4"]["pages"][0]["file"]
        broken = dict(pages)
        del broken[first]
        with self.assertRaises(GenerationError):
            validate(raw, broken.__getitem__)
        broken[first] = pages[first]+b"8.8.8.8/32\n"
        with self.assertRaisesRegex(GenerationError, "mismatch"):
            validate(raw, broken.__getitem__)
        for field, value in (("generation", "0"*64), ("number", 2), ("family", 6), ("file", "../secret")):
            mutated = copy.deepcopy(manifest)
            mutated["ipv4"]["pages"][0][field] = value
            with self.assertRaises(GenerationError):
                validate(json.dumps(mutated).encode(), pages.__getitem__)

    def test_capacity_never_truncates(self):
        feeds = sample(2)
        feeds[4] = Feed(feeds[4].data, MAX_ENTRIES+1)
        with self.assertRaisesRegex(GenerationError, "capacity"):
            build(feeds, generated_at="now", generator_version="test")
        with self.assertRaisesRegex(GenerationError, "insufficient"):
            preflight([50000, 50000], [1000, 300], free_memory=20_000_000, free_storage=100_000_000)
        self.assertGreater(preflight([1000, 300], [1000, 300], free_memory=100_000_000, free_storage=100_000_000)["memory"], 0)

    def test_dns_only_expiry_can_shrink_but_loss_of_core_space_cannot(self):
        old = [ipaddress.ip_network("8.8.8.0/24"), *[ipaddress.ip_network(f"9.0.0.{i}/32") for i in range(0, 200, 2)]]
        desired = old[:1]
        observations = [{"address": str(n.network_address)} for n in old[1:]]
        proof = dns_shrink_proof(old, desired, observations, 4)
        self.assertEqual(proof["previous_count"], 101)
        self.assertEqual(proof["removed_addresses"], "100")
        self.assertIsNone(dns_shrink_proof(old, [], observations, 4))

    def test_legacy_freezes_at_last_complete_generation(self):
        feeds = sample(5100)
        raw, pages = build(feeds, generated_at="2026-09-17", generator_version="test")
        artifact = GeneratedArtifacts(feeds[4], feeds[6], b'{}', paged_manifest=raw, pages=pages)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            # Previous complete v1 files respect the production minimum counts.
            old = sample(1200)
            for v in (4, 6):
                (target/f"ir-ipv{v}.zone").write_bytes(old[v].data)
            (target/"manifest.json").write_bytes(b'{"old":true}')
            publish_artifacts(artifact, target)
            self.assertEqual((target/"manifest.json").read_bytes(), b'{"old":true}')
            self.assertEqual((target/"ir-ipv4.zone").read_bytes(), old[4].data)
            self.assertTrue(json.loads((target/"legacy-status.json").read_bytes())["upgrade_required"])
            self.assertEqual(len(validate((target/"manifest-v2.json").read_bytes(), lambda p: (target/p).read_bytes())[4]), 5100)
            before = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*') if p.is_file()}
            self.assertFalse(publish_artifacts(artifact, target))
            self.assertEqual(before, {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*') if p.is_file()})


if __name__ == "__main__":
    unittest.main()
