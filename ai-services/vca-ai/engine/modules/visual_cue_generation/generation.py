"""Batch Qwen bridge generation for rough-mask outputs."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

from PIL import Image, UnidentifiedImageError

from modules.mask_refining import QwenRefinementRequest, refine_candidate
from modules.rag.qwen import QwenBridgeCandidateArtifact, write_qwen_bridge_results
from modules.rough_masking import AssetReference
from modules.shared import (
    CandidateId,
    ContractValidationError,
    QwenBridgeResult,
    QwenBridgeStatus,
)
from modules.visual_cue_generation.models import (
    QwenBridgeGenerationInputs,
    QwenBridgeGenerationResult,
)
from modules.visual_cue_generation.rough_records import rough_qwen_candidates

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
    rows: list[QwenBridgeCandidateArtifact] = []
    for rough_candidate in rough_qwen_candidates(inputs.rough_root, inputs.asset_root):
        rough_record = rough_candidate.rough
        # view_image_path는 이 후보를 만든 뷰(객체 크롭 또는 타일)에 실제로
        # 입력된 이미지다 - candidate.bbox_xyxy와 항상 같은 좌표계라 원본 사진
        # 전체를 쓸 때와 달리 좌표 변환이 필요 없다. rough_qwen_candidates()가
        # 이미 존재를 검증했으므로(mask_path/overlay_path와 동일한 규약) 여기서는
        # 이미지 디코딩 실패만 소프트 실패로 처리한다.
        view_image_path = rough_candidate.candidate.view_image_path
        if view_image_path is None:
            field = "view_image_path"
            reason = "rough candidate missing its own view source image"
            raise ContractValidationError(field, reason)
        source = _view_asset_reference(view_image_path, inputs.asset_root)
        try:
            dimensions = _image_dimensions(view_image_path)
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


def _view_asset_reference(view_image_path: Path, asset_root: Path) -> AssetReference:
    resolved_root = asset_root.resolve()
    media_type = (
        "image/png" if view_image_path.suffix.lower() == ".png" else "image/jpeg"
    )
    return AssetReference(
        view_image_path.relative_to(resolved_root).as_posix(),
        sha256(view_image_path.read_bytes()).hexdigest(),
        media_type,
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
