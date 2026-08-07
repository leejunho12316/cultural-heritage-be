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
    s3_key: str


_OUTPUT_USAGE = {
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


def parse_s3_key(raw_key: str) -> ParsedS3Key | None:
    key = unquote_plus(raw_key).lstrip("/")

    # S3 콘솔에서 폴더 생성 시 만들어지는 0바이트 folder marker 제외
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
            usage_name="XRAY_ORIGINAL",
            source_order=_source_order(parts[-1]),
            s3_key=key,
        )
    if parts[2:4] == ["inputs", "color"] and len(parts) >= 5:
        return ParsedS3Key(
            artifact_id=artifact_id,
            module_type="XRAY",
            usage_name="COLOR_ORIGINAL",
            source_order=None,
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
                s3_key=key,
            )
    return None


def _source_order(file_name: str) -> int | None:
    # Natural-number hint only. Exact originalSourceIndex is stored in layout.json.
    numbers = re.findall(r"\d+", file_name)
    return int(numbers[-1]) if numbers else None
