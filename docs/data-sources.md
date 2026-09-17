# Data sources and licensing

The repository's MIT license applies to the generator, workflows, tests, and
RouterOS scripts. It does not relicense upstream data.

## IPdeny

- IPv4: <https://www.ipdeny.com/ipblocks/data/countries/ir.zone>
- IPv6: <https://www.ipdeny.com/ipv6/ipaddresses/blocks/ir.zone>
- Terms: <https://www.ipdeny.com/tos.php>
- Copyright and redistribution notice: <https://www.ipdeny.com/copyright.php>
- Usage limits: <https://www.ipdeny.com/usagelimits.php>

IPdeny currently permits redistribution of its country zone files, subject to
its terms, copyright notice, and fair-usage limits. This project downloads each
Iran zone once per daily publisher run rather than once per router.

## IPtoASN

- Database and downloads: <https://iptoasn.com/>
- IPv4: <https://iptoasn.com/data/ip2asn-v4.tsv.gz>
- IPv6: <https://iptoasn.com/data/ip2asn-v6.tsv.gz>
- Data license: Public Domain Dedication and License 1.0 (PDDL-1.0), as stated
  on the database site.

The generator selects rows whose country field is `IR` or whose originating ASN
is independently selected through the registry/ownership layer. It converts
each address range to CIDRs and unions those networks with the other layers.

## NRO extended delegated statistics

- Publication and explanation: <https://www.nro.net/about/rirs/statistics/>
- Daily file: <https://ftp.ripe.net/pub/stats/ripencc/nro-stats/latest/nro-delegated-stats>
- Format: <https://www.nro.net/wp-content/uploads/nro-extended-stats-readme5.txt>

The NRO publishes the five RIRs' allocation, assignment, and ASN statistics for
analysis of Internet number resources. Country identifies the registered
holder, not necessarily the location or nationality of every service.
Allocated/assigned `IR` resources seed the selection. Reviewed foreign operator
ASNs also seed it. Same-holder resources are joined only within one registry
and snapshot: opaque holder IDs are not stable between snapshots.

This is the public statistical dataset, not RIPEstat API output or a bulk copy
of RIPE Database contact records. Do not republish registrant contact data.

## Provider inventories

- ArvanCloud: <https://www.arvancloud.ir/fa/ips.txt>, linked from
  <https://www.arvancloud.ir/fa/dev/ips>.
- MizbanCloud: <https://mizbancloud.com/ips.txt>, linked from
  <https://docs.mizbancloud.com/cdn/whitelist>.

These are provider-published network configuration inputs. The source provider
and exact downloaded content hash remain in the manifest. Provider data is not
relicensed under this repository's MIT code license. ASN affiliation evidence
and expected operator names live in `coverage-policy.json`; the feed uses
IPtoASN and NRO to select resources, not RIPEstat-derived prefix republishing.

## Identified service DNS

The reviewed service catalogue contains public domain names and their evidence
URLs. Google and Cloudflare DNS-over-HTTPS provide A/AAAA observations. Each
observation records the domain, resolver, address, CNAME target where relevant,
minimum relevant TTL, and observation timestamp. Only exact public host
addresses enter the union. This includes shared hosting under the chosen broad
coverage policy. No general cloud/CDN ASN is selected merely because one of its
addresses serves a catalogue entry.

- Google API: <https://developers.google.com/speed/public-dns/docs/doh/json>
- Cloudflare API: <https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/>

Two resolver snapshots do not exhaust geographic DNS views, application
subdomains, or future address changes. Daily snapshots are not a TTL-aware
substitute for observing the DNS answers that clients actually use.

## Why RIPEstat is not a publisher input

The retired local helper used RIPEstat to enumerate Iranian ASNs. RIPEstat's
current service terms prohibit recompiling or redistributing RIPEstat data
without written permission. The public publisher therefore does not consume
RIPEstat. At migration time, selecting `IR` from IPtoASN's public-domain data
produced exactly the same final 1,808 IPv4 and 578 IPv6 CIDRs as the legacy
RIPEstat-ASN method. That measurement is historical, not a permanent guarantee.

Upstream classifications can be incomplete or wrong. Treat these lists as a
routing/firewall input with a rollback path, not as proof of legal jurisdiction
or user location.

## Discovery-only sources

- [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community),
  MIT: recursively read the pinned `category-ir` tree. Preserve include filters,
  suffixes, exact hosts, keywords, regexes, attributes and unresolved rules.
- [bootmortis/iran-hosted-domains](https://github.com/bootmortis/iran-hosted-domains),
  MIT: use a pinned release's domain asset and record its upstream source chain.
  Its aggregation includes v2fly, so it does not independently corroborate that
  list. Per-entry origins are not supplied; the grouping is conservative.
- [Chocolate4U/Iran-v2ray-rules](https://github.com/Chocolate4U/Iran-v2ray-rules),
  GPL-3.0: discover official inventory URLs from the pinned domestic-CDN build
  script; read only matching provider files from a pinned release commit.
  Preserve original-source URLs and the upstream license. Do not ingest its
  compiled country list, separately licensed geolocation databases, or injected
  and private address inputs as Iranian ownership evidence.
- [IANA RDAP ASN bootstrap](https://data.iana.org/rdap/asn.json): locate the
  authoritative registry for bounded ASN organization lookups. Save only public
  network/registrant organization names, website links, source URLs, hashes and
  dates; omit email, phone, address and abuse/technical-contact records.

Upstream license texts are retained under `discovery/licenses/` on the data
branch. Discovery-derived assertions retain upstream licensing; this project's
MIT license does not override it. Candidates do not enter production feeds until
reviewed public evidence supports the precise catalogue relationship. Shared
lineage and repeated releases of the same assertion never multiply corroboration.
