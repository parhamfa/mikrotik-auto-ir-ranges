"""Opt-in RouterOS integration harness. Only connects to our loopback CHR.

Not part of unittest discovery. Boot a fresh CHR with port 24728 -> 8728,
then run `python tests/routeros_lab.py`. No production aliases are accepted.
"""
from __future__ import annotations

import hashlib
import http.server
import ipaddress
import json
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from auto_ir_ranges.delivery import build
from auto_ir_ranges.generator import Feed


class API:
    def __init__(self, port=24728):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=600)
        self.call('/login', name='admin', password='')

    def read(self, count):
        data = b''
        while len(data) < count:
            part = self.socket.recv(count-len(data))
            if not part:
                raise EOFError("RouterOS API disconnected")
            data += part
        return data

    def word(self):
        n = self.read(1)[0]
        if n & 0x80 == 0:
            pass
        elif n & 0xc0 == 0x80:
            n = ((n & 0x3f) << 8) + self.read(1)[0]
        elif n & 0xe0 == 0xc0:
            n = ((n & 0x1f) << 16) + int.from_bytes(self.read(2), 'big')
        elif n & 0xf0 == 0xe0:
            n = ((n & 0x0f) << 24) + int.from_bytes(self.read(3), 'big')
        else:
            n = int.from_bytes(self.read(4), 'big')
        return self.read(n).decode()

    def call(self, command, **attrs):
        words = [command] + ['='+k+'='+str(v) for k, v in attrs.items()]
        packet = b''
        for word in words:
            data = word.encode()
            n = len(data)
            length = bytes([n]) if n < 0x80 else (n|0x8000).to_bytes(2, 'big') if n < 0x4000 else (n|0xc00000).to_bytes(3, 'big') if n < 0x200000 else (n|0xe0000000).to_bytes(4, 'big')
            packet += length + data
        self.socket.sendall(packet+b'\0')
        rows, errors = [], []
        while True:
            sentence = []
            while True:
                word = self.word()
                if not word:
                    break
                sentence.append(word)
            values = dict(word[1:].split('=', 1) for word in sentence[1:] if word.startswith('='))
            if sentence[0] in ('!trap', '!fatal'):
                errors.append(values)
            elif sentence[0] == '!re':
                rows.append(values)
            elif sentence[0] == '!done':
                if errors:
                    raise RuntimeError(errors)
                return rows or ([values] if values else [])

    def script(self, source, name='lab-action'):
        for row in self.call('/system/script/print'):
            if row['name'] == name:
                self.call('/system/script/remove', **{'.id': row['.id']})
        result = self.call('/system/script/add', name=name, source=source, policy='read,write,test,policy')
        # RouterOS API execution changes /tool fetch as-value semantics. Exercise
        # the CLI/scheduler script context used by real installations through SSH.
        command = ':onerror e in={ /system script run "'+name+'" } do={ :put ("LAB-ERROR:" . $e) }'
        run = subprocess.run(['ssh', '-F', '/dev/null', '-p', '24222', '-i', str(ROOT/'build/chr/lab-key'), '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile='+str(ROOT/'build/chr/known_hosts'), '-o', 'BatchMode=yes', '-o', 'LogLevel=ERROR', 'admin@127.0.0.1', command], capture_output=True, text=True, timeout=600)
        if run.returncode or 'LAB-ERROR:' in run.stdout:
            raise RuntimeError(run.stdout+run.stderr)
        return run.stdout


def memberships(api):
    return {v: {str(ipaddress.ip_network(row['address'])) for row in api.call('/ip/firewall/address-list/print' if v == 4 else '/ipv6/firewall/address-list/print') if row['list'] == f'Iran_IPV{v}'} for v in (4, 6)}


def fixture(count4=1100, count6=350, offset=0):
    feeds = {}
    for v, count in ((4, count4), (6, count6)):
        start = int(ipaddress.ip_address('8.0.0.0' if v == 4 else '2606:4700::'))+offset
        data = ('\n'.join(str(ipaddress.ip_network((start+i*2, 32 if v == 4 else 128))) for i in range(count))+'\n').encode()
        feeds[v] = Feed(data, count)
    return feeds


def serve_fixture(directory, feeds, generation_time):
    manifest, pages = build(feeds, generated_at=generation_time, generator_version='integration-test', page_bytes=4096)
    for path, data in pages.items():
        target = directory/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (directory/'manifest-v2.json').write_bytes(manifest)
    return json.loads(manifest)


def main():
    work = ROOT/'build/chr'
    served = work/'served'
    served.mkdir(parents=True, exist_ok=True)
    key, cert = work/'server.key', served/'lab.crt'
    if not cert.exists():
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(cert), '-days', '2', '-subj', '/CN=Iran feed disposable lab', '-addext', 'subjectAltName=IP:10.0.2.2', '-addext', 'basicConstraints=critical,CA:TRUE'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        key.chmod(0o600)
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(served), **kw)
        def log_message(self, *a):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 24443), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api = API()
    assert api.call('/system/identity/print')[0]['name'] == 'CHR', 'Only the disposable CHR is supported'
    print('RouterOS', api.call('/system/resource/print')[0]['version'], flush=True)
    if not (work/'lab-key').exists():
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'ir-ranges-disposable-lab', '-f', str(work/'lab-key')], check=True)
        api.call('/file/add', name='lab-key.pub', type='file', contents=(work/'lab-key.pub').read_text())
        api.call('/user/ssh-keys/import', user='admin', **{'public-key-file': 'lab-key.pub'})
    # Pin this fresh local VM's host key before executing any script.
    if not (work/'known_hosts').exists():
        scanned = subprocess.check_output(['ssh-keyscan', '-p', '24222', '127.0.0.1'], stderr=subprocess.DEVNULL)
        (work/'known_hosts').write_bytes(scanned)
    # The certificate is locally generated, installed only in the disposable VM.
    api.script('/file remove [find where name="lab.crt"]; /file add name=lab.crt type=file; /file set [find where name="lab.crt"] contents='+json.dumps(cert.read_text())+'; /certificate import file-name=lab.crt passphrase=""')
    feeds = fixture()
    manifest = serve_fixture(served, feeds, '2026-09-17T12:00:00Z')
    installer = (ROOT/'routeros/install.rsc').read_text().replace('https://raw.githubusercontent.com/parhamfa/mikrotik-auto-ir-ranges/data/', 'https://10.0.2.2:24443/')
    api.script(installer, 'lab-install')
    expected = {v: set(feed.data.decode().splitlines()) for v, feed in feeds.items()}
    assert memberships(api) == expected
    results = ['multi-page install exact membership']
    before = {v: api.call('/ip/firewall/address-list/print' if v==4 else '/ipv6/firewall/address-list/print') for v in (4,6)}
    api.script('/system script run auto-ir-ranges-sync')
    after = {v: api.call('/ip/firewall/address-list/print' if v==4 else '/ipv6/firewall/address-list/print') for v in (4,6)}
    assert before == after
    results.append('repeat-run idempotence including entry IDs')
    print(results, flush=True)
    # Corrupt/missing/mixed-generation/capacity failures must leave BOTH families intact.
    page = served/manifest['ipv6']['pages'][-1]['file']
    content = page.read_bytes()
    original = (served/'manifest-v2.json').read_bytes()
    def reject(label):
        try:
            api.script('/system script run auto-ir-ranges-sync')
        except RuntimeError as exc:
            assert memberships(api) == expected, label
            results.append(label)
            print(label, str(exc)[:180], flush=True)
        else:
            raise AssertionError('invalid generation accepted: '+label)
    page.write_bytes(bytes([content[0] ^ 1])+content[1:])
    reject('corrupt last page leaves lists unchanged')
    page.unlink()
    reject('missing last page leaves lists unchanged')
    page.write_bytes(content)
    changed = json.loads(original)
    changed['ipv6']['pages'][-1]['generation'] = '0'*64
    (served/'manifest-v2.json').write_text(json.dumps(changed))
    reject('mixed generation leaves lists unchanged')
    changed = json.loads(original)
    changed['ipv4']['count'] = 50001
    (served/'manifest-v2.json').write_text(json.dumps(changed))
    reject('capacity ceiling leaves lists unchanged')
    (served/'manifest-v2.json').write_bytes(original)
    sync = next(r for r in api.call('/system/script/print') if r['name'] == 'auto-ir-ranges-sync')
    real = sync['source']
    for variable in ('memoryRequired', 'storageRequired'):
        injected = real.replace(':local '+variable+' (', ':local '+variable+' (1099511627776 + ')
        api.call('/system/script/set', **{'.id': sync['.id'], 'source': injected})
        reject('insufficient '+variable+' leaves lists unchanged')
    api.call('/system/script/set', **{'.id': sync['.id'], 'source': real})
    # Interrupt the real add phase after partial mutation; next run must use journal.
    next_feeds = fixture(1200, 400, offset=100000)
    target = serve_fixture(served, next_feeds, '2026-09-17T13:00:00Z')
    sync = next(r for r in api.call('/system/script/print') if r['name'] == 'auto-ir-ranges-sync')
    real = sync['source']
    interrupted = real.replace(':set addedV4 ($addedV4 + 1)', ':set addedV4 ($addedV4 + 1); :if ($addedV4 = 17) do={ :error "lab interruption" }')
    api.call('/system/script/set', **{'.id': sync['.id'], 'source': interrupted})
    try:
        api.script('/system script run auto-ir-ranges-sync')
        raise AssertionError('interruption hook not reached')
    except RuntimeError as exc:
        assert 'lab interruption' in str(exc)
    partial = memberships(api)
    assert expected[4] <= partial[4] and expected[6] <= partial[6], 'old entries pruned before all additions'
    api.call('/system/script/set', **{'.id': sync['.id'], 'source': real})
    # A newer pointer must not interfere with finishing the pending immutable generation.
    serve_fixture(served, fixture(1300,450,offset=200000), '2026-09-17T14:00:00Z')
    api.script('/system script run auto-ir-ranges-sync')
    assert memberships(api) == {v: set(f.data.decode().splitlines()) for v,f in next_feeds.items()}
    results.append('interrupted add phase resumes pinned generation before newer pointer')
    # Interrupt pruning after all new addresses have been staged.
    latest = fixture(1300,450,offset=200000)
    prune_interrupt = real.replace(':set removedV4 ($removedV4 + 1)', ':set removedV4 ($removedV4 + 1); :if ($removedV4 = 17) do={ :error "lab prune interruption" }')
    api.call('/system/script/set', **{'.id': sync['.id'], 'source': prune_interrupt})
    try:
        api.script('/system script run auto-ir-ranges-sync')
        raise AssertionError('prune interruption not reached')
    except RuntimeError as exc:
        assert 'lab prune interruption' in str(exc)
    staged = memberships(api)
    assert all(set(f.data.decode().splitlines()) <= staged[v] for v,f in latest.items())
    api.call('/system/script/set', **{'.id': sync['.id'], 'source': real})
    api.script('/system script run auto-ir-ranges-sync')
    assert memberships(api) == {v: set(f.data.decode().splitlines()) for v,f in latest.items()}
    results.append('interrupted prune preserves desired coverage and rerun finishes')
    (work/'results.json').write_text(json.dumps({'routeros': api.call('/system/resource/print')[0]['version'], 'checks': results}, indent=2)+'\n')
    print(json.dumps(results), flush=True)
    server.shutdown()


if __name__ == '__main__':
    main()
