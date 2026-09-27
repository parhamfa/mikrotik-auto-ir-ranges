from __future__ import annotations

import gzip
import io
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

from auto_ir_ranges.generator import GenerationError, download, parse_iptoasn_gzip


URL = "https://example.org/source"


def response(payload: bytes, *encodings: str, content_type: str = "text/plain"):
    result = io.BytesIO(payload)
    result.headers = Message()
    result.headers["Content-Type"] = content_type
    for encoding in encodings:
        result.headers["Content-Encoding"] = encoding
    return result


class DownloadTests(unittest.TestCase):
    def test_plain_text_is_unchanged(self):
        payload = b"185.215.232.0/22\n"
        for headers in ((), ("identity",)):
            with self.subTest(headers=headers), patch(
                "urllib.request.urlopen", return_value=response(payload, *headers)
            ):
                self.assertEqual(download(URL, attempts=1), payload)

    def test_http_gzip_text_is_decoded(self):
        payload = b"185.215.232.0/22\n"
        for encoding in ("gzip", " GZip ", "identity, gzip"):
            with self.subTest(encoding=encoding), patch(
                "urllib.request.urlopen", return_value=response(gzip.compress(payload), encoding)
            ):
                self.assertEqual(download(URL, attempts=1), payload)

    def test_stacked_http_gzip_encodings_are_decoded(self):
        payload = b"185.215.232.0/22\n"
        for headers in (("gzip, gzip",), ("gzip", "gzip")):
            with self.subTest(headers=headers), patch(
                "urllib.request.urlopen",
                return_value=response(gzip.compress(gzip.compress(payload)), *headers),
            ):
                self.assertEqual(download(URL, attempts=1), payload)

    def test_gzip_dataset_remains_compressed_for_its_parser(self):
        rows = (Path(__file__).parent / "fixtures/iptoasn-v4.tsv").read_bytes()
        dataset = gzip.compress(rows)
        for wire, headers in ((dataset, ()), (dataset, ("identity",)), (gzip.compress(dataset), ("gzip",))):
            with self.subTest(headers=headers), patch(
                "urllib.request.urlopen", return_value=response(wire, *headers, content_type="application/gzip")
            ):
                actual = download(URL + ".tsv.gz", attempts=1)
                self.assertEqual(actual, dataset)
                _, asns = parse_iptoasn_gzip(actual, 4)
                self.assertEqual(asns, {100, 300})

    @patch("auto_ir_ranges.generator.MAX_DOWNLOAD_BYTES", 64)
    def test_wire_size_limit_is_enforced(self):
        for headers in ((), ("gzip",)):
            with self.subTest(headers=headers), patch(
                "urllib.request.urlopen", return_value=response(b"x" * 65, *headers)
            ):
                with self.assertRaisesRegex(GenerationError, "source exceeds 64 bytes"):
                    download(URL, attempts=1)

    @patch("auto_ir_ranges.generator.MAX_DOWNLOAD_BYTES", 64)
    def test_decoded_size_limit_includes_all_gzip_members(self):
        for wire in (gzip.compress(b"x" * 4096), gzip.compress(b"x" * 40) + gzip.compress(b"y" * 40)):
            self.assertLessEqual(len(wire), 64)
            with self.subTest(wire_bytes=len(wire)), patch(
                "urllib.request.urlopen", return_value=response(wire, "gzip")
            ):
                with self.assertRaisesRegex(GenerationError, "decoded source exceeds 64 bytes"):
                    download(URL, attempts=1)

    @patch("auto_ir_ranges.generator.MAX_DOWNLOAD_BYTES", 64)
    def test_exact_size_limit_is_allowed(self):
        payload = b"x" * 64
        for wire, headers in ((payload, ()), (gzip.compress(payload), ("gzip",))):
            with self.subTest(headers=headers), patch(
                "urllib.request.urlopen", return_value=response(wire, *headers)
            ):
                self.assertEqual(download(URL, attempts=1), payload)

    def test_corrupt_or_truncated_gzip_is_rejected(self):
        valid = gzip.compress(b"185.215.232.0/22\n")
        corrupt = valid[:-8] + bytes([valid[-8] ^ 1]) + valid[-7:]
        for wire in (b"not gzip", valid[:-1], corrupt):
            with self.subTest(wire=wire), patch(
                "urllib.request.urlopen", return_value=response(wire, "gzip")
            ):
                with self.assertRaisesRegex(GenerationError, "download failed"):
                    download(URL, attempts=1)

    def test_unsupported_http_encoding_is_rejected(self):
        for encoding in ("br", "deflate", "gzip, br"):
            with self.subTest(encoding=encoding), patch(
                "urllib.request.urlopen", return_value=response(b"source", encoding)
            ):
                with self.assertRaisesRegex(GenerationError, "unsupported HTTP Content-Encoding"):
                    download(URL, attempts=1)

    def test_empty_sources_are_rejected(self):
        for wire, headers in ((b"", ()), (gzip.compress(b""), ("gzip",))):
            with self.subTest(headers=headers), patch(
                "urllib.request.urlopen", return_value=response(wire, *headers)
            ):
                with self.assertRaisesRegex(GenerationError, "source is empty"):
                    download(URL, attempts=1)

    def test_failed_decode_retries_a_fresh_response(self):
        payload = b"185.215.232.0/22\n"
        sleep = Mock()
        with patch("urllib.request.urlopen", side_effect=[
            response(b"truncated", "gzip"), response(gzip.compress(payload), "gzip")
        ]) as opener:
            self.assertEqual(download(URL, attempts=2, sleep=sleep), payload)
        self.assertEqual(opener.call_count, 2)
        sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
