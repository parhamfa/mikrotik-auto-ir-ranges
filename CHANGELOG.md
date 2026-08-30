# Changelog

## v1.0.1

- Fix RouterOS associative-map membership checks. Missing map keys have type
  `nothing`; v1.0.0 incorrectly checked for `nil` and therefore stopped safely
  during pre-mutation feed validation.

## v1.0.0

- Initial public release.
