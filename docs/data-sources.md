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

The generator selects rows whose ASN country field is `IR`, converts each
address range to CIDRs, and unions those networks with the IPdeny zones.

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
