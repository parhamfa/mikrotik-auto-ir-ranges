# MikroTik Auto Iran Ranges

Credential-free, self-updating Iran IPv4 and IPv6 address lists for MikroTik
RouterOS 7.20 or newer.

GitHub Actions builds a guarded, evidence-based feed once a day. Each router
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
- NRO allocations and assignments registered to Iranian holders, plus resources
  held by the same registered operators, including reviewed foreign affiliates.
- IPtoASN ranges labelled `IR`, or originated by ASNs identified independently
  through those registry records and the reviewed operator catalogue.
- Official provider inventories, currently ArvanCloud and MizbanCloud.
- Exact A/AAAA addresses of the service domains in
  [`coverage-policy.json`](coverage-policy.json), observed through Google and
  Cloudflare DNS. This includes shared hosting addresses, never the entire
  hosting ASN merely because it hosts one Iranian website.

Country registration, operator identity, and service identity are different
signals. For example, ArvanCloud's AS208006 and AS57568 are labelled `AE`, so
selecting only `IR` rows missed their networks even though Arvan published them.

**There is no measured 99.99% guarantee for all Iran-related IPs.** The catalogue
is not exhaustive, and DNS snapshots vary by resolver, geography, and time.
The daily feed/router schedule does not follow DNS TTLs. Shared IPs can also
serve unrelated foreign sites. These lists are a broad routing policy, not a
geolocation or ownership certificate.

Every successful build produces `coverage.json`: source hashes, registry
freshness, per-layer coverage, additions beyond the legacy algorithm, and DNS
observations with TTLs. Its known-source checks require complete containment,
including coverage by multiple smaller prefixes. They prove that admitted
evidence survived generation; they do not measure undiscovered networks.

Provider feed failures, malformed/truncated registry data, stale snapshots,
reviewed ASN identity changes, and provider shrinkage exceeding 10% abort
publication. Previously published feeds remain available. Existing v1.0.2
router installations can consume the expanded schema-1 feeds without reinstalling.

See [data sources and licensing](docs/data-sources.md) and
[feed operations](docs/operations.md), plus the
[2026-09-16 coverage audit](docs/coverage-audit-2026-09-16.md).

## Development

```bash
python3 -m unittest discover -s tests -v
python3 scripts/generate.py --output-dir build/data --previous-dir build/data
```

The Python generator has no third-party runtime dependencies. Code is MIT
licensed; upstream data remains subject to its own terms.
