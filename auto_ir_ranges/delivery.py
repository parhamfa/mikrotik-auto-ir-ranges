"""Immutable, bounded pages and a small generation manifest (protocol v2)."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from pathlib import Path

from .errors import GenerationError

PAGE_BYTES = 48 * 1024
MAX_ENTRIES = 50_000
MAX_MANIFEST_BYTES = 48 * 1024
V1_LIMITS = {4: (5000, 61440), 6: (2000, 61440)}


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def build(feeds, *, generated_at, generator_version, page_bytes=PAGE_BYTES):
    if not 64 <= page_bytes <= PAGE_BYTES:
        raise GenerationError("invalid page byte ceiling")
    generation = hashlib.sha256(feeds[4].data + b"\0" + feeds[6].data + b"\0" + generated_at.encode()).hexdigest()
    manifest = {"schema": 2, "country": "IR", "generation": generation,
                "generated_at": generated_at, "generator_version": generator_version,
                "max_page_bytes": PAGE_BYTES, "entry_ceiling": MAX_ENTRIES,
                "legacy_compatible": all(f.count <= V1_LIMITS[v][0] and f.size <= V1_LIMITS[v][1] for v, f in feeds.items())}
    pages = {}
    for family, feed in feeds.items():
        if not 1 <= feed.count <= MAX_ENTRIES:
            raise GenerationError(f"IPv{family} capacity exceeded or empty: {feed.count}")
        chunks, pending = [], b""
        for row in feed.data.splitlines(keepends=True):
            if len(row) > page_bytes:
                raise GenerationError("single row exceeds page capacity")
            if len(pending) + len(row) > page_bytes:
                chunks.append(pending)
                pending = b""
            pending += row
        if pending:
            chunks.append(pending)
        descriptors = []
        for number, data in enumerate(chunks, 1):
            path = f"generations/{generation}/ipv{family}-{number:04d}.zone"
            pages[path] = data
            descriptors.append({"number": number, "family": family, "generation": generation,
                                "file": path, "count": data.count(b"\n"), "bytes": len(data),
                                "sha512": hashlib.sha512(data).hexdigest()})
        manifest[f"ipv{family}"] = {"count": feed.count, "bytes": feed.size, "sha512": feed.sha512, "pages": descriptors}
    raw = encoded(manifest)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise GenerationError("manifest exceeds router fetch capacity")
    validate(raw, pages.__getitem__)
    pages[f"generations/{generation}/manifest.json"] = raw
    return raw, pages


def validate(raw, read_page, *, min_counts=None):
    """Offline reference validator; reads every page before returning membership."""
    try:
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("manifest too large")
        manifest = json.loads(raw)
        generation = manifest["generation"]
        if manifest["schema"] != 2 or manifest["country"] != "IR" or not re.fullmatch(r"[0-9a-f]{64}", generation):
            raise ValueError("invalid generation header")
        result, whole = {}, {}
        for family in (4, 6):
            section = manifest[f"ipv{family}"]
            if not (min_counts or {}).get(family, 1) <= section["count"] <= MAX_ENTRIES:
                raise ValueError(f"IPv{family} capacity/count violation")
            if not 1 <= len(section["pages"]) <= 64:
                raise ValueError("invalid page count")
            networks, blocks = [], []
            for number, page in enumerate(section["pages"], 1):
                path = f"generations/{generation}/ipv{family}-{number:04d}.zone"
                if page["generation"] != generation or page["number"] != number or page["family"] != family or page["file"] != path:
                    raise ValueError("mixed generation or wrong page sequence/path")
                if not 1 <= page["bytes"] <= PAGE_BYTES:
                    raise ValueError("invalid page size")
                data = read_page(path)
                if len(data) != page["bytes"] or hashlib.sha512(data).hexdigest() != page["sha512"]:
                    raise ValueError("page byte length or SHA-512 mismatch")
                if not data.endswith(b"\n"):
                    raise ValueError("missing final newline")
                rows = data.decode("ascii").splitlines()
                if len(rows) != page["count"] or not rows:
                    raise ValueError("page count mismatch")
                for row in rows:
                    n = ipaddress.ip_network(row, strict=True)
                    if str(n) != row or n.version != family:
                        raise ValueError("non-canonical or wrong-family CIDR")
                    networks.append(n)
                blocks.append(data)
            data = b"".join(blocks)
            if len(networks) != section["count"] or len(data) != section["bytes"] or hashlib.sha512(data).hexdigest() != section["sha512"]:
                raise ValueError("family count/bytes/hash mismatch")
            if len(set(networks)) != len(networks) or networks != sorted(networks, key=lambda n: (int(n.network_address), n.prefixlen)):
                raise ValueError("duplicate or unsorted CIDRs across pages")
            result[family] = networks
            whole[family] = data
        if hashlib.sha256(whole[4] + b"\0" + whole[6] + b"\0" + manifest["generated_at"].encode()).hexdigest() != generation:
            raise ValueError("generation digest mismatch")
        return result
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise GenerationError(f"invalid paged generation: {exc}") from exc


def preflight(desired, current, *, free_memory, free_storage):
    """Conservative transient-union budget, mirrored in the RouterOS updater."""
    if any(not 1 <= n <= MAX_ENTRIES for n in desired):
        raise GenerationError("entry capacity exceeded")
    count = sum(desired) + sum(current)
    required = {"memory": 16*1024*1024 + count*2048, "storage": 4*1024*1024 + count*384}
    if free_memory < required["memory"] or free_storage < required["storage"]:
        raise GenerationError(f"insufficient router capacity: required {required}")
    return required


def dns_shrink_proof(previous, desired, records, family):
    """Permit DNS churn only if every removed address was a prior exact answer.

    Any loss outside those exact host addresses keeps the normal shrink guard.
    This prevents an expired, large DNS layer from freezing healthy core feeds.
    """
    from .coverage import subtract_networks
    lost = subtract_networks(previous, desired, family)
    hosts = [ipaddress.ip_network(row["address"]) for row in records if ipaddress.ip_address(row["address"]).version == family]
    if not lost or subtract_networks(lost, hosts, family):
        return None
    return {"reason": "removed space covered only by prior DNS observations", "previous_count": len(previous), "removed_addresses": str(sum(n.num_addresses for n in lost))}
