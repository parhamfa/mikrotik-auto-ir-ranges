# MikroTik Auto Iran Ranges

Credential-free, self-updating Iran IPv4 and IPv6 address lists for MikroTik
RouterOS 7.20 or newer.

GitHub Actions builds a guarded, ASN-augmented feed once a day. Each router
fetches that feed directly over certificate-validated HTTPS and updates its own
`Iran_IPV4` and `Iran_IPV6` lists at **03:00 router-local time**. No router API
service, central controller, or stored router password is required.

## Install v1.0.2

First confirm that the router clock and timezone are correct, then export the
configuration. RouterOS exports hide sensitive values by default:

```routeros
/system clock print
/export file=before-auto-ir-ranges
```

Then paste this single line into a RouterOS terminal:

```routeros
:if ([:pick [/system resource get version] 0 4] = "7.20") do={ /certificate settings set builtin-trust-anchors=trusted }; /tool fetch url="https://raw.githubusercontent.com/parhamfa/mikrotik-auto-ir-ranges/v1.0.2/routeros/install.rsc" check-certificate=yes dst-path=auto-ir-ranges-install.rsc; /import file-name=auto-ir-ranges-install.rsc; /file remove auto-ir-ranges-install.rsc
```

The installer performs a successful initial sync before enabling the scheduler.
It does not create or change firewall, mangle, NAT, routing, or WireGuard rules.
The 7.20 preamble enables MikroTik's built-in root CAs, which are disabled by
default on some upgraded routers. The raw immutable tag URL is intentional:
RouterOS 7.20 does not follow GitHub release-asset redirects.

On RouterOS 7.21 or newer, `check-certificate=yes` uses the built-in trust store.
If you intentionally restricted that store and the fetch reports no trusted CA,
allow the `fetch` service under `/certificate settings`; do not disable
certificate checking.

## Verify

```routeros
/ip firewall address-list print count-only where list="Iran_IPV4"
/ipv6 firewall address-list print count-only where list="Iran_IPV6"
/system scheduler print detail where name="auto-ir-ranges-daily"
/log print where message~"auto-ir-ranges"
```

Compare the two counts with the current
[`manifest.json`](https://raw.githubusercontent.com/parhamfa/mikrotik-auto-ir-ranges/data/manifest.json).
The scheduler should be enabled with `start-time=03:00:00` and `interval=1d`.

Run an immediate refresh at any time:

```routeros
/system script run auto-ir-ranges-sync
```

An unchanged run validates the remote data but performs no address-list writes.

## Ownership and migration

`Iran_IPV4` and `Iran_IPV6` are fully managed. The first run adopts existing
entries in those lists, adds missing CIDRs before removing stale ones, removes
duplicates, and applies the comment `managed:mikrotik-auto-ir-ranges`.
Manual entries placed in either managed list will be removed on the next sync.
Use a different list name for local exceptions.

Existing rules referencing these two list names continue to work unchanged.
Disable any old `adlist.py` cron/launchd job before installation so there is only
one writer. Do not copy `mikrotik_config.json` into this repository; after all
routers have migrated, remove that credential file and rotate the stored router
passwords.

## Safety model

Before changing either list, the router validates all three downloads:

- TLS certificates, manifest schema, filenames, byte sizes, and SHA-512 hashes.
- Exact CIDR counts, address families, prefix lengths, and duplicate rows.
- Bounds of 1,000–5,000 IPv4 and 300–2,000 IPv6 CIDRs.
- A 60 KiB maximum per feed and a greater-than-50% shrink rejection.

The publisher independently applies the same count, size, syntax, and shrink
guards. Failed generation leaves the `data` branch untouched. Address-list
updates are add-first, so a mid-run failure cannot create a coverage gap.

## Upgrade, stop, and uninstall

Router code never updates itself. To upgrade, review the release and run the
new release's version-pinned install command.

Temporarily stop updates without changing the lists:

```routeros
/system scheduler disable [find where name="auto-ir-ranges-daily"]
```

Uninstall v1.0.2 while retaining the last valid lists and every rule that uses
them:

```routeros
/tool fetch url="https://raw.githubusercontent.com/parhamfa/mikrotik-auto-ir-ranges/v1.0.2/routeros/uninstall.rsc" check-certificate=yes dst-path=auto-ir-ranges-uninstall.rsc; /import file-name=auto-ir-ranges-uninstall.rsc; /file remove auto-ir-ranges-uninstall.rsc
```

For rollback to the pre-migration list contents, first uninstall, then restore
the two address lists from `before-auto-ir-ranges.rsc` or rerun the retired
helper deliberately. Do not blindly import the entire export into a live router.

## What the feed covers

The generated set is the collapsed union of:

- IPdeny Iran country IPv4 and IPv6 ranges.
- IPtoASN ranges whose origin ASN is classified as Iranian.

This retains the old helper's important ASN augmentation, including some
Iranian-operated, foreign-registered address space. It does not identify an
Iranian service hosted entirely behind a non-Iranian ASN or CDN.

The legacy helper discovered Iranian ASNs through RIPEstat. This public
publisher instead uses IPtoASN's own ASN-country field: it produced exact CIDR
parity during migration, avoids an extra dependency, and avoids republishing
RIPEstat-derived data contrary to RIPEstat's service terms.

See [data sources and licensing](docs/data-sources.md) and
[feed operations](docs/operations.md).

## Development

```bash
python3 -m unittest discover -s tests -v
python3 scripts/generate.py --output-dir build/data --previous-dir build/data
```

The Python generator has no third-party runtime dependencies. Code is MIT
licensed; upstream data remains subject to its own terms.
