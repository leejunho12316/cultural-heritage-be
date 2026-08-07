from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from urllib.parse import unquote_plus


@dataclass(frozen=True)
class ParsedS3Key:
    artifact_id: str
    module_type: str
    usage_name: str
    source_order: int | None
    original_name: str | None
    s3_key: str


_OUTPUT_USAGE = {
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


def parse_s3_key(raw_key: str) -> ParsedS3Key | None:
    key = unquote_plus(raw_key).lstrip("/")
    if not key or key.endswith("/"):
        return None

    parts = key.split("/")
    if len(parts) < 4 or parts[0] != "xray":
        return None

    artifact_id = parts[1]
    try:
        uuid.UUID(artifact_id)
    except ValueError:
        return None

    if parts[2:4] == ["inputs", "xray"] and len(parts) >= 5:
        return ParsedS3Key(
            artifact_id=artifact_id,
            module_type="XRAY",
            usage_name="xray_original",
            source_order=_source_order(parts[-1]),
            original_name=parts[-1],
            s3_key=key,
        )
    if parts[2:4] == ["inputs", "color"] and len(parts) >= 5:
        return ParsedS3Key(
            artifact_id=artifact_id,
            module_type="XRAY",
            usage_name="color_reference",
            source_order=None,
            original_name=parts[-1],
            s3_key=key,
        )
    if parts[2] == "outputs" and len(parts) == 4:
        usage = _OUTPUT_USAGE.get(parts[3])
        if usage:
            return ParsedS3Key(
                artifact_id=artifact_id,
                module_type="XRAY",
                usage_name=usage,
                source_order=None,
                original_name=parts[3],
                s3_key=key,
            )
    return None


def _source_order(file_name: str) -> int | None:
    # Metadata가 없는 기존 객체를 위한 fallback이다.
    numbers = re.findall(r"\d+", file_name)
    return int(numbers[-1]) if numbers else None
