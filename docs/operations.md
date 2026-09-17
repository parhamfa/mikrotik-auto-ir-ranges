# Feed operations

The collection catalogue (`coverage-policy.json`, schema 2) is reviewed code.
The discovery pool on the `data` branch supplies leads; it cannot change routing.
The private DNS evaluation lab remains independent of both. No capture or key is
read by these commands.

## Publication and protocol v2

Daily generation remains at 12:17 UTC. The router refreshes at 03:00 local time.
Each successful Git commit publishes a complete generation:

- `manifest-v2.json`: current generation ID, counts, sizes and page descriptors.
- `generations/<id>/manifest.json`: immutable manifest for interrupted recovery.
- `generations/<id>/ipv4-NNNN.zone` and `ipv6-NNNN.zone`: immutable, numbered
  pages, each at most 49,152 bytes (48 KiB). Every descriptor binds its generation,
  family, number, relative path, count, bytes and SHA-512 hash.
- `coverage.json`, `provenance.json.gz`, `dns-cache.json`: public source evidence,
  explanations and last successful DNS observations of accepted service domains.
- `legacy-status.json`: explicit indication of whether a v1 updater must upgrade.

The ceiling is 50,000 entries per family. Empty or oversized output fails; it is
never truncated. Every admitted range must be fully contained in the collapsed
output. The overall generation ID binds both family contents and generation time.
Unknown schemas, missing pages and mixed generations fail validation. Serve only
committed generations: local output files are staged before the Git publication.
Immutable generation directories must remain available for interrupted routers.

The v1 `ir-ipv4.zone`, `ir-ipv6.zone` and `manifest.json` remain complete and
compatible while IPv4 fits 5,000 entries, IPv6 fits 2,000 entries, and each file
fits 60 KiB. When any limit is exceeded, **all three v1 files stay at their last
valid generation**. `legacy-status.json`, the coverage report and workflow output
announce the required updater upgrade. Old v1 software cannot display this new
notice itself; operators must monitor publication. Rollout should upgrade the
fleet before expanding beyond v1 capacity.

## Source and DNS failure policy

Core and provider failures still block publication: malformed/truncated/stale NRO
snapshots, malformed IPtoASN or IPdeny data, conflicting operator identities,
reviewed ASNs disappearing or changing identity, and provider prefix/address
volume shrinkage greater than 10%. A greater-than-50% aggregate count shrinkage
also fails, except when exact subtraction proves every removed address was a
prior service DNS observation. That exception is bound to the prior entry count
in the v2 manifest; routers with a different installed count fail visibly and
require reconciliation. Legacy updaters cannot apply that exception, so their
endpoints freeze with an upgrade notice. Expiry of a large DNS layer therefore
cannot hold the healthy core publisher hostage. Last valid generations remain
available.

DNS is handled separately for every hostname, resolver and family. Successful
public A/AAAA answers are unioned; successful empty answers replace older answers.
A failed lookup reuses only that question's last successful response, with its
original observation time and TTL. Reuse is capped at **48 hours at generation
time**, including repeated failures. Older observations are omitted. Outages,
expired observations, resolver disagreement and unresolved services appear in
`coverage.json`; healthy sources continue publishing. A changed resolver endpoint
does not inherit another endpoint's cache. Schema-1 snapshot replay retains its
old strict four-hour validation; new collection always writes schema 2.

DNS TTLs are recorded as evidence, not used to claim continuous tracking. A
48-hour cached observation is explicitly marked cached, even if its DNS TTL has
expired. Daily generation and daily router refresh also mean installed entries
can outlive the 48-hour generation cutoff until the next successful refresh.

## Discovery and investigation

The Saturday discovery workflow refreshes the persistent public candidate pool,
global ASN-description index and bounded RDAP enrichment. It pins upstream Git
commits/releases, records errors and preserves upstream license texts. Previously
seen candidates remain in the pool when an upstream disappears. This workflow
never edits the accepted catalogue or any feed files.

```sh
python scripts/discover.py refresh --output-dir build/discovery
python scripts/discover.py search 'Arvan' --directory build/discovery
python scripts/discover.py candidate 'asn:208006' --directory build/discovery
```

Copy the prior `discovery/` directory from the `data` branch before refreshing a
new worktree, to retain first-seen dates and RDAP caching. RDAP refresh is bounded
(default 250 requests); unqueried and failed enrichments remain explicit. IR
anchors are drawn from NRO and IPtoASN without manual seeds. Matching normalizes
names, handles joined brand suffixes and proposes approximate matches. Common
non-identifying words are excluded from nomination, but remain searchable.
Names and websites from registrant RDAP entities are supplementary public cues.
Matching does not establish affiliation or ownership.

v2fly suffixes, TLDs, regexes and filtered includes remain visible as
non-enumerable rules. Bootmortis domains are leads, not automatically accepted
services. Its overlap with v2fly is conservatively one corroboration group.
Chocolate4U provider inputs are discovered from its pinned build script and
provider-specific release files. Its compiled country list is deliberately not
imported because it also combines private, injected and global-cloud inputs.

Reviewed catalogue entries distinguish `operator_affiliation`, `asn_ownership`,
`provider_inventory` and `service_dns`. They require evidence URLs, review date
and reason; operator aliases aid discovery. Pending/rejected candidates cannot
select ASNs. Only the same registry/snapshot may join opaque NRO holder IDs.
A shared-host service admits exact answers, never its hosting ASN.

Candidate decisions retain their evidence fingerprints. A new release of an
unchanged assertion is not new evidence. New assertions reopen rejections.
At least half the weekly review queue is reserved for older unreviewed leads.
The [weekly investigator](weekly-investigator.md) creates PRs only; no automatic
merge, direct production publication or router changes are authorized for it.

## Explain and compare

```sh
python scripts/explain.py 185.215.232.1 --directory build/feed
python scripts/explain.py AS208006 --directory build/feed
python scripts/compare.py build/previous build/feed
python scripts/project.py --baseline-policy build/baseline-policy.json
```

Explanations join the queried address/ASN to admitted input ranges, source hashes,
registry selection, reviewed ownership, provider inventories and time-stamped
DNS observations. Provenance must match the generation. Comparison uses exact
address-set subtraction: prefix counts alone can hide substantial changes.

`known_source_completeness_percent` is checked at 100% for admitted evidence.
Unresolved services and pending discovery remain separately visible.
`measured_lab_recall` and `universe_coverage_percent` remain null until a separate
measurement has an explicit population, time window and defensible denominator.
Neither candidate counts nor source containment prove global 99.99% coverage.

## Router application and recovery

Before list mutation the updater validates every page of both families, count
bounds, byte lengths, hashes, CIDR syntax, duplicates and shrink guards. It checks
free memory and disk against a conservative budget for the transient union:
16 MiB + 2,048 bytes per existing/desired entry in memory, and 4 MiB + 384 bytes
per existing/desired entry on disk. These are conservative admission estimates,
not a platform-specific guarantee of capacity; allocation errors fail visibly.

Only then it writes a recovery journal, adds all missing entries in both families,
and prunes obsolete/duplicate entries. The journal is the comment on
`auto-ir-ranges-state`; it pins the immutable manifest and original pre-update
counts. A later run finishes that generation even if the latest pointer changed,
then the following run can advance. Count and enabled ownership checks finish the
run before clearing the journal. A failure can temporarily leave a superset;
rerunning reconciles it. One script job may run at a time. No routing/firewall
rules or other address lists are managed.

Use the RouterOS CLI, terminal import or scheduler to run the updater. In the
7.23.7 lab, running `/system/script/run` directly through the RouterOS API changes
`/tool fetch as-value` result handling; it is not a supported invocation path.
SSH execution from automation preserves the normal script context.

## Validation and canary

Run `python -m unittest discover -s tests -v`. `tests/routeros_lab.py` is an opt-in
integration harness targeting **only loopback port 24728** for a fresh disposable
CHR. Boot an official image under QEMU, map loopback 24222 -> SSH and 24728 -> API,
complete first login, and install a disposable SSH key at `build/chr/lab-key`.
The harness serves generated fixtures over local HTTPS with its own lab CA and
uses SSH execution plus API readback. It checks exact membership, repeat-run entry
IDs, bad/missing/mixed pages, capacity failure and recovery after partial adds.
It must never target a production alias.

For a real canary, save a private encrypted binary backup and a sensitive-hidden
export; save independent snapshots of lists, updater/scheduler and routing rules.
Validate the candidate in CHR first. Install through SSH, compare exact installed
membership against the manifest, rerun for idempotence, and compare all rules and
unmanaged lists. Preserve the separate Arvan list and existing evaluation lab.

## Incident response

1. Disable `auto-ir-ranges-daily`; inspect logs prefixed `auto-ir-ranges:` and the
   journal. A capacity error never permits a shortened feed.
2. Inspect `data` history, source/DNS health and the last valid immutable manifest.
3. Prefer rerunning an interrupted generation. For a deliberate rollback, save
   the journal first, restore the backed-up updater and both managed lists, then
   clear the pending journal. Do not silently point a pending journal at another
   generation, weaken checks, or import a full router export indiscriminately.
4. Verify exact membership and unchanged routing/firewall rules before resuming
   the daily schedule. Coordinate fleet upgrades before v1 capacity is exceeded.
