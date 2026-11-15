# Commands and maintenance

## Router commands

```routeros
# Refresh
/system script run auto-ir-ranges-sync

# Status
/log print where message~"auto-ir-ranges"
/ip firewall address-list print count-only where list="Iran_IPV4"
/ipv6 firewall address-list print count-only where list="Iran_IPV6"

# Pause or resume automatic updates
/system scheduler disable [find where name="auto-ir-ranges-daily"]
/system scheduler enable [find where name="auto-ir-ranges-daily"]
```

Remove the updater while keeping the lists and their referencing rules:

```routeros
/tool fetch url="https://raw.githubusercontent.com/parhamfa/mikrotik-auto-ir-ranges/v2.1.0/routeros/uninstall.rsc" check-certificate=yes dst-path=auto-ir-ranges-uninstall.rsc; /import file-name=auto-ir-ranges-uninstall.rsc; /file remove auto-ir-ranges-uninstall.rsc
```

The updater owns all entries in `Iran_IPV4` and `Iran_IPV6`; manual additions
there are removed on refresh. Run it through the terminal, SSH or scheduler.

## Feed and recovery

GitHub Actions publishes daily at 12:17 UTC. Routers refresh at 03:00 local time.
When a source other than the registry fails, the run still publishes, then turns
red if that source was dropped or has been reused for more than 3 days; the
failure names each source and how long it has been stale.
The [data branch](https://github.com/parhamfa/mikrotik-auto-ir-ranges/tree/data)
contains the feed and reports:

| File | Purpose |
| --- | --- |
| `manifest-v2.json` | Current generation, page paths, counts, sizes and SHA-512 hashes |
| `generations/` | Immutable manifests and numbered IPv4/IPv6 pages |
| `coverage.json` | Per-source freshness, layer sizes and collection gaps; not a measure of accuracy |
| `provenance.json.gz` | Evidence used by the IP/ASN explanation command |

Pages are at most 48 KiB, with a ceiling of 50,000 entries per family. Routers
check the complete generation and available memory/storage before changing
lists. A failed check retains the installed feed; output is never truncated.
A router refuses an update that would remove more entries than the feed
contains, and the publisher refuses to publish one.
If application is interrupted, rerun the sync. Each run adds whatever is missing
and removes whatever is stale, so the next run finishes the job from the lists
themselves; the updater keeps no state files.

Old v1 endpoints stay complete while they fit their limits. Otherwise they keep
the last compatible generation, and `legacy-status.json` reports that the router
updater needs upgrading.

## Local development

Requires Python 3.11+; no third-party Python dependencies.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/generate.py --output-dir build/feed --previous-dir build/feed
python3 scripts/explain.py 185.215.232.1 --directory build/feed
```

Local generation does not publish. For RouterOS integration, boot a fresh
disposable CHR with loopback ports 24222 → SSH and 24728 → API, then run
`tests/routeros_lab.py` followed by `tests/routeros_capacity_lab.py`.

## Provider research

The provider layer grows through weekly research, run as an agent routine. The
rules and the routine's instructions are in [provider research](provider-research.md).

`provider-ledger.json` lists every Iranian hosting company the research knows
about, whether or not it publishes a list of its own ranges. `coverage-policy.json`
holds only the entries the daily build reads.

```sh
python3 scripts/ledger.py stats
python3 scripts/ledger.py next 10
python3 scripts/ledger.py add --brand Example --domain example.ir
python3 scripts/ledger.py leads --iptoasn ip2asn-v4.tsv.gz --only sotoon
python3 scripts/check_providers.py --offline
python3 scripts/check_providers.py --only chabokan
```

`add` records a company found by market research; it needs no network of its
own. `leads` prints networks registered under another country whose name
matches a company. A lead is only a name match: the research guide says what
turns one into evidence.

`check_providers.py` fetches a provider's list the way the build does and
refuses it when it does not parse, when no row sits inside known Iranian space,
or when it would add more than 4,096 addresses outside that space. The same
limit applies in the daily build. A repeated or private row is dropped and
reported in `coverage.json`; it no longer refuses the whole file.

To preview a catalogue change against the same source observations:

```sh
mkdir -p build
git show origin/main:coverage-policy.json > build/baseline-policy.json
python3 scripts/project.py --baseline-policy build/baseline-policy.json
python3 scripts/compare.py build/projection/baseline/feed build/projection/candidate/feed
```

Review address additions/removals, evidence and collection gaps before merging.
The generator checks that every accepted input survived generation; that does
not measure undiscovered networks. The list's accuracy against real traffic has
not been measured yet.
