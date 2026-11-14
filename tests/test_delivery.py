import copy
import hashlib
import ipaddress
import json
import tempfile
import unittest
from pathlib import Path

from auto_ir_ranges.delivery import build, validate, preflight, MAX_ENTRIES
from auto_ir_ranges.generator import Feed, GeneratedArtifacts, publish_artifacts, validate_removal
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

    def test_removal_may_not_exceed_feed_size(self):
        old = [ipaddress.ip_network(f"9.0.{i}.0/24") for i in range(10)]
        validate_removal(old, old[:5], 4)  # removes 5, keeps 5
        with self.assertRaisesRegex(GenerationError, "IPv4 update removes 6 entries but the feed contains only 4"):
            validate_removal(old, old[:4], 4)
        fresh = [ipaddress.ip_network(f"10.0.{i}.0/24") for i in range(9)]
        with self.assertRaisesRegex(GenerationError, "routers refuse"):
            validate_removal(old, fresh, 4)  # 10 removed, 9 in feed, no 50% shrink

    def test_publisher_refuses_high_turnover_and_accepts_small_change(self):
        def artifact(rows, when):
            nets = {4: [ipaddress.ip_network(r) for r in rows], 6: [ipaddress.ip_network(f"2606:4700:{i:x}::/48") for i in range(350)]}
            feeds = {v: Feed(("\n".join(map(str, sorted(n, key=lambda x: (int(x.network_address), x.prefixlen)))) + "\n").encode(), len(n)) for v, n in nets.items()}
            raw, pages = build(feeds, generated_at=when, generator_version="test")
            return GeneratedArtifacts(feeds[4], feeds[6], b'{"generated_at":"' + when.encode() + b'"}', paged_manifest=raw, pages=pages)
        old = [str(ipaddress.ip_network((int(ipaddress.ip_address("9.0.0.0")) + i*256, 24))) for i in range(1200)]
        new = [str(ipaddress.ip_network((int(ipaddress.ip_address("20.0.0.0")) + i*256, 24))) for i in range(1000)]
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            self.assertTrue(publish_artifacts(artifact(old, "t1"), target))
            before = (target/"manifest-v2.json").read_bytes()
            turnover = old[:50] + new[:950]  # 1000 rows (more than half of 1200), but 1150 removed
            with self.assertRaisesRegex(GenerationError, "removes 1150 entries but the feed contains only 1000"):
                publish_artifacts(artifact(turnover, "t2"), target)
            self.assertEqual((target/"manifest-v2.json").read_bytes(), before)
            small = old[50:] + new[:50]
            self.assertTrue(publish_artifacts(artifact(small, "t3"), target))
            self.assertNotEqual((target/"manifest-v2.json").read_bytes(), before)
            self.assertEqual(len(validate((target/"manifest-v2.json").read_bytes(), lambda p: (target/p).read_bytes())[4]), 1200)

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
