"""Opt-in large-page/DNS-expiry integration, after routeros_lab.py provisions CHR."""
import http.server
import json
import ssl
import threading
from pathlib import Path

from routeros_lab import API, ROOT, fixture, memberships
from auto_ir_ranges.delivery import build
from auto_ir_ranges.generator import GeneratedArtifacts, publish_artifacts
from auto_ir_ranges.errors import GenerationError


def main():
    work = ROOT/'build/chr'
    served = work/'capacity-served'
    served.mkdir(exist_ok=True)
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
    prior_dns = [{'address': row.split('/')[0]} for v in (4,6) for row in set(old[v].data.decode().splitlines()) - set(new[v].data.decode().splitlines())]
    (served/'coverage.json').write_text(json.dumps({'layers': {}, 'dns_records': prior_dns}))
    def artifact(feeds, when):
        raw, pages = build(feeds, generated_at=when, generator_version='capacity-test')
        return GeneratedArtifacts(feeds[4], feeds[6], b'{"schema":1}', b'{"layers":{}}', raw, pages)
    before = (served/'manifest-v2.json').read_bytes()
    try:
        publish_artifacts(artifact(fixture(1100,350,offset=300000), '2026-09-17T18:30:00Z'), served)
        raise AssertionError('non-DNS source loss was accepted')
    except GenerationError as exc:
        assert 'shrank' in str(exc)
        assert (served/'manifest-v2.json').read_bytes() == before
    candidate = artifact(new, '2026-09-17T19:00:00Z')
    publish_artifacts(candidate, served)
    manifest = json.loads((served/'manifest-v2.json').read_bytes())
    assert all(manifest[f'ipv{v}']['dns_only_shrink']['previous_count'] == old[v].count for v in (4,6))
    api.script('/system script run auto-ir-ranges-sync')
    assert memberships(api) == {v: set(f.data.decode().splitlines()) for v,f in new.items()}
    # Replay publication and updater: no immutable metadata conflicts, no list churn.
    publish_artifacts(candidate, served)
    before = {v: api.call('/ip/firewall/address-list/print' if v==4 else '/ipv6/firewall/address-list/print') for v in (4,6)}
    api.script('/system script run auto-ir-ranges-sync')
    assert before == {v: api.call('/ip/firewall/address-list/print' if v==4 else '/ipv6/firewall/address-list/print') for v in (4,6)}
    result = {'large_family_bytes': old[4].size, 'large_counts': [5100,900], 'dns_expiry_counts': [1100,350], 'non_dns_loss_rejected': True, 'exact_membership': True, 'idempotent': True}
    (work/'capacity-results.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)
    server.shutdown()


if __name__ == '__main__':
    main()
