# MikroTik Auto Iran Ranges

Automatically updates `Iran_IPV4` and `Iran_IPV6` address lists on MikroTik.
GitHub builds the feed daily; each router downloads it directly over HTTPS.

## Install

Requires **RouterOS 7.20+** and access to GitHub. Paste this into the router terminal:

```routeros
:if ([:pick [/system resource get version] 0 4] = "7.20") do={ /certificate settings set builtin-trust-anchors=trusted }; /tool fetch url="https://raw.githubusercontent.com/parhamfa/mikrotik-auto-ir-ranges/v2.0.1/routeros/install.rsc" check-certificate=yes dst-path=auto-ir-ranges-install.rsc; /import file-name=auto-ir-ranges-install.rsc; /file remove auto-ir-ranges-install.rsc
```

This syncs both lists and schedules updates for **03:00 router-local time**.
Use the list names in your own routing and firewall rules; the installer only
manages the lists and updater. Keep manual exceptions in a separate list.

## How it works

1. **Collect:** IPdeny supplies Iran country ranges. NRO supplies registered
   allocations and ASNs. IPtoASN adds Iran-labelled ranges and ranges associated
   with selected ASNs.
2. **Extend:** a reviewed [catalogue](coverage-policy.json) adds operator
   affiliations, provider-published ranges and public DNS addresses for listed
   services. A service on shared hosting adds its exact addresses, not the
   hosting provider's entire network.
3. **Publish:** combine and deduplicate the ranges, validate the result, and
   publish a versioned feed on the [`data` branch](https://github.com/parhamfa/mikrotik-auto-ir-ranges/tree/data).
4. **Sync:** routers validate every page, add missing entries, then remove stale
   entries. Interrupted updates resume on the next run.

Discovery searches global ASN descriptions, registry information and community
lists for more operators and services. Candidates stay separate from the feed;
a weekly investigator proposes catalogue changes through reviewed pull requests.
Accepted sources then refresh automatically.

Coverage depends on the sources and catalogue. Service DNS is a daily snapshot;
shared addresses can also serve unrelated sites.

## Management

Refresh now:

```routeros
/system script run auto-ir-ranges-sync
```

Check the updater log:

```routeros
/log print where message~"auto-ir-ranges"
```

The feed updates automatically. To update the router script, rerun the install
command from the current README.

## Thanks to

Discovery builds on these community projects:

- [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community) — Iran-related domain rules.
- [bootmortis/iran-hosted-domains](https://github.com/bootmortis/iran-hosted-domains) — service domain lists.
- [Chocolate4U/Iran-v2ray-rules](https://github.com/Chocolate4U/Iran-v2ray-rules) — provider inventory sources and ranges.

[Sources and selection](docs/data-sources.md) ·
[Commands and maintenance](docs/operations.md)
