# Feed operations

## Published branch

The generated `data` branch contains:

- `ir-ipv4.zone`
- `ir-ipv6.zone`
- `manifest.json`
- `coverage.json`

Manifest schema 1 records generation time, counts, byte lengths, SHA-512
digests, source URLs and hashes, and input statistics. Consumers should reject
unknown schemas.

## Publisher behavior

The scheduled workflow runs at 12:17 UTC and can also be dispatched manually.
It retries downloads, generates into the checked-out `data` branch, validates
the output against the previous branch contents, and commits feed changes and
the latest coverage evidence. The manifest is preserved when both CIDR files
and their coverage policy/generator version are unchanged. A source, syntax,
size, count, or shrink failure exits before any push, leaving the last valid
commit available to routers.

The NRO snapshot must be no more than three days old and its complete record
count must match its header. DNS observations must be no more than four hours
old at generation time; both configured resolvers must answer every configured
A/AAAA query successfully, and every service must have at least one public IP.
Provider source counts and address volumes may not fall by more than 10% from
the previous coverage report without review. A reviewed operator ASN must still
appear in the routing dataset under its expected name.

These checks protect source integrity and known-source coverage. They do not
make the daily feed respect DNS TTLs or discover all subdomains. Review the
coverage report's limitations before using its endpoint layer as routing policy.

## Extending and measuring coverage

Edit `coverage-policy.json` to add evidence-backed operator ASNs, official
provider feeds, and identified service domains. Do not infer ownership from
substring matches in ASN names or add an entire shared hosting ASN from one
customer domain. The service layer admits only observed host addresses.

Each build records exact additions beyond the legacy IPdeny + `IR`-labelled
IPtoASN union. Compare the candidate with the published files by address-set
difference, not just prefix counts: aggregation can reduce the count while
increasing coverage. Source health, coverage, and discovery completeness are
separate measurements.

Saved inputs can be replayed using `--source-dir PATH --generated-at TIMESTAMP`.
The directory must contain payloads named after source keys, including the
timestamped `dns_snapshot`. Use the original observation time when replaying;
do not present a replay as a fresh network observation.

## Router behavior

The router fetches the manifest and both feeds into memory, then validates both
families before the first address-list mutation. It stages missing CIDRs,
adopts matching legacy entries, removes duplicates and stale entries, and
verifies exact final counts. Subsequent identical runs do not rewrite entries.

RouterOS 7.20 may require
`/certificate settings set builtin-trust-anchors=trusted` before its first
certificate-validated fetch. Version-pinned setup uses the raw Git tag because
7.20 does not follow GitHub release-asset redirects.

Relevant log prefix: `auto-ir-ranges:`.

## Incident response

1. Disable `auto-ir-ranges-daily` on affected routers.
2. Inspect the `data` branch history and the publisher workflow logs.
3. Compare list counts and SHA-512 values with the last known-good manifest.
4. Revert the bad `data` commit or repair the generator through a reviewed
   change; do not weaken the router checks to force an update.
5. Run `auto-ir-ranges-sync` manually on one pilot router before re-enabling
   the fleet.
