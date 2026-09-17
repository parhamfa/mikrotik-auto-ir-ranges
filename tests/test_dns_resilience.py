import json
import unittest
from datetime import datetime, timedelta, timezone

from auto_ir_ranges import dns
from test_coverage import dns_response

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)
POLICY = {"services": [{"domain": "service.example"}], "dns_resolvers": {"a": "https://a.example", "b": "https://b.example"}}


def healthy(domain, resolver, endpoint, rrtype):
    return dns_response(domain, rrtype, "104.16.1.1" if resolver == "a" else "104.16.1.2") if rrtype == 1 else {"Status": 0, "Question": [{"name": domain, "type": rrtype}], "Answer": []}


class ResilientDNSTests(unittest.TestCase):
    def test_union_and_disagreement_report(self):
        raw = dns.collect(POLICY, lookup=healthy, clock=lambda: NOW)
        networks, _, report = dns.evaluate(raw, POLICY, now=NOW)
        self.assertEqual({str(n) for n in networks[4]}, {"104.16.1.1/32", "104.16.1.2/32"})
        self.assertEqual(len(report["resolver_disagreements"]), 1)

    def test_failure_uses_cache_without_advancing_timestamp_then_expires(self):
        raw = dns.collect(POLICY, lookup=healthy, clock=lambda: NOW)
        def failed(*_):
            raise OSError("offline")
        cached = dns.collect(POLICY, raw, lookup=failed, clock=lambda: NOW+timedelta(hours=24))
        cached = dns.collect(POLICY, cached, lookup=failed, clock=lambda: NOW+timedelta(hours=47))
        networks, records, _ = dns.evaluate(cached, POLICY, now=NOW+timedelta(hours=48))
        self.assertTrue(networks[4])
        self.assertTrue(all(r["observed_at"] == NOW.isoformat() and r["ttl"] == 60 and r["freshness"] == "cached" for r in records))
        networks, _, report = dns.evaluate(cached, POLICY, now=NOW+timedelta(hours=48, seconds=1))
        self.assertEqual(networks, {4: [], 6: []})
        self.assertEqual(report["unresolved_services"], ["service.example"])

    def test_one_failed_family_does_not_block_healthy_resolver(self):
        def partial(domain, resolver, endpoint, rrtype):
            if resolver == "a":
                raise OSError("timeout")
            return healthy(domain, resolver, endpoint, rrtype)
        raw = dns.collect(POLICY, lookup=partial, clock=lambda: NOW)
        networks, _, report = dns.evaluate(raw, POLICY, now=NOW)
        self.assertEqual([str(n) for n in networks[4]], ["104.16.1.2/32"])
        self.assertFalse(report["unresolved_services"])
        self.assertEqual(sum(r["status"] == "failure" for r in report["outcomes"]), 2)

    def test_replayed_success_is_reported_as_cached_and_changed_endpoint_does_not_reuse_it(self):
        raw = dns.collect(POLICY, lookup=healthy, clock=lambda: NOW)
        _, records, _ = dns.evaluate(raw, POLICY, now=NOW+timedelta(hours=24))
        self.assertTrue(all(r["freshness"] == "cached" for r in records))
        changed = {**POLICY, "dns_resolvers": {"a": "https://different.example"}}
        def failed(*_):
            raise OSError("offline")
        raw = dns.collect(changed, raw, lookup=failed, clock=lambda: NOW+timedelta(hours=1))
        self.assertEqual(dns.evaluate(raw, changed, now=NOW+timedelta(hours=1))[0], {4: [], 6: []})

    def test_successful_empty_answer_replaces_previous_addresses(self):
        raw = dns.collect(POLICY, lookup=healthy, clock=lambda: NOW)
        def empty(domain, resolver, endpoint, rrtype):
            return {"Status": 0, "Question": [{"name": domain, "type": rrtype}], "Answer": []}
        raw = dns.collect(POLICY, raw, lookup=empty, clock=lambda: NOW+timedelta(hours=1))
        self.assertFalse(dns.evaluate(raw, POLICY, now=NOW+timedelta(hours=1))[0][4])


if __name__ == "__main__":
    unittest.main()
