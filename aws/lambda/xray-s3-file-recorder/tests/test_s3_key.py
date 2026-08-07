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
        self.assertEqual("xray_original", xray.usage_name)
        self.assertEqual(12, xray.source_order)
        self.assertEqual("piece_12.png", xray.original_name)
        self.assertEqual("color_reference", color.usage_name)

    def test_all_outputs(self):
        expected = {
            "assembled_xray.png": "assembled_auto",
            "layout.json": "layout_auto",
            "report.json": "report_json",
            "layout_fragment_masks.zip": "layout_fragment_masks",
            "layout.final.json": "layout_final",
            "assembled_xray.final.png": "assembled_final",
            "source_owner.final.png": "source_owner",
            "fragment_owner.final.png": "fragment_owner",
            "seam_zone.final.png": "seam_zone",
            "overlap_mask.final.png": "overlap_mask",
            "provenance.final.json": "provenance",
            "defect_result.png": "defect_result",
        }
        for file_name, usage in expected.items():
            with self.subTest(file_name=file_name):
                parsed = parse_s3_key(f"xray/{self.artifact}/outputs/{file_name}")
                self.assertIsNotNone(parsed)
                self.assertEqual(usage, parsed.usage_name)

    def test_ignores_unknown_and_folder_marker(self):
        self.assertIsNone(parse_s3_key("visual/not-xray/file.png"))
        self.assertIsNone(parse_s3_key("xray/not-a-uuid/outputs/layout.json"))
        self.assertIsNone(parse_s3_key(f"xray/{self.artifact}/inputs/xray/"))


if __name__ == "__main__":
    unittest.main()
