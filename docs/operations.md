# Feed operations

## Published branch

The generated `data` branch contains only:

- `ir-ipv4.zone`
- `ir-ipv6.zone`
- `manifest.json`

Manifest schema 1 records generation time, counts, byte lengths, SHA-512
digests, source URLs and hashes, and input statistics. Consumers should reject
unknown schemas.

## Publisher behavior

The scheduled workflow runs at 12:17 UTC and can also be dispatched manually.
It retries downloads, generates into the checked-out `data` branch, validates
the output against the previous branch contents, and commits only when either
CIDR file changed. A source, syntax, size, count, or shrink failure exits before
any push, leaving the last valid commit available to routers.

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
