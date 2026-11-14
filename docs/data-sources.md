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

Country registration and operator affiliation are different signals. The
[catalogue](../coverage-policy.json) records accepted organizations, ASNs and
provider URLs, with evidence, aliases and review dates. A provider inventory
does not establish ownership of its hosting ASN. Website hostnames are not
tracked; the generator rejects a catalogue that lists them.

A missing or malformed NRO registry stops publication. If IPdeny, IPtoASN or a
provider inventory fails to download or fails its checks, the feed still
publishes with that source's ranges from the last good build, for up to 30 days;
after that the source is left out. A reviewed operator ASN whose IPtoASN name
no longer matches the catalogue loses its non-`IR` ranges for that build. Both
are recorded in `coverage.json` (`source_status`, `warnings`). Routers receive
changes at their next successful daily sync.

## Discovery

Discovery is paused since 2026-11-14, until a measurement of real traffic shows
what the list is missing. Its workflow runs only when started by hand, and
nothing in the daily build depends on it. When it runs:

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
confirmations. Domain leads can no longer enter the catalogue, because website
hostnames are not tracked.

## Attribution

Upstream data keeps its own terms; the repository's MIT license covers the code.
IPdeny publishes [terms](https://www.ipdeny.com/tos.php) and
[redistribution notices](https://www.ipdeny.com/copyright.php). IPtoASN publishes
its database under PDDL-1.0. Provider sources and evidence URLs are recorded in
the catalogue. Discovery preserves upstream versions, source links and license
texts under `discovery/licenses/` on the data branch.
