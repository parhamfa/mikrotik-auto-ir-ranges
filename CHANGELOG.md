# Changelog

## v1.1.0 (2026-09-16, publisher)

- Separate Iranian operator identity from country labels using NRO resource
  holder links and reviewed foreign-registered operator ASNs.
- Import official ArvanCloud and MizbanCloud inventories and exact DNS
  endpoints for a reviewed service catalogue, including shared hosting.
- Publish source-by-source coverage evidence, timestamps, and DNS TTLs without
  claiming an unmeasured global coverage percentage.
- Reject truncated/stale registry inputs, stale or incomplete DNS snapshots,
  changed reviewed ASN identities, and abrupt provider source shrinkage.
- Preserve the schema-1 RouterOS feed contract and existing v1.0.2 installers.

## v1.0.2

- Replace per-CIDR RouterOS searches with linear membership scans. This keeps
  unchanged daily runs fast while preserving validate-first and add-first
  behavior.
- Use the immutable raw Git tag in setup commands for RouterOS 7.20 redirect
  compatibility, and document its built-in trust-anchor prerequisite.

## v1.0.1

- Fix RouterOS associative-map membership checks. Missing map keys have type
  `nothing`; v1.0.0 incorrectly checked for `nil` and therefore stopped safely
  during pre-mutation feed validation.

## v1.0.0

- Initial public release.
