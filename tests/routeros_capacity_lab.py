"""Opt-in large-page and shrink-guard integration, after routeros_lab.py provisions CHR."""
import http.server
import json
import shutil
import ssl
import threading
from pathlib import Path

from routeros_lab import API, ROOT, fixture, memberships
from auto_ir_ranges.delivery import build
from auto_ir_ranges.generator import Feed, GeneratedArtifacts, publish_artifacts
from auto_ir_ranges.errors import GenerationError


def main():
    work = ROOT/'build/chr'
    served = work/'capacity-served'
    shutil.rmtree(served, ignore_errors=True)  # no published files left over from an earlier run
    served.mkdir()
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(served), **kw)
        def log_message(self, *a):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 24443), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(work/'served/lab.crt', work/'server.key')
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api = API()
    assert api.call('/system/identity/print')[0]['name'] == 'CHR'
    old, new = fixture(5100, 900), fixture(1100, 350)
    raw, pages = build(old, generated_at='2026-09-17T18:00:00Z', generator_version='capacity-test')
    assert old[4].size > 65536
    assert max(p['bytes'] for p in json.loads(raw)['ipv4']['pages']) > 48000
    for name, data in {**pages, 'manifest-v2.json': raw}.items():
        path = served/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    installer = (ROOT/'routeros/install.rsc').read_text().replace('https://raw.githubusercontent.com/parhamfa/mikrotik-auto-ir-ranges/data/', 'https://10.0.2.2:24443/')
    api.script(installer, 'lab-capacity-install')
    assert memberships(api) == {v: set(f.data.decode().splitlines()) for v,f in old.items()}
    print('Exact 5,100 IPv4 / 900 IPv6 membership; family larger than 64 KiB; full-sized pages.', flush=True)

    def artifact(feeds, when):
        raw, pages = build(feeds, generated_at=when, generator_version='capacity-test')
        return GeneratedArtifacts(feeds[4], feeds[6], b'{"schema":1}', b'{"layers":{}}', raw, pages)
    def lists():
        return {v: api.call('/ip/firewall/address-list/print' if v==4 else '/ipv6/firewall/address-list/print') for v in (4,6)}
    # Both former shrink candidates (a disjoint loss, and the subset that used to pass as DNS expiry) are refused.
    before = (served/'manifest-v2.json').read_bytes()
    for label, feeds in (('disjoint', fixture(1100,350,offset=300000)), ('subset', new)):
        try:
            publish_artifacts(artifact(feeds, '2026-09-17T18:30:00Z'), served)
            raise AssertionError(f'{label} shrink was published')
        except GenerationError as exc:
            assert 'shrank' in str(exc) or 'removes' in str(exc), str(exc)
            assert (served/'manifest-v2.json').read_bytes() == before
            print(f'publisher refused {label}:', str(exc)[:120], flush=True)
    # The same subset feed served directly (bypassing the publisher) is refused by the router.
    raw, pages = build(new, generated_at='2026-09-17T18:45:00Z', generator_version='capacity-test')
    for name, data in {**pages, 'manifest-v2.json': raw}.items():
        (served/name).parent.mkdir(parents=True, exist_ok=True)
        (served/name).write_bytes(data)
    snapshot = lists()
    try:
        api.script('/system script run auto-ir-ranges-sync')
        raise AssertionError('router accepted a shrink the publisher refuses')
    except RuntimeError as exc:
        assert 'shrink guard' in str(exc), str(exc)
        router_refusal = str(exc).strip().replace('LAB-ERROR:', '')[:120]
    assert lists() == snapshot
    print('router refused direct feed:', router_refusal, flush=True)
    (served/'manifest-v2.json').write_bytes(before)
    # A legitimate smaller change: drop 1,100 IPv4 and 100 IPv6 rows, keep the rest.
    smaller = {}
    for v, keep in ((4, 4000), (6, 800)):
        rows = old[v].data.decode().splitlines()[:keep]
        smaller[v] = Feed(('\n'.join(rows)+'\n').encode(), keep)
    candidate = artifact(smaller, '2026-09-17T19:00:00Z')
    publish_artifacts(candidate, served)
    api.script('/system script run auto-ir-ranges-sync')
    assert memberships(api) == {v: set(f.data.decode().splitlines()) for v,f in smaller.items()}
    # Replay publication and updater: no immutable metadata conflicts, no list churn.
    publish_artifacts(candidate, served)
    before = lists()
    api.script('/system script run auto-ir-ranges-sync')
    assert before == lists()
    result = {'large_family_bytes': old[4].size, 'large_counts': [5100,900], 'publisher_refused': ['disjoint 1100/350', 'subset 1100/350'], 'router_refused_direct_subset': router_refusal, 'smaller_change_counts': [4000,800], 'exact_membership': True, 'idempotent': True}
    (work/'capacity-results.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)
    server.shutdown()


if __name__ == '__main__':
    main()
