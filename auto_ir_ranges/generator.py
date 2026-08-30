from __future__ import annotations

import argparse
import gzip
import hashlib
import ipaddress
import json
import os
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from . import __version__


SCHEMA_VERSION = 1
COUNTRY_CODE = "IR"
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
USER_AGENT = (
    f"mikrotik-auto-ir-ranges/{__version__} "
    "(+https://github.com/parhamfa/mikrotik-auto-ir-ranges)"
)

SOURCE_URLS = {
    "ipdeny_ipv4": "https://www.ipdeny.com/ipblocks/data/countries/ir.zone",
    "ipdeny_ipv6": "https://www.ipdeny.com/ipv6/ipaddresses/blocks/ir.zone",
    "iptoasn_ipv4": "https://iptoasn.com/data/ip2asn-v4.tsv.gz",
    "iptoasn_ipv6": "https://iptoasn.com/data/ip2asn-v6.tsv.gz",
}

DEFAULT_LIMITS = {
    4: {"min_count": 1_000, "max_count": 5_000, "max_bytes": 60 * 1024},
    6: {"min_count": 300, "max_count": 2_000, "max_bytes": 60 * 1024},
}


class GenerationError(RuntimeError):
    """Raised when a source or generated artifact fails validation."""


@dataclass(frozen=True)
class Feed:
    data: bytes
    count: int

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def sha512(self) -> str:
        return hashlib.sha512(self.data).hexdigest()


@dataclass(frozen=True)
class GeneratedArtifacts:
    ipv4: Feed
    ipv6: Feed
    manifest: bytes


def download(
    url: str,
    *,
    attempts: int = 3,
    timeout: int = 180,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """Download one source with bounded reads and exponential retry."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(payload) > MAX_DOWNLOAD_BYTES:
                raise GenerationError(f"source exceeds {MAX_DOWNLOAD_BYTES} bytes: {url}")
            if not payload:
                raise GenerationError(f"source is empty: {url}")
            return payload
        except Exception as exc:  # urllib exposes several unrelated error types
            last_error = exc
            if attempt < attempts:
                sleep(2 ** (attempt - 1))
    raise GenerationError(f"download failed after {attempts} attempts: {url}: {last_error}")


def parse_country_zone(payload: bytes | str, version: int) -> list[ipaddress._BaseNetwork]:
    """Parse an IPdeny zone and reject malformed or wrong-family rows."""
    text = payload.decode("ascii") if isinstance(payload, bytes) else payload
    networks: list[ipaddress._BaseNetwork] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            network = ipaddress.ip_network(line, strict=False)
        except ValueError as exc:
            raise GenerationError(f"invalid country CIDR at line {line_number}: {line}") from exc
        if network.version != version:
            raise GenerationError(
                f"wrong IP family in IPv{version} country source at line {line_number}: {line}"
            )
        networks.append(network)
    if not networks:
        raise GenerationError(f"IPv{version} country source contains no CIDRs")
    return networks


def parse_iptoasn_rows(
    payload: bytes | str,
    version: int,
    *,
    country_code: str = COUNTRY_CODE,
) -> tuple[list[ipaddress._BaseNetwork], set[int]]:
    """Select ranges whose originating ASN country is Iran and convert them to CIDRs."""
    text = payload.decode("utf-8", "strict") if isinstance(payload, bytes) else payload
    networks: list[ipaddress._BaseNetwork] = []
    asns: set[int] = set()
    wanted_country = country_code.upper()

    for line_number, raw in enumerate(text.splitlines(), start=1):
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t", 4)
        if len(parts) < 4:
            raise GenerationError(f"malformed IPtoASN row at line {line_number}")
        if parts[3].strip().upper() != wanted_country:
            continue
        try:
            first = ipaddress.ip_address(parts[0].strip())
            last = ipaddress.ip_address(parts[1].strip())
            asn = int(parts[2])
        except ValueError as exc:
            raise GenerationError(f"invalid Iranian IPtoASN row at line {line_number}") from exc
        if first.version != version or last.version != version or first > last:
            raise GenerationError(f"wrong or reversed range at IPtoASN line {line_number}")
        if asn <= 0:
            raise GenerationError(f"invalid ASN at IPtoASN line {line_number}: {asn}")
        asns.add(asn)
        networks.extend(ipaddress.summarize_address_range(first, last))

    if not networks:
        raise GenerationError(f"IPtoASN contains no IPv{version} rows for {wanted_country}")
    return networks, asns


def parse_iptoasn_gzip(
    payload: bytes,
    version: int,
    *,
    country_code: str = COUNTRY_CODE,
) -> tuple[list[ipaddress._BaseNetwork], set[int]]:
    try:
        uncompressed = gzip.decompress(payload)
    except (OSError, EOFError) as exc:
        raise GenerationError(f"invalid IPv{version} IPtoASN gzip stream") from exc
    return parse_iptoasn_rows(uncompressed, version, country_code=country_code)


def collapse_networks(
    networks: Iterable[ipaddress._BaseNetwork], version: int
) -> list[ipaddress._BaseNetwork]:
    materialized = list(networks)
    if any(network.version != version for network in materialized):
        raise GenerationError(f"mixed address families passed to IPv{version} aggregation")
    return sorted(
        ipaddress.collapse_addresses(materialized),
        key=lambda network: (int(network.network_address), network.prefixlen),
    )


def serialize_zone(networks: Sequence[ipaddress._BaseNetwork]) -> bytes:
    if not networks:
        return b""
    return ("\n".join(str(network) for network in networks) + "\n").encode("ascii")


def parse_and_validate_zone(
    data: bytes,
    version: int,
    *,
    limits: Mapping[int, Mapping[str, int]] = DEFAULT_LIMITS,
) -> list[ipaddress._BaseNetwork]:
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise GenerationError(f"IPv{version} output is not ASCII") from exc
    if not data.endswith(b"\n"):
        raise GenerationError(f"IPv{version} output lacks a final newline")
    if any(not line for line in lines):
        raise GenerationError(f"IPv{version} output contains an empty line")

    networks: list[ipaddress._BaseNetwork] = []
    for line_number, line in enumerate(lines, start=1):
        try:
            network = ipaddress.ip_network(line, strict=True)
        except ValueError as exc:
            raise GenerationError(f"invalid generated CIDR at line {line_number}: {line}") from exc
        if network.version != version:
            raise GenerationError(f"wrong family in generated IPv{version} output: {line}")
        if str(network) != line:
            raise GenerationError(f"non-canonical generated CIDR: {line}")
        networks.append(network)

    if len(set(networks)) != len(networks):
        raise GenerationError(f"IPv{version} output contains duplicate CIDRs")
    if networks != sorted(networks, key=lambda n: (int(n.network_address), n.prefixlen)):
        raise GenerationError(f"IPv{version} output is not numerically sorted")

    policy = limits[version]
    if not policy["min_count"] <= len(networks) <= policy["max_count"]:
        raise GenerationError(
            f"IPv{version} count {len(networks)} outside "
            f"{policy['min_count']}..{policy['max_count']}"
        )
    if len(data) > policy["max_bytes"]:
        raise GenerationError(
            f"IPv{version} output is {len(data)} bytes, above {policy['max_bytes']}"
        )
    return networks


def validate_shrink(new_count: int, previous_count: int, version: int) -> None:
    if previous_count > 0 and new_count * 2 < previous_count:
        raise GenerationError(
            f"IPv{version} output shrank from {previous_count} to {new_count} (>50%)"
        )


def _source_record(url: str, payload: bytes) -> dict[str, str | int]:
    return {
        "url": url,
        "bytes": len(payload),
        "sha512": hashlib.sha512(payload).hexdigest(),
    }


def generate_from_sources(
    source_payloads: Mapping[str, bytes],
    *,
    generated_at: str | None = None,
    source_urls: Mapping[str, str] = SOURCE_URLS,
    limits: Mapping[int, Mapping[str, int]] = DEFAULT_LIMITS,
) -> GeneratedArtifacts:
    missing = sorted(set(SOURCE_URLS) - set(source_payloads))
    if missing:
        raise GenerationError(f"missing source payloads: {', '.join(missing)}")

    country_v4 = parse_country_zone(source_payloads["ipdeny_ipv4"], 4)
    country_v6 = parse_country_zone(source_payloads["ipdeny_ipv6"], 6)
    asn_v4, asns_v4 = parse_iptoasn_gzip(source_payloads["iptoasn_ipv4"], 4)
    asn_v6, asns_v6 = parse_iptoasn_gzip(source_payloads["iptoasn_ipv6"], 6)

    collapsed_v4 = collapse_networks([*country_v4, *asn_v4], 4)
    collapsed_v6 = collapse_networks([*country_v6, *asn_v6], 6)
    feed_v4 = Feed(serialize_zone(collapsed_v4), len(collapsed_v4))
    feed_v6 = Feed(serialize_zone(collapsed_v6), len(collapsed_v6))
    parse_and_validate_zone(feed_v4.data, 4, limits=limits)
    parse_and_validate_zone(feed_v6.data, 6, limits=limits)

    timestamp = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    manifest = {
        "schema": SCHEMA_VERSION,
        "generated_at": timestamp,
        "country": COUNTRY_CODE,
        "generator_version": __version__,
        "coverage": {
            "country_ranges": "IPdeny Iran country zones",
            "asn_ranges": "IPtoASN rows whose origin-ASN country code is IR",
            "operation": "union, collapse, numeric sort",
        },
        "ipv4": {
            "file": "ir-ipv4.zone",
            "count": feed_v4.count,
            "bytes": feed_v4.size,
            "sha512": feed_v4.sha512,
        },
        "ipv6": {
            "file": "ir-ipv6.zone",
            "count": feed_v6.count,
            "bytes": feed_v6.size,
            "sha512": feed_v6.sha512,
        },
        "source_stats": {
            "ipv4": {
                "ipdeny_cidrs": len(country_v4),
                "iptoasn_ir_ranges": len(asn_v4),
                "iptoasn_ir_asns": len(asns_v4),
            },
            "ipv6": {
                "ipdeny_cidrs": len(country_v6),
                "iptoasn_ir_ranges": len(asn_v6),
                "iptoasn_ir_asns": len(asns_v6),
            },
        },
        "sources": {
            key: _source_record(source_urls[key], source_payloads[key])
            for key in sorted(source_payloads)
        },
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return GeneratedArtifacts(feed_v4, feed_v6, manifest_bytes)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _previous_count(path: Path, version: int) -> int | None:
    if not path.exists():
        return None
    data = path.read_bytes()
    try:
        return len(parse_and_validate_zone(data, version))
    except GenerationError as exc:
        raise GenerationError(f"refusing to replace invalid previous {path.name}: {exc}") from exc


def publish_artifacts(
    artifacts: GeneratedArtifacts,
    output_dir: Path,
    *,
    previous_dir: Path | None = None,
) -> bool:
    """Atomically write changed feeds. Preserve the old manifest on unchanged runs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    previous_dir = previous_dir or output_dir
    for filename, feed, version in (
        ("ir-ipv4.zone", artifacts.ipv4, 4),
        ("ir-ipv6.zone", artifacts.ipv6, 6),
    ):
        prior = _previous_count(previous_dir / filename, version)
        if prior is not None:
            validate_shrink(feed.count, prior, version)

    v4_path = output_dir / "ir-ipv4.zone"
    v6_path = output_dir / "ir-ipv6.zone"
    unchanged = (
        v4_path.exists()
        and v6_path.exists()
        and v4_path.read_bytes() == artifacts.ipv4.data
        and v6_path.read_bytes() == artifacts.ipv6.data
    )
    if unchanged:
        return False

    _atomic_write(v4_path, artifacts.ipv4.data)
    _atomic_write(v6_path, artifacts.ipv6.data)
    _atomic_write(output_dir / "manifest.json", artifacts.manifest)
    return True


def build_live_artifacts(
    *,
    source_urls: Mapping[str, str] = SOURCE_URLS,
    generated_at: str | None = None,
) -> GeneratedArtifacts:
    payloads: dict[str, bytes] = {}
    for name in sorted(source_urls):
        print(f"downloading {name}: {source_urls[name]}", file=sys.stderr)
        payloads[name] = download(source_urls[name])
    return generate_from_sources(
        payloads,
        generated_at=generated_at,
        source_urls=source_urls,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--previous-dir",
        type=Path,
        help="directory containing the last published feeds (defaults to output-dir)",
    )
    parser.add_argument("--generated-at", help="fixed RFC3339 timestamp for reproducible tests")
    args = parser.parse_args(argv)

    try:
        artifacts = build_live_artifacts(generated_at=args.generated_at)
        changed = publish_artifacts(
            artifacts,
            args.output_dir,
            previous_dir=args.previous_dir,
        )
    except GenerationError as exc:
        print(f"generation failed: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "changed": changed,
                "ipv4_count": artifacts.ipv4.count,
                "ipv4_bytes": artifacts.ipv4.size,
                "ipv6_count": artifacts.ipv6.count,
                "ipv6_bytes": artifacts.ipv6.size,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
