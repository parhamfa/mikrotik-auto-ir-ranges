# Iran range coverage audit — 16 September 2026

## Finding

The missing Arvan ranges were a selection-model failure. The publisher equated
Iran-related networks with the union of IPdeny's Iran country list and IPtoASN
rows labelled `IR`. It had no independent concept of Iranian operator
affiliation, published provider inventory, or services on foreign/shared hosts.

The router faithfully contained the published feed: 1,810 IPv4 and 581 IPv6
prefixes. Checksums, aggregation, publishing, and synchronization did not drop
the Arvan ranges. They were never selected.

Live upstream evidence:

| Missing official range | IPtoASN origin | Country label | Result of old predicate |
| --- | --- | --- | --- |
| `185.215.232.0/22` | AS208006 `ARVANCLOUD-CDN` | AE | Rejected |
| `193.24.119.0/29` (inside `/24`) | AS57568 `TR_ARVANCLOUD` | AE | Rejected |
| `152.233.66.88/29` | AS60068 `CDN77` | GB | Rejected |

The first two are in [Arvan's official inventory](https://www.arvancloud.ir/fa/ips.txt).
The third is in [MizbanCloud's inventory](https://mizbancloud.com/ips.txt), linked
from its [firewall documentation](https://docs.mizbancloud.com/cdn/whitelist).
The raw route mappings came from the downloaded
[IPtoASN dataset](https://iptoasn.com/).
RIPE identifies both [AS208006](https://rdap.db.ripe.net/autnum/208006) and
[AS57568](https://rdap.db.ripe.net/autnum/57568) with ArvanCloud Global
Technologies. This is a foreign-registration blind spot, not evidence that the
published Arvan ranges stopped belonging to its service footprint.

RIPE explicitly distinguishes a holder's legal registration country from
where its networks operate in its
[country-code explanation](https://labs.ripe.net/author/wilhelm/impact-of-nwi-10-on-country-codes-in-delegated-statistics/).
Two sources using country registration cannot independently establish business
or service affiliation.

## Where the chain fell short

1. **Source discovery:** no official provider inventories or service-domain
   catalogue were consumed.
2. **Selection:** one country predicate stood in for operator and service
   identity. Foreign affiliates and third-party hosting were excluded by design.
3. **Validation:** syntax, hashes, size bounds, migration parity, and shrink
   guards demonstrated consistency. None measured completeness against an
   independently maintained set of expected providers/services. Shrink guards
   cannot detect a range that was never present.
4. **Distribution:** the feed and router agreed; this part worked.

The audit found no uncovered Iran-registered RIPE IPv4/IPv6 allocation, or
observed route from those registered ASNs, in the checked snapshots. This is
bounded evidence about the domestic registry layer, not proof that unknown
foreign affiliates and hosted services were covered.

## Implemented candidate

The v1.1.0 publisher candidate separates these evidence layers:

- Country lists, retained as before.
- NRO registry allocations/ASNs across the five RIRs. Select `IR` holders and
  reviewed operator anchors, then join resources by holder identity within the
  same registry and snapshot. Do not treat changing opaque IDs as persistent.
- Origin routes for independently identified ASNs, regardless of country label.
- Official provider inventories with count, family, prefix-size, and shrink
  validation. Initially ArvanCloud and MizbanCloud.
- Exact public A/AAAA endpoints for 20 identified service domains, from Google
  and Cloudflare DNS. Shared hosting is included; entire unrelated hosting ASNs
  are not inferred from one customer address.
- Source hashes, source-specific coverage and additions, DNS timestamps/TTLs,
  and explicit scope limitations in `coverage.json`.

The two foreign Arvan ASNs alone expose nine IPv4 networks covering 5,888
addresses beyond the old feed, not merely the original two inventory entries.
Registry holder links also recover their allocated IPv6 space. MizbanCloud
adds another eight IPv4 addresses. The DNS sample identifies two Cloudflare
IPv6 host addresses used by `parspack.com` outside the old list.

| Check | Previously published feed | Candidate |
| --- | --- | --- |
| Arvan official ranges | 12/14 | 14/14 |
| Arvan official IPv4 addresses | 1,268/2,300 | 2,300/2,300 |
| MizbanCloud official ranges | 7/8 | 8/8 |
| Listed service domains observed | No service layer | 20, with 34 unique IPs in the audited snapshot |
| Total IPv4 prefixes | 1,810 | 1,818 |
| Total IPv6 prefixes | 581 | 585 |

Address-set differences matter more than the total prefix count. The candidate
adds 5,896 IPv4 addresses. Fresh upstream data also removes `201.7.23.0/24`:
the current IPtoASN row is ASN 0, `None`, `Not routed`, and the current country
source does not contain it. This is input drift, not a consequence of the new
selection layers. No claim of an ownership transfer is made from that observation.

The new IPv6 space includes three Arvan allocations (`2a0a:77c0::/29`,
`2a0d:4ac0::/29`, and `2a0f:94c0::/29`, partly overlapping previously selected
routes), plus the two exact shared-host addresses. Existing schema-1 RouterOS
readers accept the feed sizes/counts without an installer change.

## What the 99.99% target still requires

There is no enumerated universe of “anything related to Iran”, so no defensible
global percentage can be calculated from this audit. Counting the sources that
were just unioned into an output would be circular evidence for that claim.
The report deliberately leaves `universe_coverage_percent` null.

An operational SLO needs a maintained reference set and a unit: provider
networks, addresses, domains, successful requests, or observed connections.
Track at least these separately:

1. Complete containment of accepted registry/operator/provider evidence.
2. Coverage of service endpoints from independently maintained domain and
   application-dependency inventories, including different DNS viewpoints.
3. Actual client-path misses, their traffic impact, and time to classification.
4. Source freshness, catalogue review age, false inclusions, and list-size budget.

The current DNS layer is a daily snapshot and is not adequate to promise
continuous coverage of DNS addresses with short TTLs. For that, the next
architectural step is observing the DNS answers clients actually use, updating
an explicitly owned dynamic list with TTL-aware expiry, and making the static
updater preserve those entries. That requires a reviewed router/DNS policy
change; simply polling a global feed more frequently does not solve geographic
DNS or unlisted application dependencies.

The provider/operator repair and 29-test suite close the confirmed failure
class. Expanding the catalogue and measuring misses remain ongoing work;
neither two providers nor 20 domains establish global 99.99% recall.

## Release boundary

The figures above describe the tested candidate at audit time. Production
publication is recorded in the publisher workflow and `data` branch history;
installation requires a separate live router readback.
Publishing the common data feed changes every subscriber's referenced firewall
and routing policy. In particular, including shared hosting addresses can
affect unrelated services on those same addresses. Review the exact candidate
delta and retain the previous data commit before publishing and validating a
pilot router.
