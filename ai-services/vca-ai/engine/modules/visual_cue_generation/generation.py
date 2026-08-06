"""Batch Qwen bridge generation for rough-mask outputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PIL import Image, UnidentifiedImageError

from modules.mask_refining import QwenRefinementRequest, refine_candidate
from modules.rag.qwen import QwenBridgeCandidateArtifact, write_qwen_bridge_results
from modules.shared import CandidateId, QwenBridgeResult, QwenBridgeStatus
from modules.visual_cue_generation.models import (
    QwenBridgeGenerationInputs,
    QwenBridgeGenerationResult,
)
from modules.visual_cue_generation.rough_records import rough_qwen_candidates
from modules.visual_cue_generation.source_manifest import source_assets_by_image

if TYPE_CHECKING:
    from pathlib import Path

    from modules.mask_refining import QwenBackend
    from modules.mask_refining.rendering.views import QwenViewRenderer
    from modules.rag.operations.candidate_sidecar_artifacts import RoughRagCandidate


def generate_qwen_bridge_results(
    inputs: QwenBridgeGenerationInputs,
    renderer: QwenViewRenderer,
    backend: QwenBackend,
) -> QwenBridgeGenerationResult:
    """Write Qwen bridge rows after batch-fatal source manifest validation."""
    source_assets = source_assets_by_image(
        inputs.input_manifest_path,
        inputs.asset_root,
    )
    rows: list[QwenBridgeCandidateArtifact] = []
    for rough_candidate in rough_qwen_candidates(inputs.rough_root, inputs.asset_root):
        rough_record = rough_candidate.rough
        source = source_assets.get(rough_candidate.candidate.image_id)
        if source is None:
            rows.append(
                _bridge_artifact(
                    rough_record,
                    _failed_source_row(rough_record.candidate_id),
                )
            )
            continue
        try:
            dimensions = _image_dimensions(inputs.asset_root / source.relative_path)
        except (OSError, UnidentifiedImageError):
            rows.append(
                _bridge_artifact(
                    rough_record,
                    _failed_decode_row(rough_record.candidate_id),
                )
            )
            continue
        request = QwenRefinementRequest(
            rough_candidate.candidate,
            source,
            inputs.asset_root,
            inputs.device,
            *dimensions,
        )
        result = refine_candidate(
            request,
            renderer,
            backend,
        )
        rows.append(_bridge_artifact(rough_record, result.to_bridge_result()))
    artifact_path = write_qwen_bridge_results(inputs.rag_run_dir, tuple(rows))
    return QwenBridgeGenerationResult(
        artifact_path,
        len(rows),
        sum(row.result.status is QwenBridgeStatus.SUCCESS for row in rows),
        sum(row.result.status is QwenBridgeStatus.FAILED for row in rows),
    )


def _failed_source_row(candidate_id: CandidateId) -> QwenBridgeResult:
    return QwenBridgeResult(
        candidate_id=candidate_id,
        status=QwenBridgeStatus.FAILED,
        selected_terms=(),
        extracted_descriptors=(),
        confidence=None,
        reason="source_asset_missing",
        qwen_observation_id=None,
        input_view_hashes=(),
        failure_code="source_asset_missing",
    )


def _failed_decode_row(candidate_id: CandidateId) -> QwenBridgeResult:
    return QwenBridgeResult(
        candidate_id=candidate_id,
        status=QwenBridgeStatus.FAILED,
        selected_terms=(),
        extracted_descriptors=(),
        confidence=None,
        reason="image_decode_failed",
        qwen_observation_id=None,
        input_view_hashes=(),
        failure_code="image_decode_failed",
    )


def _bridge_artifact(
    rough_record: RoughRagCandidate,
    result: QwenBridgeResult,
) -> QwenBridgeCandidateArtifact:
    return QwenBridgeCandidateArtifact(
        rough_record_path=rough_record.rough_record_path,
        rough_record_index=rough_record.rough_record_index,
        result=result,
    )


def _image_dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size
