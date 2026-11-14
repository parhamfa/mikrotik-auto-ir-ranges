"""Opt-in RouterOS integration harness. Only connects to our loopback CHR.

Not part of unittest discovery. Boot a fresh CHR with port 24728 -> 8728,
then run `python tests/routeros_lab.py`. No production aliases are accepted.
The CHR's identity must be `CHR` (older images default to `MikroTik`; rename it).
Covers fresh install, repeat run, changed feed, interrupted runs, refusals, a
scheduler-started sync and the upgrade from a stuck v2.0.1 (git tag v2.0.1, identical to main before 2.1.0).
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


def reboot(api):
    api.call('/system/reboot')
    api.socket.close()
    time.sleep(3)
    deadline = time.monotonic()+600
    while time.monotonic() < deadline:
        try:
            fresh = API()
            assert fresh.call('/system/identity/print')[0]['name'] == 'CHR'
            return fresh
        except (OSError, EOFError, RuntimeError):
            time.sleep(2)
    raise TimeoutError('Disposable CHR did not return after reboot')


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


# Mixed-prefix feeds: one non-overlapping slot per row (a /16 for IPv4, a /48
# for IPv6). Host rows (/32, /128) exercise RouterOS's address normalisation.
V4_PREFIXES = (24, 22, 20, 32, 16, 28, 30, 23, 21, 26)
V6_PREFIXES = (48, 56, 64, 128, 52, 60, 49)


def slot_network(v, slot, prefix):
    base = int(ipaddress.ip_address('8.0.0.0' if v == 4 else '2a01:5e00::'))
    return ipaddress.ip_network((base+slot*(1 << (16 if v == 4 else 80)), prefix))


def mixed_rows(v, slots, shift=0):
    prefixes = V4_PREFIXES if v == 4 else V6_PREFIXES
    return {slot: prefixes[(slot+shift) % len(prefixes)] for slot in slots}


def feeds_from(rows):
    feeds = {}
    for v in (4, 6):
        networks = sorted((slot_network(v, slot, prefix) for slot, prefix in rows[v].items()), key=lambda n: (int(n.network_address), n.prefixlen))
        feeds[v] = Feed(('\n'.join(map(str, networks))+'\n').encode(), len(networks))
    return feeds


def expected(feeds):
    return {v: set(f.data.decode().splitlines()) for v, f in feeds.items()}


def router_form(feeds):
    """Exact address strings RouterOS stores: IPv4 /32 drops the suffix, IPv6 /128 keeps it."""
    return {4: {r[:-3] if r.endswith('/32') else r for r in expected(feeds)[4]}, 6: expected(feeds)[6]}


def entries(api):
    return {v: api.call('/ip/firewall/address-list/print' if v == 4 else '/ipv6/firewall/address-list/print') for v in (4, 6)}


def raw_addresses(api):
    return {v: {r['address'] for r in rows if r['list'] == f'Iran_IPV{v}'} for v, rows in entries(api).items()}


def assert_exact(api, feeds, label):
    got = entries(api)
    for v in (4, 6):
        rows = [r for r in got[v] if r['list'] == f'Iran_IPV{v}']
        want = router_form(feeds)[v]
        addresses = [r['address'] for r in rows]
        assert len(addresses) == len(want) and set(addresses) == want, f'{label}: IPv{v} differs (router {len(addresses)}, feed {len(want)}; missing {sorted(want-set(addresses))[:5]}, extra {sorted(set(addresses)-want)[:5]})'
        assert all(r.get('comment') == 'managed:mikrotik-auto-ir-ranges' and r.get('disabled') == 'false' for r in rows), f'{label}: IPv{v} unmanaged or disabled entry'
    assert memberships(api) == expected(feeds), label


def host_counts(feeds):
    return {4: sum(r.endswith('/32') for r in expected(feeds)[4]), 6: sum(r.endswith('/128') for r in expected(feeds)[6])}


def logs(api, needle):
    return [r['message'] for r in api.call('/log/print') if needle in r.get('message', '')]


def find(api, path, **match):
    return [r for r in api.call(path+'/print') if all(r.get(k) == v for k, v in match.items())]


def files(api):
    return {r['name']: r for r in api.call('/file/print')}


def wipe(api):
    """Return the CHR to an empty router: no updater objects, files or lists."""
    for path, name in (('/system/scheduler', 'auto-ir-ranges-daily'), ('/system/script', 'auto-ir-ranges-sync'), ('/system/script', 'auto-ir-ranges-state')):
        for row in find(api, path, name=name):
            api.call(path+'/remove', **{'.id': row['.id']})
    for name in sorted((n for n in files(api) if 'auto-ir-ranges' in n and not n.endswith('.rsc')), key=len, reverse=True):
        api.call('/file/remove', **{'.id': files(api)[name]['.id']})
    for v in (4, 6):
        for row in entries(api)[v]:
            if row['list'] == f'Iran_IPV{v}':
                api.call(('/ip' if v == 4 else '/ipv6')+'/firewall/address-list/remove', **{'.id': row['.id']})


def add_entries(api, v, rows, comment='managed:mikrotik-auto-ir-ranges'):
    for row in rows:
        api.call(('/ip' if v == 4 else '/ipv6')+'/firewall/address-list/add', list=f'Iran_IPV{v}', address=row, comment=comment)


def assert_installed(api, label):
    script = find(api, '/system/script', name='auto-ir-ranges-sync')[0]
    scheduler = find(api, '/system/scheduler', name='auto-ir-ranges-daily')[0]
    assert script['comment'] == 'managed:mikrotik-auto-ir-ranges version=2.1.0', label
    assert script['policy'] == 'read,write,test', (label, script['policy'])
    assert scheduler['disabled'] == 'false' and scheduler['start-time'] == '03:00:00', (label, scheduler)
    assert scheduler['comment'] == 'managed:mikrotik-auto-ir-ranges version=2.1.0', label
    leftovers = [n for n in files(api) if n.startswith(('auto-ir-ranges', 'flash/auto-ir-ranges'))]
    assert not leftovers, (label, leftovers)
    assert not find(api, '/system/script', name='auto-ir-ranges-state'), label


def scheduled_run(api):
    """Let the real scheduler start the sync (its own policy, no interactive session) and wait for it."""
    scheduler = find(api, '/system/scheduler', name='auto-ir-ranges-daily')[0]
    runs_before = int(scheduler['run-count'])
    clock = api.call('/system/clock/print')[0]
    hh, mm, ss = map(int, clock['time'].split(':'))
    start = hh*3600+mm*60+ss+45
    assert start < 86400, 'too close to midnight on the CHR clock'
    api.call('/system/scheduler/set', **{'.id': scheduler['.id'], 'disabled': 'no', 'start-date': clock['date'], 'start-time': '%02d:%02d:%02d' % (start//3600, start//60 % 60, start % 60)})
    deadline = time.monotonic()+900
    while time.monotonic() < deadline:
        time.sleep(10)
        row = find(api, '/system/scheduler', name='auto-ir-ranges-daily')[0]
        if int(row['run-count']) > runs_before and not find(api, '/system/script/job', script='auto-ir-ranges-sync'):
            break
    else:
        raise TimeoutError('scheduler did not run the sync: '+json.dumps(logs(api, 'auto-ir-ranges')[-5:]))
    api.call('/system/scheduler/set', **{'.id': scheduler['.id'], 'start-time': '03:00:00'})
    return runs_before, row


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
    version = api.call('/system/resource/print')[0]['version']
    print('RouterOS', version, flush=True)
    if not (work/'lab-key').exists():
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'ir-ranges-disposable-lab', '-f', str(work/'lab-key')], check=True)
    if not find(api, '/user/ssh-keys', user='admin'):
        api.call('/file/add', name='lab-key.pub', type='file', contents=(work/'lab-key.pub').read_text())
        api.call('/user/ssh-keys/import', user='admin', **{'public-key-file': 'lab-key.pub'})
    # Pin this fresh local VM's host key before executing any script.
    if not (work/'known_hosts').exists():
        scanned = subprocess.check_output(['ssh-keyscan', '-p', '24222', '127.0.0.1'], stderr=subprocess.DEVNULL)
        (work/'known_hosts').write_bytes(scanned)
    # The certificate is locally generated, installed only in the disposable VM.
    api.script('/file remove [find where name="lab.crt"]; /file add name=lab.crt type=file; /file set [find where name="lab.crt"] contents='+json.dumps(cert.read_text())+'; /certificate import file-name=lab.crt passphrase=""')
    lab_url = 'https://10.0.2.2:24443/'
    def localise(text):
        return text.replace('https://raw.githubusercontent.com/parhamfa/mikrotik-auto-ir-ranges/data/', lab_url)
    installer = localise((ROOT/'routeros/install.rsc').read_text())
    results = []
    def record(label, evidence):
        results.append({'case': label, 'evidence': evidence})
        print(label, '->', evidence, flush=True)
    def sync():
        api.script('/system script run auto-ir-ranges-sync')

    # (k) The installer's version gate accepts every 7.20+ spelling RouterOS reports,
    # including a bare "7.25" (no patch number, no channel), and refuses older ones.
    gate_end = installer.index(':if ([:len [/system script job find where script="auto-ir-ranges-sync"]] > 0)')
    assert '[/system resource get version]' in installer[:gate_end]
    def gate(reported):
        return api.script(installer[:gate_end].replace('[/system resource get version]', json.dumps(reported)) + '\n:put "GATE-OK"', 'lab-version-gate')
    accepted = ('7.25', '7.20', '7.20.8 (long-term)', '7.24.6 (long-term)', '7.25 (stable)', '7.26rc1 (testing)', '7.100.1 (stable)', '8.0', '8.1.2 (stable)')
    for reported in accepted:
        assert 'GATE-OK' in gate(reported), reported
    refused = {'7.19.6 (stable)': '7.20 or newer', '7.19': '7.20 or newer', '7.2': '7.20 or newer', '6.49.18 (long-term)': '7.20 or newer', '7': 'cannot parse RouterOS version', '7.x': 'cannot parse RouterOS minor version'}
    for reported, needle in refused.items():
        try:
            gate(reported)
        except RuntimeError as exc:
            assert needle in str(exc), (reported, str(exc)[:200])
        else:
            raise AssertionError('version gate accepted '+reported)
    record('k version gate', f'accepted {len(accepted)} spellings incl. bare 7.25; refused {len(refused)}')

    # (a) Fresh install on an empty router, mixed prefixes including /32 and /128.
    wipe(api)
    assert raw_addresses(api) == {4: set(), 6: set()}
    rows_a = {4: mixed_rows(4, range(1100)), 6: mixed_rows(6, range(380))}
    feeds_a = feeds_from(rows_a)
    hosts = host_counts(feeds_a)
    assert hosts[4] >= 15 and hosts[6] >= 15, hosts
    serve_fixture(served, feeds_a, '2026-11-14T01:00:00Z')
    installed_before = len(logs(api, 'v2.1.0 installed'))
    api.script(installer, 'lab-install')
    assert_exact(api, feeds_a, 'fresh install')
    assert_installed(api, 'fresh install')
    assert len(logs(api, 'v2.1.0 installed')) == installed_before+1
    bare = sum('/' not in r for r in raw_addresses(api)[4])
    with128 = sum(r.endswith('/128') for r in raw_addresses(api)[6])
    assert bare == hosts[4] and with128 == hosts[6]
    record('a fresh install exact', f'ipv4={feeds_a[4].count} (/32 stored bare: {bare}) ipv6={feeds_a[6].count} (/128 stored with suffix: {with128}); scheduler enabled; {logs(api, "synced")[-1]}')

    # (c) A second run against the same feed changes nothing, entry IDs included.
    before = entries(api)
    unchanged_before = len(logs(api, 'auto-ir-ranges: unchanged'))
    sync()
    assert entries(api) == before
    unchanged = logs(api, 'auto-ir-ranges: unchanged')
    assert len(unchanged) == unchanged_before+1
    record('c repeat run unchanged', unchanged[-1])

    # (d) Changed feed: rows added and removed in both families, /32 and /128 on both sides.
    rows_d = {v: dict(rows) for v, rows in rows_a.items()}
    removed4 = list(range(0, 200, 3))
    removed6 = list(range(0, 70, 5))
    for s in removed4:
        del rows_d[4][s]
    for s in removed6:
        del rows_d[6][s]
    rows_d[4].update(mixed_rows(4, range(1100, 1160)))
    rows_d[6].update(mixed_rows(6, range(380, 400)))
    feeds_d = feeds_from(rows_d)
    gone, new = expected(feeds_a), expected(feeds_d)
    assert any(r.endswith('/32') for r in gone[4]-new[4]) and any(r.endswith('/32') for r in new[4]-gone[4])
    assert any(r.endswith('/128') for r in gone[6]-new[6]) and any(r.endswith('/128') for r in new[6]-gone[6])
    serve_fixture(served, feeds_d, '2026-11-14T02:00:00Z')
    sync()
    assert_exact(api, feeds_d, 'changed feed')
    line = logs(api, 'auto-ir-ranges: synced')[-1]
    adds = len(new[4]-gone[4])+len(new[6]-gone[6])
    stale = len(gone[4]-new[4])+len(gone[6]-new[6])
    assert f'added={adds} ' in line and f'stale={stale} ' in line, line
    record('d changed feed applied exactly', line)

    # (e) Half-applied list converges: manual partial state, then a real interrupted add and prune.
    rows_e = {v: dict(rows) for v, rows in rows_d.items()}
    for s in range(200, 300, 3):
        del rows_e[4][s]
    rows_e[4].update(mixed_rows(4, range(1160, 1260)))
    rows_e[6].update(mixed_rows(6, range(400, 720)))  # IPv6 grows to 700 for the shrink case
    feeds_e = feeds_from(rows_e)
    current, target = expected(feeds_d), expected(feeds_e)
    missing = {v: sorted(target[v]-current[v]) for v in (4, 6)}
    add_entries(api, 4, [r[:-3] if r.endswith('/32') else r for r in missing[4][::2]])
    add_entries(api, 6, missing[6][::2])
    add_entries(api, 4, ['192.0.2.0/24'], comment='hand-added')
    serve_fixture(served, feeds_e, '2026-11-14T03:00:00Z')
    partial = memberships(api)
    assert current[4]-target[4] and partial[4] > current[4] and partial != target
    sync()
    assert_exact(api, feeds_e, 'half-applied list')
    record('e1 manual half-applied list converges', logs(api, 'auto-ir-ranges: synced')[-1])
    sync_row = find(api, '/system/script', name='auto-ir-ranges-sync')[0]
    real = sync_row['source']
    def set_source(source):
        api.call('/system/script/set', **{'.id': sync_row['.id'], 'source': source})
    # Interrupt the real add phase after partial mutation, reboot, then run again.
    rows_e2 = {v: dict(rows) for v, rows in rows_e.items()}
    for s in range(300, 400, 2):
        del rows_e2[4][s]
    rows_e2[4].update(mixed_rows(4, range(1260, 1400)))
    feeds_e2 = feeds_from(rows_e2)
    serve_fixture(served, feeds_e2, '2026-11-14T04:00:00Z')
    set_source(real.replace(':set addedV4 ($addedV4 + 1)', ':set addedV4 ($addedV4 + 1); :if ($addedV4 = 17) do={ :error "lab interruption" }'))
    try:
        sync()
        raise AssertionError('interruption hook not reached')
    except RuntimeError as exc:
        assert 'lab interruption' in str(exc)
    set_source(real)
    partial = memberships(api)
    assert expected(feeds_e)[4] < partial[4] and partial[6] == expected(feeds_e)[6], 'old entries pruned before all additions'
    api = reboot(api)
    assert memberships(api) == partial
    sync()
    assert_exact(api, feeds_e2, 'interrupted add after reboot')
    record('e2 interrupted add survives reboot and converges', f'{len(partial[4])} IPv4 after interruption -> {feeds_e2[4].count}; {logs(api, "auto-ir-ranges: synced")[-1]}')
    # Interrupt pruning after every new row is staged.
    rows_e3 = {v: dict(rows) for v, rows in rows_e2.items()}
    for s in range(400, 500, 2):
        del rows_e3[4][s]
    feeds_e3 = feeds_from(rows_e3)
    serve_fixture(served, feeds_e3, '2026-11-14T05:00:00Z')
    sync_row = find(api, '/system/script', name='auto-ir-ranges-sync')[0]
    set_source(real.replace(':set removedV4 ($removedV4 + 1)', ':set removedV4 ($removedV4 + 1); :if ($removedV4 = 17) do={ :error "lab prune interruption" }'))
    try:
        sync()
        raise AssertionError('prune interruption not reached')
    except RuntimeError as exc:
        assert 'lab prune interruption' in str(exc)
    set_source(real)
    staged = memberships(api)
    assert expected(feeds_e3)[4] < staged[4]
    sync()
    assert_exact(api, feeds_e3, 'interrupted prune')
    record('e3 interrupted prune converges', f'{len(staged[4])} IPv4 after interruption -> {feeds_e3[4].count}; {logs(api, "auto-ir-ranges: synced")[-1]}')

    # (f) Refusals leave both lists untouched, entry IDs included.
    manifest = json.loads((served/'manifest-v2.json').read_bytes())
    original = (served/'manifest-v2.json').read_bytes()
    applied = [feeds_e3]
    def reject(label, needle, case='f'):
        before = entries(api)
        try:
            sync()
        except RuntimeError as exc:
            assert needle in str(exc), (label, str(exc)[:300])
            assert entries(api) == before, label
            assert_exact(api, applied[0], label)
            message = str(exc).strip().replace('LAB-ERROR:', '')
            record(case+' '+label, message[:120])
        else:
            raise AssertionError('invalid generation accepted: '+label)
    page = served/manifest['ipv6']['pages'][-1]['file']
    content = page.read_bytes()
    page.write_bytes(bytes([content[0] ^ 1])+content[1:])
    reject('corrupt last page refused', 'IPv6 SHA-512 mismatch')
    page.unlink()
    reject('missing last page refused', '404')
    page.write_bytes(content)
    changed = json.loads(original)
    changed['ipv6']['pages'][-1]['generation'] = '0'*64
    (served/'manifest-v2.json').write_text(json.dumps(changed))
    reject('mixed generation refused', 'mixed generation or invalid IPv6 page sequence')
    changed = json.loads(original)
    changed['ipv4']['count'] = 50001
    (served/'manifest-v2.json').write_text(json.dumps(changed))
    reject('capacity ceiling refused', 'IPv4 capacity/count limit')
    (served/'manifest-v2.json').write_bytes(original)
    rows_shrink = {4: rows_e3[4], 6: mixed_rows(6, range(320))}
    assert len(rows_shrink[6])*2 < feeds_e3[6].count
    serve_fixture(served, feeds_from(rows_shrink), '2026-11-14T06:00:00Z')
    reject(f'IPv6 shrink {feeds_e3[6].count}->{len(rows_shrink[6])} (stale 400) refused', 'IPv6 shrink guard')
    (served/'manifest-v2.json').write_bytes(original)
    for variable in ('memoryRequired', 'storageRequired'):
        set_source(real.replace(':local '+variable+' (', ':local '+variable+' (1099511627776 + '))
        reject('insufficient '+variable+' refused', 'insufficient')
    set_source(real)

    def ipv6_change(kept, new_slots):
        return {4: rows_e3[4], 6: {**{s: rows_cur[6][s] for s in sorted(rows_cur[6])[:kept]}, **mixed_rows(6, new_slots)}}
    def apply(rows, when, label):
        feeds = feeds_from(rows)
        serve_fixture(served, feeds, when)
        sync()
        assert_exact(api, feeds, label)
        applied[0] = feeds
        return feeds
    rows_cur = rows_e3

    # (h) Boundary: removing exactly as many entries as the feed holds passes; one more is refused.
    assert len(rows_cur[6]) == 706
    serve_fixture(served, feeds_from(ipv6_change(300, range(3000, 3105))), '2026-11-14T06:10:00Z')
    reject('IPv6 706 -> 405 (300 kept, stale 406) refused', 'IPv6 shrink guard; stale=406 feed=405', 'h')
    rows_cur = ipv6_change(300, range(3000, 3106))
    feeds_h = apply(rows_cur, '2026-11-14T06:20:00Z', 'boundary accepted')
    record('h IPv6 706 -> 406 (300 kept, stale 406) accepted', logs(api, 'auto-ir-ranges: synced')[-1])

    # (i) Interruption invariance: an allowed change stays allowed after any interruption.
    rows_i = ipv6_change(200, range(3200, 3320))  # 406 -> 320, stale 206
    feeds_i = feeds_from(rows_i)
    serve_fixture(served, feeds_i, '2026-11-14T06:30:00Z')
    marker = '    # Adopt one desired entry per CIDR, then prune duplicates and stale entries.'
    assert marker in real
    set_source(real.replace(marker, '    :error "lab after add pass"\n'+marker))
    try:
        sync()
        raise AssertionError('after-add interruption not reached')
    except RuntimeError as exc:
        assert 'lab after add pass' in str(exc)
    set_source(real)
    after_add = len(raw_addresses(api)[6])
    assert after_add == 406+120, after_add
    api = reboot(api)
    sync_row = find(api, '/system/script', name='auto-ir-ranges-sync')[0]
    sync()
    assert_exact(api, feeds_i, 'converge after completed add pass')
    applied[0], rows_cur = feeds_i, rows_i
    record('i1 add pass completed, reboot, rerun converges', f'IPv6 {after_add} after interruption -> {feeds_i[6].count}; {logs(api, "auto-ir-ranges: synced")[-1]}')
    rows_i2 = ipv6_change(170, range(3400, 3550))  # 320 -> 320, stale 150
    feeds_i2 = feeds_from(rows_i2)
    serve_fixture(served, feeds_i2, '2026-11-14T06:40:00Z')
    set_source(real.replace(':set removedV6 ($removedV6 + 1)', ':set removedV6 ($removedV6 + 1); :if ($removedV6 = 50) do={ :error "lab mid-prune" }'))
    try:
        sync()
        raise AssertionError('mid-prune interruption not reached')
    except RuntimeError as exc:
        assert 'lab mid-prune' in str(exc)
    set_source(real)
    mid = len(raw_addresses(api)[6])
    assert mid == 320+150-50, mid
    sync()
    assert_exact(api, feeds_i2, 'converge after mid-prune')
    applied[0], rows_cur = feeds_i2, rows_i2
    record('i2 mid-prune interruption converges', f'IPv6 {mid} after interruption -> {feeds_i2[6].count}; {logs(api, "auto-ir-ranges: synced")[-1]}')

    # (j) The earlier deadlock repro: IPv6 400 -> 300 with no overlap is refused on the first run.
    rows_cur = {4: rows_e3[4], 6: {**rows_cur[6], **mixed_rows(6, range(3600, 3680))}}
    apply(rows_cur, '2026-11-14T06:50:00Z', 'grow to 400')
    assert len(raw_addresses(api)[6]) == 400
    serve_fixture(served, feeds_from({4: rows_e3[4], 6: mixed_rows(6, range(5000, 5300))}), '2026-11-14T06:55:00Z')
    reject('IPv6 400 -> 300 disjoint refused on first run', 'IPv6 shrink guard; stale=400 feed=300', 'j')

    # (g) A sync started by the RouterOS scheduler itself, with the script's own policy.
    rows_g = {v: dict(rows) for v, rows in rows_cur.items()}
    for s in range(500, 520):
        del rows_g[4][s]
    rows_g[4].update(mixed_rows(4, range(1400, 1430)))
    feeds_g = feeds_from(rows_g)
    serve_fixture(served, feeds_g, '2026-11-14T07:00:00Z')
    synced_before = len(logs(api, 'auto-ir-ranges: synced'))
    runs_before, row = scheduled_run(api)
    assert len(logs(api, 'auto-ir-ranges: synced')) == synced_before+1
    assert_exact(api, feeds_g, 'scheduler-triggered sync')
    record('g scheduler-triggered sync', f'run-count {runs_before}->{row["run-count"]}; {logs(api, "auto-ir-ranges: synced")[-1]}')

    # (b) Upgrade from the stuck v2.0.1 state with leftovers of every older layout.
    old_installer = localise(subprocess.check_output(['git', '-C', str(ROOT), 'show', 'v2.0.1:routeros/install.rsc'], text=True))
    assert 'version=2.0.1' in old_installer and ':delay 45s' in old_installer
    wipe(api)
    rows_p = {4: mixed_rows(4, range(2000, 3100), shift=1), 6: mixed_rows(6, range(1000, 1380), shift=2)}
    feeds_p = feeds_from(rows_p)
    serve_fixture(served, feeds_p, '2026-11-14T08:00:00Z')
    # Field routers had matching lists at install time, so v2.0.1's install skipped the journal.
    add_entries(api, 4, sorted(router_form(feeds_p)[4]))
    add_entries(api, 6, sorted(router_form(feeds_p)[6]))
    api.script(old_installer, 'lab-old-install')
    assert memberships(api) == expected(feeds_p)
    assert find(api, '/system/script', name='auto-ir-ranges-sync')[0]['comment'].endswith('version=2.0.1')
    # Observe what v2.0.1 itself does with a changed feed on this RouterOS.
    rows_q = {v: dict(rows) for v, rows in rows_p.items()}
    for s in range(2000, 2100, 2):
        del rows_q[4][s]
    rows_q[4].update(mixed_rows(4, range(3100, 3150)))
    serve_fixture(served, feeds_from(rows_q), '2026-11-14T09:00:00Z')
    # Observe the nightly path: the scheduler runs v2.0.1 with its own read,write,test policy.
    old_lists = memberships(api)
    scheduled_run(api)
    log_tail = [r.get('topics', '')+' '+r.get('message', '') for r in api.call('/log/print')][-3:]
    old_outcome = 'v2.0.1 scheduled run: lists '+('changed' if memberships(api) != old_lists else 'unchanged')+', log tail '+' | '.join(log_tail)
    old_files = sorted(n for n in files(api) if 'auto-ir-ranges' in n and not n.endswith('.rsc'))
    print('observed', old_outcome, 'files', old_files, flush=True)
    # Ensure the field state: no state.json, a correct state.json.tmp, lists differing from the feed.
    current = files(api)
    if 'auto-ir-ranges/state.json' in current:
        api.call('/file/remove', **{'.id': current['auto-ir-ranges/state.json']['.id']})
    if 'auto-ir-ranges' not in files(api):
        api.call('/file/add', name='auto-ir-ranges', type='directory')
    if 'auto-ir-ranges/state.json.tmp' not in files(api):
        api.call('/file/add', name='auto-ir-ranges/state.json.tmp', type='file', contents='{"generation":"'+'a'*64+'","previous4":1100,"previous6":380,"expected4":1100,"expected6":380}')
    api.call('/file/add', name='flash', type='directory')
    api.call('/file/add', name='flash/auto-ir-ranges', type='directory')
    api.call('/file/add', name='flash/auto-ir-ranges/state.json', type='file', contents='{}')
    api.call('/system/script/add', name='auto-ir-ranges-state', source='', policy='read', comment='{"generation":"'+'b'*64+'"}')
    rows_r = {v: dict(rows) for v, rows in rows_q.items()}
    for s in range(2100, 2200, 2):
        del rows_r[4][s]
    rows_r[4].update(mixed_rows(4, range(3150, 3200)))
    rows_r[6].update(mixed_rows(6, range(1380, 1400)))
    feeds_r = feeds_from(rows_r)
    serve_fixture(served, feeds_r, '2026-11-14T10:00:00Z')
    assert memberships(api) != expected(feeds_r)
    stuck_files = sorted(n for n in files(api) if 'auto-ir-ranges' in n and not n.endswith('.rsc'))
    api.script(installer, 'lab-install')
    assert_exact(api, feeds_r, 'upgrade from stuck state')
    assert_installed(api, 'upgrade from stuck state')
    assert 'flash' in files(api)  # only our own subdirectory is removed
    api.call('/file/remove', **{'.id': files(api)['flash']['.id']})
    record('b upgrade from stuck v2.0.1', f'{old_outcome}; files after it {old_files}; leftovers before upgrade {stuck_files} -> none; {logs(api, "auto-ir-ranges: synced")[-1]}')

    (work/'results.json').write_text(json.dumps({'routeros': version, 'checks': results}, indent=2)+'\n')
    print(json.dumps({'routeros': version, 'cases': len(results)}), flush=True)
    server.shutdown()


if __name__ == '__main__':
    main()
