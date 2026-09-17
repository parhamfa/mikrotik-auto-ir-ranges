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
/tool fetch url="https://raw.githubusercontent.com/parhamfa/mikrotik-auto-ir-ranges/v2.0.1/routeros/uninstall.rsc" check-certificate=yes dst-path=auto-ir-ranges-uninstall.rsc; /import file-name=auto-ir-ranges-uninstall.rsc; /file remove auto-ir-ranges-uninstall.rsc
```

The updater owns all entries in `Iran_IPV4` and `Iran_IPV6`; manual additions
there are removed on refresh. Run it through the terminal, SSH or scheduler.

## Feed and recovery

GitHub Actions publishes daily at 12:17 UTC. Routers refresh at 03:00 local time.
The [data branch](https://github.com/parhamfa/mikrotik-auto-ir-ranges/tree/data)
contains the feed and reports:

| File | Purpose |
| --- | --- |
| `manifest-v2.json` | Current generation, page paths, counts, sizes and SHA-512 hashes |
| `generations/` | Immutable manifests and numbered IPv4/IPv6 pages |
| `coverage.json` | Source completeness, freshness and collection gaps |
| `provenance.json.gz` | Evidence used by the IP/ASN explanation command |
| `dns-cache.json` | Timestamped public DNS observations for catalogue services |
| `discovery/` | Candidate pool, review queue and source provenance |

Pages are at most 48 KiB, with a ceiling of 50,000 entries per family. Routers
check the complete generation and available memory/storage before changing
lists. A failed check retains the installed feed; output is never truncated.
If application is interrupted, rerun the sync. Its recovery journal finishes
the same generation before advancing to a newer one. Recovery is stored in
`auto-ir-ranges/state.json` (under `flash/` where required), and removed on success.
Changed lists wait 45 seconds after saving state for RouterOS disk write-back;
unchanged runs skip the write and wait.

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

## Catalogue maintenance

Saturday's workflow refreshes discovery. The weekly investigator reviews public
evidence and proposes PRs; the accepted catalogue continues refreshing daily.
See the [investigator instructions](weekly-investigator.md).

Reuse `discovery/` from the data branch in `build/discovery/` to preserve the
candidate history and RDAP cache before refreshing:

```sh
python3 scripts/discover.py refresh --output-dir build/discovery
python3 scripts/discover.py search 'organization name'
```

To preview a catalogue change against the same source observations:

```sh
mkdir -p build
git show origin/main:coverage-policy.json > build/baseline-policy.json
python3 scripts/project.py --baseline-policy build/baseline-policy.json
python3 scripts/compare.py build/projection/baseline/feed build/projection/candidate/feed
```

Review address additions/removals, evidence and collection gaps before merging.
Source-completeness checks confirm that accepted inputs survived generation;
they do not measure undiscovered networks or services.
