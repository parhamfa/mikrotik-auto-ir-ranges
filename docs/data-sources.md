# Sources and selection

The feed combines country data, registry records and a reviewed catalogue.
A range can qualify through any of these sources; they do not all have to agree.

## Automatic collection

| Source | What enters the feed |
| --- | --- |
| [IPdeny](https://www.ipdeny.com/) | Iran IPv4 and IPv6 country ranges. |
| [NRO](https://www.nro.net/about/rirs/statistics/) | Allocated or assigned `IR` resources, plus resources linked to selected ASNs through the same registered holder. Holder IDs are matched only within one registry and snapshot. |
| [IPtoASN](https://iptoasn.com/) | Ranges labelled `IR` or associated with ASNs selected through NRO and the catalogue. |
| Provider inventories | Published ranges from the URLs in the catalogue. A repeated or private row is dropped; a file that would add more than 4,096 addresses outside known Iranian space is refused. |

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

## Provider research

Iranian hosting companies rent servers from foreign hosts. The registry lists
those addresses under the foreign host's country and IPtoASN shows the foreign
host's network, so the only public source that ties them to Iran is a list the
company publishes itself. Weekly research works through `provider-ledger.json`
looking for such lists and for networks the same company registered under
another country. See [provider research](provider-research.md).

## Attribution

Upstream data keeps its own terms; the repository's MIT license covers the code.
IPdeny publishes [terms](https://www.ipdeny.com/tos.php) and
[redistribution notices](https://www.ipdeny.com/copyright.php). IPtoASN publishes
its database under PDDL-1.0. Provider sources and evidence URLs are recorded in
the catalogue.
