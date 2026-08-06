"""Validation of accepted detector records into rough candidates."""

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Final

from modules.prompt_generating import PromptRecord
from modules.rough_masking.artifacts.assets import AssetReference, normalize_asset
from modules.rough_masking.artifacts.records import JsonValue
from modules.rough_masking.contracts import AdapterRequest, SeedThresholds
from modules.shared import CandidateId, DetectorLane, ImageId, PromptMetadata


class CandidateStatus(StrEnum):
    """Only normalized accepted records become rough candidates."""

    ACCEPTED = "accepted"


BBOX_COORDINATE_COUNT: Final = 4
ROUGH_MASK_SEMANTICS: Final = "anomaly_region"
IDENTITY_RECORD_KEYS: Final = (
    "accepted",
    "bbox_xyxy",
    "generation_lane",
    "image",
    "mask_path",
    "mask_semantics",
    "overlay_path",
    "prompt",
    "prompt_pack_id",
    "score",
)


@dataclass(frozen=True, slots=True)
class RawDetectorCandidate:
    """Validated common representation for an accepted detector record."""

    candidate_id: CandidateId
    image_id: ImageId
    lane: DetectorLane
    status: CandidateStatus
    executable_prompt: str
    score: float
    bbox_xyxy: tuple[float, float, float, float]
    rough_mask: AssetReference
    overlay: AssetReference
    detector_model_id: str
    sam2_model_id: str
    threshold_config: SeedThresholds
    prompt_provenance: PromptMetadata
    source_view_id: str
    source_object_id: str | None
    source_tile_view_id: str | None
    diagnostics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateParts:
    """Normalized values required to construct an accepted candidate."""

    score: float
    image: str
    bbox_xyxy: tuple[float, float, float, float]
    mask: AssetReference
    overlay: AssetReference
    prompt: PromptRecord


def _number(
    raw: JsonValue | None, missing: str, invalid: str
) -> tuple[float | None, str | None]:
    if raw is None:
        return None, missing
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None, invalid
    value = float(raw)
    return (value, None) if math.isfinite(value) else (None, invalid)


def _score(record: dict[str, JsonValue]) -> tuple[float | None, str | None]:
    if record.get("accepted") is False:
        return None, f"record_rejected:{record.get('reject_reason', 'unspecified')}"
    return _number(record.get("score"), "score_missing", "score_invalid")


def _image(
    request: AdapterRequest, record: dict[str, JsonValue]
) -> tuple[str | None, str | None]:
    image = record.get("image")
    if not isinstance(image, str) or image != request.view.image_id:
        return None, "image_id_missing"
    return image, None


def _bbox(
    request: AdapterRequest, record: dict[str, JsonValue]
) -> tuple[tuple[float, float, float, float] | None, str | None]:
    bbox = record.get("bbox_xyxy")
    if not isinstance(bbox, list) or len(bbox) != BBOX_COORDINATE_COUNT:
        return None, "bbox_missing"
    values: list[float] = []
    for raw in bbox:
        value, diagnostic = _number(raw, "bbox_missing", "bbox_not_finite")
        if diagnostic is not None or value is None:
            return None, diagnostic
        values.append(value)
    left, top, right, bottom = values
    if min(values) < 0:
        return None, "bbox_negative"
    if right <= left or bottom <= top:
        return None, "bbox_invalid_order"
    if right > request.image_width_px or bottom > request.image_height_px:
        return None, "bbox_outside_image"
    return (left, top, right, bottom), None


def _assets(
    request: AdapterRequest, record: dict[str, JsonValue]
) -> tuple[tuple[AssetReference, AssetReference] | None, str | None]:
    semantics = record.get("mask_semantics")
    if semantics is None:
        return None, "mask_semantics_missing"
    if semantics != ROUGH_MASK_SEMANTICS:
        return None, "mask_semantics_invalid"
    mask, diagnostic = normalize_asset(
        request.lane_output_dir, record.get("mask_path"), "mask"
    )
    if diagnostic is not None or mask is None:
        return None, diagnostic
    overlay, diagnostic = normalize_asset(
        request.lane_output_dir, record.get("overlay_path"), "overlay"
    )
    if diagnostic is not None or overlay is None:
        return None, diagnostic
    return (mask, overlay), None


def _prompt(
    request: AdapterRequest, record: dict[str, JsonValue]
) -> tuple[PromptRecord | None, str | None]:
    prompt = record.get("prompt")
    matched = next(
        (item for item in request.prompts if item.prompt_text == prompt), None
    )
    if matched is None:
        return None, "prompt_not_locked_seed"
    if record.get("prompt_pack_id") != matched.metadata.prompt_pack_id:
        return None, "prompt_pack_id_mismatch"
    if record.get("generation_lane") != matched.metadata.model_lane.value:
        return None, "generation_lane_mismatch"
    return matched, None


def _candidate_parts(
    request: AdapterRequest, record: dict[str, JsonValue]
) -> tuple[CandidateParts | None, str | None]:
    score, diagnostic = _score(record)
    if diagnostic is not None or score is None:
        return None, diagnostic
    image, diagnostic = _image(request, record)
    if diagnostic is not None or image is None:
        return None, diagnostic
    bbox, diagnostic = _bbox(request, record)
    if diagnostic is not None or bbox is None:
        return None, diagnostic
    assets, diagnostic = _assets(request, record)
    if diagnostic is not None or assets is None:
        return None, diagnostic
    prompt, diagnostic = _prompt(request, record)
    if diagnostic is not None or prompt is None:
        return None, diagnostic
    return CandidateParts(score, image, bbox, *assets, prompt), None


def normalize_candidate(
    request: AdapterRequest, record: dict[str, JsonValue]
) -> tuple[RawDetectorCandidate | None, str | None]:
    """Normalize one accepted runner record or return its diagnostic."""
    parts, diagnostic = _candidate_parts(request, record)
    if diagnostic is not None or parts is None:
        return None, diagnostic
    identity_record = {
        key: record[key] for key in IDENTITY_RECORD_KEYS if key in record
    }
    identity = json.dumps(
        {
            "record": identity_record,
            "mask_sha256": parts.mask.sha256,
            "overlay_sha256": parts.overlay.sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    candidate_id = CandidateId(
        sha256(f"{request.lane}|{parts.image}|{identity}".encode()).hexdigest()
    )
    return RawDetectorCandidate(
        candidate_id,
        ImageId(parts.image),
        request.lane,
        CandidateStatus.ACCEPTED,
        parts.prompt.prompt_text,
        parts.score,
        parts.bbox_xyxy,
        parts.mask,
        parts.overlay,
        request.detector_model_id,
        request.sam2_model_id,
        request.threshold_config,
        parts.prompt.metadata,
        request.view.view_id,
        request.view.object_id,
        request.view.tile_view_id,
        (),
    ), None
