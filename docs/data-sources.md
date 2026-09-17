# Sources and selection

The feed combines country data, registry records and a reviewed catalogue.
A range can qualify through any of these sources; they do not all have to agree.

## Automatic collection

| Source | What enters the feed |
| --- | --- |
| [IPdeny](https://www.ipdeny.com/) | Iran IPv4 and IPv6 country ranges. |
| [NRO](https://www.nro.net/about/rirs/statistics/) | Allocated or assigned `IR` resources, plus resources linked to selected ASNs through the same registered holder. Holder IDs are matched only within one registry and snapshot. |
| [IPtoASN](https://iptoasn.com/) | Ranges labelled `IR` or associated with ASNs selected through NRO and the catalogue. |
| Provider inventories | Published ranges from the URLs in the catalogue. |
| Public DNS | Exact public A/AAAA addresses for catalogue hostnames, queried through Google and Cloudflare. |

Country registration, operator affiliation and service hosting are different
signals. The [catalogue](../coverage-policy.json) records accepted organizations,
ASNs, provider URLs and service hostnames, with evidence, aliases and review dates.
Hosting a service does not establish ownership of its hosting ASN.

Malformed core data, conflicting operator identities and suspicious provider
changes stop publication. DNS failures are handled per hostname, resolver and
family: successful answers continue, while previous answers can be reused for
up to 48 hours at generation time. Original timestamps and TTLs are retained;
expired answers are omitted and reported. Routers receive changes at their next
successful daily sync.

## Discovery

NRO and IPtoASN identify Iranian starting points. Name matching across the global
ASN index, enriched with [RDAP organization records](https://data.iana.org/rdap/asn.json),
finds possible affiliates in other countries. Community sources add more leads:

| Source | Use | Upstream license |
| --- | --- | --- |
| [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community) | Iran-related domain rules and includes | MIT |
| [bootmortis/iran-hosted-domains](https://github.com/bootmortis/iran-hosted-domains) | Service domain candidates | MIT |
| [Chocolate4U/Iran-v2ray-rules](https://github.com/Chocolate4U/Iran-v2ray-rules) | Provider inventory URLs and provider-specific ranges | GPL-3.0 |

Name matches and community entries are candidates, not automatic additions.
Review decisions persist; new evidence can reopen a rejection. Wildcards,
suffixes and patterns remain visible as rules rather than being treated as
individual hostnames. Copied lists share provenance and do not count as separate
confirmations.

## Attribution

Upstream data keeps its own terms; the repository's MIT license covers the code.
IPdeny publishes [terms](https://www.ipdeny.com/tos.php) and
[redistribution notices](https://www.ipdeny.com/copyright.php). IPtoASN publishes
its database under PDDL-1.0. Provider sources and evidence URLs are recorded in
the catalogue. Discovery preserves upstream versions, source links and license
texts under `discovery/licenses/` on the data branch.
