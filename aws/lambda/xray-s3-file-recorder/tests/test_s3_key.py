import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from s3_key import parse_s3_key


class S3KeyTest(unittest.TestCase):
    def setUp(self):
        self.artifact = str(uuid.uuid4())

    def test_inputs(self):
        xray = parse_s3_key(f"xray/{self.artifact}/inputs/xray/piece_12.png")
        color = parse_s3_key(f"xray/{self.artifact}/inputs/color/front.jpg")
        self.assertEqual("XRAY_ORIGINAL", xray.usage_name)
        self.assertEqual(12, xray.source_order)
        self.assertEqual("COLOR_ORIGINAL", color.usage_name)

    def test_all_outputs(self):
        expected = {
            "assembled_xray.png": "ASSEMBLED",
            "layout.json": "LAYOUT",
            "report.json": "STITCH_REPORT",
            "finalization_bundle.zip": "FINALIZATION_BUNDLE",
            "layout.final.json": "FINAL_LAYOUT",
            "assembled_xray.final.png": "FINAL_ASSEMBLED",
            "source_owner.final.png": "SOURCE_OWNER",
            "fragment_owner.final.png": "FRAGMENT_OWNER",
            "seam_zone.final.png": "SEAM_ZONE",
            "overlap_mask.final.png": "OVERLAP_MASK",
            "provenance.final.json": "PROVENANCE",
        }
        for file_name, usage in expected.items():
            with self.subTest(file_name=file_name):
                parsed = parse_s3_key(f"xray/{self.artifact}/outputs/{file_name}")
                self.assertIsNotNone(parsed)
                self.assertEqual(usage, parsed.usage_name)

    def test_ignores_unknown(self):
        self.assertIsNone(parse_s3_key("visual/not-xray/file.png"))
        self.assertIsNone(parse_s3_key("xray/not-a-uuid/outputs/layout.json"))

    def test_ignores_folder_marker(self):
        artifact = "22222222-2222-2222-2222-222222222222"

        self.assertIsNone(parse_s3_key(f"xray/{artifact}/inputs/xray/"))
        self.assertIsNone(parse_s3_key(f"xray/{artifact}/inputs/color/"))


if __name__ == "__main__":
    unittest.main()
