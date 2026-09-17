import json
import tempfile
import unittest
from pathlib import Path

from auto_ir_ranges.generator import generate_from_sources, publish_artifacts, parse_iptoasn_rows
from auto_ir_ranges.provenance import explain
from auto_ir_ranges.errors import GenerationError
from test_coverage import generation_inputs, LIMITS


class ProvenanceTests(unittest.TestCase):
    def test_explanation_distinguishes_provider_registration_and_origin_evidence(self):
        payloads, policy = generation_inputs()
        artifact = generate_from_sources(payloads, policy=policy, generated_at="2026-09-16T00:00:00Z", limits=LIMITS)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            publish_artifacts(artifact, directory)
            address = explain("193.24.119.1", directory)
            self.assertTrue(address["included"])
            self.assertIn("provider_arvancloud", address["contributing_layers"])
            self.assertFalse(address["asn_origins"])
            asn = explain("AS208006", directory)
            self.assertTrue(asn["selected"])
            self.assertEqual(asn["admitted_origins"][0]["country"], "AE")
            self.assertEqual(asn["registry_evidence"][0]["holder_selection_evidence"]["reason"], "reviewed ASN anchor")
            self.assertFalse(explain("AS15169", directory)["selected"])
            self.assertFalse(explain("8.8.8.8", directory)["included"])
            manifest = json.loads((directory/"manifest-v2.json").read_bytes())
            manifest["generated_at"] = "2026-09-17T00:00:00Z"
            (directory/"manifest-v2.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "generation time"):
                explain("AS208006", directory)

    def test_malformed_unselected_core_row_still_blocks_collection(self):
        rows = "2.144.0.0\t2.144.0.255\t100\tIR\tTEST\nnot-an-ip\t8.8.8.8\t15169\tUS\tGOOGLE\n"
        with self.assertRaises(GenerationError):
            parse_iptoasn_rows(rows, 4)


if __name__ == "__main__":
    unittest.main()
