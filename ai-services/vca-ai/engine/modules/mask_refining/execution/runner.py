"""Prompt-grouped local refinement orchestration using rough-masking seams."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from modules.mask_refining.execution.assets import join_preprocessing_assets
from modules.mask_refining.execution.models import (
    AcceptedRefinedCandidate,
    PostRefinementQwenEvidence,
    PostRefinementQwenEvidenceFactory,
    RefinedExecutionRecord,
    RefinementRunnerFactory,
    RefinementRunRequest,
    RefinementRunResult,
    RefinementSkip,
    RefinementStatus,
    RunnerFactoryInput,
    SkipStage,
)
from modules.mask_refining.execution.prompts import read_prompt_variants
from modules.preprocessing.model_runtime.inventory import ModelInventoryEntry
from modules.prompt_generating import PromptRecord
from modules.rag.qwen.qwen_bridge_json import JsonValue, parse_json_object
from modules.rough_masking import (
    AdapterRequest,
    AssetReference,
    DetectorRunner,
    LocalModelCachePolicy,
    execute_adapter,
    seed_thresholds,
)
from modules.rough_masking.contracts import SAM2_MODEL_ID
from modules.rough_masking.local_model.runner import (
    DETECTOR_MODEL_KEYS,
    EXPECTED_MODEL_REPO_IDS,
    build_local_model_runner,
)
from modules.shared import (
    DETECTOR_ADAPTER_SCHEMA_VERSION,
    ContractValidationError,
    DetectorLane,
    RagLane,
    detector_to_rag_lane,
    ensure_no_symlink_leaf,
    resolve_model_path,
    update_stage_progress_count,
)

if TYPE_CHECKING:
    from modules.mask_refining.execution.models import (
        JoinedRefinementAssets,
        PromptVariantGroup,
    )
    from modules.preprocessing import CoordinateTransform
    from modules.rough_masking import RawDetectorCandidate
    from modules.shared import ImageId
    from modules.visual_cue_generation.rough_records import RoughQwenCandidate


def _detector_lane(lane: RagLane) -> DetectorLane:
    match lane:
        case RagLane.OWLV2:
            return DetectorLane.OWLV2_SAM2
        case RagLane.FLORENCE2:
            return DetectorLane.FLORENCE2_SAM2
        case RagLane.GROUNDINGDINO:
            return DetectorLane.GROUNDED_SAM2


# PromptVariant를 어댑터가 기대하는 PromptRecord로 변환한다. seeds.py의
# 시드 프롬프트와 달리 여기서는 세 플래그를 모두 True로 고정하는데,
# RAG 정제 프롬프트는 이미 최종 보존 어휘·이상 클래스 근거를
# 갖췄다는 뜻이다.
def _prompt_records(group: PromptVariantGroup) -> tuple[PromptRecord, ...]:
    return tuple(
        PromptRecord(
            metadata=variant.metadata,
            prompt_text=variant.generated_prompt,
            rough_target_anchor_only=True,
            final_conservation_vocabulary=True,
            anomaly_class_proof=True,
        )
        for variant in group.variants
    )


# parent_id를 해시해 lane별 detector 산출물 경로를 만든다. 원본 후보
# ID를 그대로 파일 경로에 쓰지 않아 파일시스템에 안전하지 않은 문자나
# 충돌을 피한다.
def _records_path(output_dir: Path, parent_id: str, lane: DetectorLane) -> Path:
    digest = sha256(parent_id.encode("utf-8")).hexdigest()[:16]
    return output_dir / "refined" / digest / lane.value / "records.json"


# 하나의 prompt group을 실행하는 데 필요한 모든 값(lane, 자산 경로,
# 프롬프트, 임계값, 모델 ID 등)을 모아 AdapterRequest를 만든다.
# run_refinement의 메인 루프에서 group마다 호출된다.
def _adapter_request(
    request: RefinementRunRequest,
    group: PromptVariantGroup,
    assets: JoinedRefinementAssets,
) -> AdapterRequest:
    lane = _detector_lane(group.model_lane)
    records_json = _records_path(
        request.output_dir, group.rag_parent_candidate_id, lane
    )
    detector_key = DETECTOR_MODEL_KEYS[lane]
    return AdapterRequest(
        schema_version=DETECTOR_ADAPTER_SCHEMA_VERSION,
        lane=lane,
        view=assets.view,
        image_width_px=assets.image_width_px,
        image_height_px=assets.image_height_px,
        lane_output_dir=records_json.parent,
        records_json=records_json,
        prompts=_prompt_records(group),
        threshold_config=seed_thresholds(lane),
        detector_model_id=EXPECTED_MODEL_REPO_IDS[detector_key],
        sam2_model_id=SAM2_MODEL_ID,
        object_mask_path=assets.object_mask_path,
    )


# RAG 근거가 없는 후보를 위한 자기-정제(self-refinement) 프롬프트. RAG가
# 만든 프롬프트 대신 rough_masking이 원래 이 후보를 찾을 때 썼던
# 프롬프트(candidate.prompt_provenance/executable_prompt)를 그대로 재사용해
# SAM2를 한 번 더 돌린다 - 새 의미 정보는 없지만, 1차 탐지기가 만든
# 느슨하거나 조각난 마스크를 더 타이트하고 깨끗한 하나의 마스크로 다시
# 잡아줄 수 있다. rough_target_anchor_only만 True로 두고
# final_conservation_vocabulary/anomaly_class_proof는 원래 시드 프롬프트와
# 동일하게 False로 둔다 - RAG가 검증한 보존과학 어휘가 아니라는 사실을
# 그대로 유지한다.
def _self_refinement_prompt_records(
    candidate: RawDetectorCandidate,
) -> tuple[PromptRecord, ...]:
    return (
        PromptRecord(
            metadata=candidate.prompt_provenance,
            prompt_text=candidate.executable_prompt,
            rough_target_anchor_only=True,
            final_conservation_vocabulary=False,
            anomaly_class_proof=False,
        ),
    )


# _adapter_request와 대응되는 자기-정제 버전. PromptVariantGroup 대신
# RawDetectorCandidate에서 바로 lane/프롬프트를 뽑는다는 점만 다르다.
def _self_refinement_adapter_request(
    request: RefinementRunRequest,
    candidate: RawDetectorCandidate,
    assets: JoinedRefinementAssets,
) -> AdapterRequest:
    lane = candidate.lane
    records_json = _records_path(request.output_dir, str(candidate.candidate_id), lane)
    detector_key = DETECTOR_MODEL_KEYS[lane]
    return AdapterRequest(
        schema_version=DETECTOR_ADAPTER_SCHEMA_VERSION,
        lane=lane,
        view=assets.view,
        image_width_px=assets.image_width_px,
        image_height_px=assets.image_height_px,
        lane_output_dir=records_json.parent,
        records_json=records_json,
        prompts=_self_refinement_prompt_records(candidate),
        threshold_config=seed_thresholds(lane),
        detector_model_id=EXPECTED_MODEL_REPO_IDS[detector_key],
        sam2_model_id=SAM2_MODEL_ID,
        object_mask_path=assets.object_mask_path,
    )


def load_model_entries(model_cache_root: Path) -> dict[str, ModelInventoryEntry]:
    """Load model inventory entries with shared local-path resolution."""
    inventory_path = model_cache_root / "inventory" / "model_inventory.json"
    try:
        decoded = parse_json_object(inventory_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        msg = "model_inventory"
        raise ContractValidationError(msg, "file missing") from error
    except ContractValidationError as error:
        msg = "model_inventory"
        raise ContractValidationError(msg, "malformed JSON") from error
    raw_models = decoded.get("models")
    if not isinstance(raw_models, list):
        msg = "model_inventory"
        raise ContractValidationError(msg, "models must be a list")
    entries: dict[str, ModelInventoryEntry] = {}
    for raw_entry in raw_models:
        if not isinstance(raw_entry, dict):
            msg = "model_inventory"
            raise ContractValidationError(msg, "model entry must be an object")
        key: JsonValue | None = raw_entry.get("key")
        repo_id: JsonValue | None = raw_entry.get("repo_id")
        revision: JsonValue | None = raw_entry.get("revision")
        local_dir: JsonValue | None = raw_entry.get("local_dir")
        if not (
            isinstance(key, str)
            and isinstance(repo_id, str)
            and isinstance(revision, str)
            and isinstance(local_dir, str)
            and all(value.strip() for value in (key, repo_id, revision, local_dir))
        ):
            msg = "model_inventory"
            raise ContractValidationError(msg, "model entry fields must be strings")
        entries[key] = ModelInventoryEntry(
            key,
            repo_id,
            revision,
            resolve_model_path(key, Path(local_dir), model_cache_root),
        )
    return entries


def default_runner_factory(details: RunnerFactoryInput) -> DetectorRunner:
    """Build the local-only runner after all lightweight input joining succeeds."""
    return build_local_model_runner(
        lane=details.lane,
        image_path=details.image_path,
        model_entries=load_model_entries(details.model_cache_root),
        device=details.device,
        model_cache_policy=LocalModelCachePolicy(
            details.model_cache_root, details.verify_model_hashes
        ),
    )


# rough_masking 단계가 승인한 후보 목록을 읽어온다. 순환 import를
# 피하기 위해 visual_cue_generation 모듈을 함수 내부에서 지역 import한다.
def _rough_qwen_candidates(rough_root: Path) -> tuple[RoughQwenCandidate, ...]:
    from modules.visual_cue_generation.rough_records import (  # noqa: PLC0415
        rough_qwen_candidates,
    )

    return rough_qwen_candidates(rough_root, rough_root)


def _prepare_output_dir(output_dir: Path) -> None:
    if output_dir.is_symlink():
        field = "output_dir"
        reason = "must not be a symlink"
        raise ContractValidationError(field, reason)
    output_dir.mkdir(parents=True, exist_ok=True)


# _write_jsonl에서 accepted_candidates 행마다 호출된다. candidate.mask.relative_path는
# _restore_original_mask가 이미 절대경로 문자열로 채워둔 값이라 그대로
# 내보내면 된다(records_json 필드와 같은 절대경로 문자열 관례를 맞춘 것).
def _absolute_mask_path(candidate: AcceptedRefinedCandidate) -> str | None:
    if candidate.mask is None:
        return None
    return candidate.mask.relative_path


# 실행/실패한 각 prompt group 결과를 refined_records.jsonl로 직렬화한다.
# run_refinement가 실행을 마친 뒤 최종 결과를 기록할 때 호출한다.
def _write_jsonl(path: Path, records: tuple[RefinedExecutionRecord, ...]) -> None:
    lines = tuple(
        json.dumps(
            {
                "rag_parent_candidate_id": record.rag_parent_candidate_id,
                "model_lane": record.model_lane.value,
                "detector_lane": record.detector_lane.value,
                "prompt_texts": [
                    prompt.prompt_text for prompt in record.prompt_records
                ],
                "records_json": str(record.records_json),
                "status": record.status.value,
                "diagnostics": list(record.diagnostics),
                "accepted_candidate_ids": list(record.accepted_candidate_ids),
                "accepted_candidates": [
                    {
                        "candidate_id": candidate.candidate_id,
                        "image_id": candidate.image_id,
                        "source_object_id": candidate.source_object_id,
                        "source_view_id": candidate.source_view_id,
                        "bbox_xyxy": list(candidate.bbox_xyxy),
                        "original_bbox_xyxy": list(candidate.original_bbox_xyxy),
                        "prompt": candidate.prompt,
                        "qwen_final_success": candidate.qwen_final_success,
                        "qwen_report_display_text": candidate.qwen_report_display_text,
                        "qwen_confidence": candidate.qwen_confidence,
                        "mask_path": _absolute_mask_path(candidate),
                        "mask_sha256": (
                            candidate.mask.sha256
                            if candidate.mask is not None
                            else None
                        ),
                    }
                    for candidate in record.accepted_candidates
                ],
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        for record in records
    )
    safe_path = ensure_no_symlink_leaf(
        path, "refinement artifact leaf is a symlink"
    )
    _ = safe_path.write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )


# 자산을 못 찾았거나 입력이 잘못돼 실행 자체를 시도하지 못한 group을
# skips.jsonl로 기록한다. 실패(FAILED)와 달리 아예 실행하지 못한
# 경우를 구분해서 남긴다.
def _write_skips(path: Path, skips: tuple[RefinementSkip, ...]) -> None:
    lines = tuple(
        json.dumps(
            {
                "stage": skip.stage.value,
                "reason": skip.reason,
                "line_number": skip.line_number,
                "rag_parent_candidate_id": skip.rag_parent_candidate_id,
                "model_lane": None
                if skip.model_lane is None
                else skip.model_lane.value,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        for skip in skips
    )
    safe_path = ensure_no_symlink_leaf(
        path, "refinement artifact leaf is a symlink"
    )
    _ = safe_path.write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )


# 이번 실행의 입력 경로와 실행/실패/스킵 개수를 manifest.json으로
# 요약해 남긴다.
def _write_manifest(request: RefinementRunRequest, result: RefinementRunResult) -> None:
    payload = {
        "schema": "mask_refinement_execution_v1",
        "inputs": {
            "prompt_output_dir": str(request.prompt_output_dir),
            "rough_root": str(request.rough_root),
            "asset_root": str(request.asset_root),
            "input_roles": {
                "prompt_output_dir": "prompt_generating output",
                "rough_root": "rough_masking output",
                "asset_root": "preprocessing assets",
            },
            "model_cache_root": str(request.model_cache_root),
            "device": request.device,
            "verify_model_hashes": request.verify_model_hashes,
            "max_groups": request.max_groups,
        },
        "counts": {
            "executed_groups": result.executed_groups,
            "failed_groups": sum(
                record.status is RefinementStatus.FAILED for record in result.records
            ),
            "skips": len(result.skips),
        },
    }
    manifest_path = ensure_no_symlink_leaf(
        request.output_dir / "manifest.json",
        "refinement artifact leaf is a symlink",
    )
    _ = manifest_path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8"
    )


# refine 단계에서 쓰인 뷰 좌표계의 bbox를 CoordinateTransform의
# offset/scale로 원본 이미지 좌표계로 되돌린다. coordinate_transform이
# 없으면 복원할 수 없으므로 예외를 던진다(치명적 오류로 취급).
def _restore_original_bbox(
    bbox_xyxy: tuple[float, float, float, float],
    coordinate_transform: CoordinateTransform | None,
    original_image_width_px: int | None,
    original_image_height_px: int | None,
) -> tuple[float, float, float, float]:
    if coordinate_transform is None:
        field = "coordinate_transform"
        reason = "required to restore original-image bbox coordinates"
        raise ContractValidationError(field, reason)
    offset_x = coordinate_transform.restore_offset_x
    offset_y = coordinate_transform.restore_offset_y
    scale_x = coordinate_transform.original_to_view_scale_x
    scale_y = coordinate_transform.original_to_view_scale_y
    left, top, right, bottom = bbox_xyxy
    restored = (
        left * scale_x + offset_x,
        top * scale_y + offset_y,
        right * scale_x + offset_x,
        bottom * scale_y + offset_y,
    )
    if original_image_width_px is not None and original_image_height_px is not None:
        _validate_bbox_in_bounds(
            restored, original_image_width_px, original_image_height_px
        )
    return restored


# 복원된 bbox가 원본 이미지 범위를 벗어나지 않는지 확인한다. 좌표
# 변환 정보가 잘못됐을 때 조용히 넘어가지 않고 바로 실패시킨다.
def _validate_bbox_in_bounds(
    bbox_xyxy: tuple[float, float, float, float], width_px: int, height_px: int
) -> None:
    left, top, right, bottom = bbox_xyxy
    if left < 0 or top < 0 or right > width_px or bottom > height_px:
        field = "original_bbox_xyxy"
        reason = "restored bbox must fall within the original image bounds"
        raise ContractValidationError(field, reason)


# _accepted_refined_candidates(정제된 후보)와 _passthrough_record(RAG 근거가
# 없어 정제를 아예 안 거친 후보) 양쪽에서 호출된다. candidate.rough_mask가
# 크롭 로컬(ROI) 좌표계에서 만들어진 마스크를 원본 이미지 좌표계로 되돌린다 -
# _restore_original_bbox와 정확히 같은 scale/offset 변환을 픽셀 배열에
# 적용한다(리사이즈 후 offset 위치에 붙여넣기). mask_root는
# candidate.rough_mask.relative_path가 상대적인 기준 디렉터리로, 정제된
# 후보는 이번 정제 실행의 lane_output_dir, 통과(passthrough) 후보는
# rough_masking의 rough_root다 - 서로 다른 루트에 상대적인 경로라 호출자가
# 명시적으로 넘겨야 한다. 반환하는 AssetReference.relative_path는 절대경로
# 문자열이다(records_json.parent / 절대경로는 pathlib에서 그 절대경로 그대로
# 남으므로 기존 호출부를 안 건드려도 된다). 원본 이미지 크기를 모르면(더
# 이전 산출물이나 지원 안 하는 이미지 포맷 등) 안전하게 복원할 수 없으므로
# None을 반환하고, anomaly_grouping이 명시적으로 실패하게 둔다.
def _restore_original_mask(
    candidate: RawDetectorCandidate,
    assets: JoinedRefinementAssets,
    mask_root: Path,
    output_dir: Path,
) -> AssetReference | None:
    if (
        assets.original_image_width_px is None
        or assets.original_image_height_px is None
    ):
        return None
    transform = assets.view.coordinate_transform
    if transform is None:
        return None
    # PIL/numpy stay out of this module's top-level imports so importing the
    # CLI without running a real refinement never pulls in image runtimes
    # (see test_cli_import_does_not_load_model_or_image_runtimes).
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    crop_mask_path = mask_root / candidate.rough_mask.relative_path
    with Image.open(crop_mask_path) as crop_image:
        crop_array = np.asarray(crop_image.convert("L")) > 0
    crop_height, crop_width = crop_array.shape
    target_width = max(1, round(crop_width * transform.original_to_view_scale_x))
    target_height = max(1, round(crop_height * transform.original_to_view_scale_y))
    crop_bytes = np.multiply(crop_array, 255).astype(np.uint8)
    resized = Image.fromarray(crop_bytes).resize(
        (target_width, target_height), Image.Resampling.NEAREST
    )
    canvas = Image.new(
        "L",
        (assets.original_image_width_px, assets.original_image_height_px),
        0,
    )
    canvas.paste(
        resized, (round(transform.restore_offset_x), round(transform.restore_offset_y))
    )
    output_path = output_dir / "restored_masks" / f"{candidate.candidate_id}.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)
    digest = sha256(output_path.read_bytes()).hexdigest()
    return AssetReference(str(output_path), digest, "image/png")


def _shared_asset_root(asset_root: Path) -> Path:
    # asset_root is the preprocessing run root (output/preprocessing/<run>);
    # Qwen evidence needs one root that also contains this stage's own
    # output tree (output/mask_refining/<run>/...), so every asset path can
    # be expressed without ".." traversal. output/ is that common ancestor.
    return asset_root.parent.parent


# input_manifest.json에서 image_id별 원본 자산 참조를 읽는다. 파일이
# 없거나 손상돼도 예외를 올리지 않고 빈 dict를 반환해(fail-open),
# 후속 Qwen 증거 단계가 소스가 없는 경우를 스스로 처리하게 둔다.
def _load_source_assets(
    asset_root: Path, shared_asset_root: Path
) -> dict[ImageId, AssetReference]:
    from modules.visual_cue_generation.source_manifest import (  # noqa: PLC0415
        source_assets_by_image,
    )

    manifest_path = asset_root / "manifests" / "input_manifest.json"
    try:
        return source_assets_by_image(manifest_path, shared_asset_root)
    except (ContractValidationError, OSError):
        return {}


# 현재 산출물 루트 기준 상대경로를 공유 asset root 기준 상대경로로
# 다시 계산한다. 결과 경로가 shared_asset_root를 벗어나면 예외를
# 던져 경로 이탈을 막는다.
def _rebased_asset(
    asset: AssetReference, current_root: Path, shared_asset_root: Path
) -> AssetReference:
    resolved_root = shared_asset_root.resolve()
    resolved_path = (current_root / asset.relative_path).resolve()
    if not resolved_path.is_relative_to(resolved_root):
        field = "asset_path"
        reason = "must stay inside the shared asset root"
        raise ContractValidationError(field, reason)
    return AssetReference(
        resolved_path.relative_to(resolved_root).as_posix(),
        asset.sha256,
        asset.media_type,
    )


# 후보의 rough_mask/overlay 자산 경로를 _rebased_asset으로 공유 root
# 기준으로 재계산한다. Qwen 증거 조회 전에 호출된다.
def _rebased_candidate(
    candidate: RawDetectorCandidate, lane_output_dir: Path, shared_asset_root: Path
) -> RawDetectorCandidate:
    from dataclasses import replace  # noqa: PLC0415

    return replace(
        candidate,
        rough_mask=_rebased_asset(
            candidate.rough_mask, lane_output_dir, shared_asset_root
        ),
        overlay=_rebased_asset(candidate.overlay, lane_output_dir, shared_asset_root),
    )


def default_post_refinement_qwen_evidence_factory(
    asset_root: Path, model_cache_root: Path, device: str
) -> PostRefinementQwenEvidenceFactory:
    """Build a factory that loads the Qwen renderer/backend once and reuses them.

    Imports are local: this module (and the CLI that uses it) must stay
    importable without pulling in PIL/torch/transformers until a real
    refinement run actually needs post-refinement Qwen evidence.
    """
    from modules.mask_refining import (  # noqa: PLC0415
        PillowQwenViewRenderer,
        QwenRefinementRequest,
        load_transformers_qwen_backend,
        refine_candidate,
    )
    from modules.mask_refining.contracts.models import (  # noqa: PLC0415
        DEFAULT_SOURCE_HEIGHT_PX,
        DEFAULT_SOURCE_WIDTH_PX,
    )
    from modules.visual_cue_generation.startup_runner import (  # noqa: PLC0415
        qwen_model_directory,
    )

    shared_asset_root = _shared_asset_root(asset_root)
    renderer = PillowQwenViewRenderer(shared_asset_root)
    backend = load_transformers_qwen_backend(
        qwen_model_directory(model_cache_root), device
    )

    # 후보 하나에 대한 후속 Qwen 증거를 만든다. source 자산이 없거나
    # 경로 재계산이 실패하면 실패로 처리하지 않고 "없음" 표시의
    # 실패 증거를 반환한다(never raises 계약).
    def factory(
        candidate: RawDetectorCandidate,
        source_asset: AssetReference | None,
        assets: JoinedRefinementAssets,
        lane_output_dir: Path,
    ) -> PostRefinementQwenEvidence:
        if source_asset is None:
            return PostRefinementQwenEvidence(
                final_success=False, report_display_text="없음", confidence=None
            )
        try:
            rebased_candidate = _rebased_candidate(
                candidate, lane_output_dir, shared_asset_root
            )
        except ContractValidationError:
            return PostRefinementQwenEvidence(
                final_success=False, report_display_text="없음", confidence=None
            )
        request = QwenRefinementRequest(
            rebased_candidate,
            source_asset,
            shared_asset_root,
            device,
            assets.original_image_width_px or DEFAULT_SOURCE_WIDTH_PX,
            assets.original_image_height_px or DEFAULT_SOURCE_HEIGHT_PX,
        )
        result = refine_candidate(request, renderer, backend)
        return PostRefinementQwenEvidence(
            result.final_success, result.report_display_text, result.confidence
        )

    return factory


class _LazyRefinementDependencies:
    """Builds the Qwen evidence factory and source-asset map at most once."""

    def __init__(
        self,
        request: RefinementRunRequest,
        qwen_evidence_factory: PostRefinementQwenEvidenceFactory | None,
    ) -> None:
        self._request: RefinementRunRequest = request
        self._qwen_evidence_factory: PostRefinementQwenEvidenceFactory | None = (
            qwen_evidence_factory
        )
        self._resolved_qwen_evidence_factory: (
            PostRefinementQwenEvidenceFactory | None
        ) = None
        self._resolved_source_assets: dict[ImageId, AssetReference] | None = None

    def qwen_evidence_factory(self) -> PostRefinementQwenEvidenceFactory:
        if self._qwen_evidence_factory is not None:
            return self._qwen_evidence_factory
        if self._resolved_qwen_evidence_factory is None:
            self._resolved_qwen_evidence_factory = (
                default_post_refinement_qwen_evidence_factory(
                    self._request.asset_root,
                    self._request.model_cache_root,
                    self._request.device,
                )
            )
        return self._resolved_qwen_evidence_factory

    def source_assets(self) -> dict[ImageId, AssetReference]:
        if self._resolved_source_assets is None:
            self._resolved_source_assets = _load_source_assets(
                self._request.asset_root,
                _shared_asset_root(self._request.asset_root),
            )
        return self._resolved_source_assets


# run_refinement이 메인 루프를 다 돈 뒤, RAG 정제 프롬프트가 한 번도 생성되지
# 않은(= 이 후보에 문헌 근거 카드가 하나도 없었던) rough 후보마다 호출된다.
# SAM2 재정제는 하지 않고 rough 마스크/bbox를 원본 좌표로 복원만 해서 그대로
# 통과시킨다 - RAG 근거가 없어도 리포트에는 실려야 한다는 결정. Qwen도
# 정제 후 단계라 실행하지 않고 명시적으로 "없음"으로 남긴다. 반환하는 첫
# 값은 refined_records.jsonl과 같은 모양의 행(anomaly_grouping이 그대로 재사용
# 할 수 있게)이고, 자산을 못 찾으면 대신 skip 사유를 반환한다.
def _passthrough_record(
    candidate: RawDetectorCandidate,
    request: RefinementRunRequest,
) -> tuple[JsonValue | None, RefinementSkip | None]:
    if candidate.source_object_id is None:
        return None, RefinementSkip(
            SkipStage.ASSET_JOIN,
            "rough_candidate_missing_object_id",
            rag_parent_candidate_id=str(candidate.candidate_id),
            model_lane=None,
        )
    assets = join_preprocessing_assets(
        request.asset_root, candidate.source_object_id, str(candidate.image_id)
    )
    if assets is None:
        return None, RefinementSkip(
            SkipStage.ASSET_JOIN,
            "preprocessing_assets_missing",
            rag_parent_candidate_id=str(candidate.candidate_id),
            model_lane=None,
        )
    try:
        original_bbox = _restore_original_bbox(
            candidate.bbox_xyxy,
            assets.view.coordinate_transform,
            assets.original_image_width_px,
            assets.original_image_height_px,
        )
        mask = _restore_original_mask(
            candidate, assets, request.rough_root, request.output_dir / "passthrough"
        )
    except (ContractValidationError, OSError) as error:
        return None, RefinementSkip(
            SkipStage.EXECUTION,
            f"passthrough_coordinate_restoration_failed: {error}",
            rag_parent_candidate_id=str(candidate.candidate_id),
            model_lane=None,
        )
    row: JsonValue = {
        "rag_parent_candidate_id": str(candidate.candidate_id),
        "detector_lane": candidate.lane.value,
        "diagnostics": ["no_rag_evidence_passthrough"],
        "accepted_candidate_ids": [str(candidate.candidate_id)],
        "accepted_candidates": [
            {
                "candidate_id": str(candidate.candidate_id),
                "image_id": str(candidate.image_id),
                "source_object_id": candidate.source_object_id,
                "source_view_id": candidate.source_view_id,
                "source_tile_view_id": candidate.source_tile_view_id,
                "bbox_xyxy": list(candidate.bbox_xyxy),
                "original_bbox_xyxy": list(original_bbox),
                "prompt": candidate.executable_prompt,
                "qwen_final_success": False,
                "qwen_report_display_text": "없음",
                "qwen_confidence": None,
                "mask_path": mask.relative_path if mask is not None else None,
                "mask_sha256": mask.sha256 if mask is not None else None,
            }
        ],
    }
    return row, None


# _passthrough_record 결과들을 refined_records.jsonl과 나란히 두는 별도
# 사이드카(passthrough_records.jsonl)로 기록한다. run_refinement이 실행을
# 마친 뒤 호출한다.
def _write_passthrough_jsonl(path: Path, rows: tuple[JsonValue, ...]) -> None:
    lines = tuple(
        json.dumps(row, ensure_ascii=True, sort_keys=True) for row in rows
    )
    safe_path = ensure_no_symlink_leaf(
        path, "refinement artifact leaf is a symlink"
    )
    _ = safe_path.write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )


# 어댑터가 받아들인 각 후보에 대해 bbox를 원본 좌표로 복원하고
# 후속 Qwen 증거를 붙여 AcceptedRefinedCandidate 목록을 만든다.
# run_refinement가 group마다 호출한다.
def _accepted_refined_candidates(
    candidates: tuple[RawDetectorCandidate, ...],
    assets: JoinedRefinementAssets,
    lazy_dependencies: _LazyRefinementDependencies,
    lane_output_dir: Path,
    *,
    rough_source_tile_view_id: str | None,
) -> tuple[AcceptedRefinedCandidate, ...]:
    # rough_source_tile_view_id는 이 정제를 촉발한 원본 rough_masking 후보의
    # tile_view_id다 - 정제된 candidate 자신의 source_view_id는 정제 실행
    # 자체의 내부 ROI 뷰를 가리키므로 타일 출처와는 다른 값이다.
    source_assets = lazy_dependencies.source_assets()
    qwen_evidence_factory = lazy_dependencies.qwen_evidence_factory()
    records: list[AcceptedRefinedCandidate] = []
    for candidate in candidates:
        if candidate.source_object_id is None:
            field = "source_object_id"
            reason = "accepted candidate handoff required"
            raise ContractValidationError(field, reason)
        evidence = qwen_evidence_factory(
            candidate, source_assets.get(candidate.image_id), assets, lane_output_dir
        )
        records.append(
            AcceptedRefinedCandidate(
                str(candidate.candidate_id),
                str(candidate.image_id),
                candidate.source_object_id,
                candidate.source_view_id,
                candidate.bbox_xyxy,
                _restore_original_bbox(
                    candidate.bbox_xyxy,
                    assets.view.coordinate_transform,
                    assets.original_image_width_px,
                    assets.original_image_height_px,
                ),
                candidate.executable_prompt,
                evidence.final_success,
                evidence.report_display_text,
                evidence.confidence,
                _restore_original_mask(
                    candidate, assets, lane_output_dir, lane_output_dir
                ),
                rough_source_tile_view_id,
            )
        )
    return tuple(records)


# RAG 근거가 없는 후보에 한 번 더 SAM2 정제를 시도한다. 성공하면(적어도
# 하나는 accepted) 실제로 정제된 RefinedExecutionRecord를 돌려준다.
# 자산을 못 찾거나, 어댑터 실행 자체가 실패하거나, 이번엔 아무 것도
# accept되지 않으면 None을 반환해 호출자가 _passthrough_record(원본 러프
# 마스크를 좌표만 복원)로 폴백하게 한다 - 이 폴백이 있어야 자기-정제
# 실패가 후보를 리포트에서 통째로 사라지게 만들지 않는다(같은 세션에서
# passthrough 자체를 도입한 이유와 동일).
def _try_self_refine(
    candidate: RawDetectorCandidate,
    request: RefinementRunRequest,
    runner_factory: RefinementRunnerFactory,
    lazy_dependencies: _LazyRefinementDependencies,
) -> RefinedExecutionRecord | None:
    if candidate.source_object_id is None:
        return None
    assets = join_preprocessing_assets(
        request.asset_root, candidate.source_object_id, str(candidate.image_id)
    )
    if assets is None:
        return None
    adapter_request = _self_refinement_adapter_request(request, candidate, assets)
    try:
        runner = runner_factory(
            RunnerFactoryInput(
                adapter_request.lane,
                assets.roi_image_path,
                request.model_cache_root,
                request.device,
                request.verify_model_hashes,
            )
        )
        receipt = execute_adapter(adapter_request, runner)
        accepted_candidates = _accepted_refined_candidates(
            receipt.candidates,
            assets,
            lazy_dependencies,
            adapter_request.lane_output_dir,
            rough_source_tile_view_id=candidate.source_tile_view_id,
        )
    except (
        ContractValidationError,
        ImportError,
        OSError,
        RuntimeError,
        ValueError,
    ):
        return None
    if not accepted_candidates:
        return None
    return RefinedExecutionRecord(
        str(candidate.candidate_id),
        detector_to_rag_lane(candidate.lane),
        adapter_request.lane,
        adapter_request.prompts,
        adapter_request.records_json,
        RefinementStatus.EXECUTED,
        receipt.diagnostics,
        tuple(str(item.candidate_id) for item in receipt.candidates),
        accepted_candidates,
    )


# run_refinement의 두 번째 루프(RAG 근거 없는 후보) 본문을 분리한 헬퍼 -
# run_refinement 자체의 순환 복잡도를 낮추기 위한 것뿐, 판단 로직은 그대로
# 옮겨온 것이다. within_budget이 False면 자기-정제를 시도조차 하지 않고
# 바로 passthrough로 간다(예산 소진 후에는 추가 SAM2 호출을 하지 않는다).
def _self_refine_or_passthrough(
    candidate: RawDetectorCandidate,
    request: RefinementRunRequest,
    runner_factory: RefinementRunnerFactory,
    lazy_dependencies: _LazyRefinementDependencies,
    *,
    within_budget: bool,
) -> tuple[RefinedExecutionRecord | None, JsonValue | None, RefinementSkip | None]:
    if within_budget:
        refined_record = _try_self_refine(
            candidate, request, runner_factory, lazy_dependencies
        )
        if refined_record is not None:
            return refined_record, None, None
    row, skip = _passthrough_record(candidate, request)
    return None, row, skip


# run_refinement의 첫 번째 루프(RAG 근거 있는 그룹) 본문을 분리한 헬퍼 -
# run_refinement 자체의 순환 복잡도를 낮추기 위한 것뿐, 판단 로직은
# 그대로 옮겨온 것이다. 정확히 record/skip 중 하나만 채워서 반환한다.
def _execute_prompt_group(
    request: RefinementRunRequest,
    group: PromptVariantGroup,
    candidates: dict[str, RawDetectorCandidate],
    runner_factory: RefinementRunnerFactory,
    lazy_dependencies: _LazyRefinementDependencies,
) -> tuple[RefinedExecutionRecord | None, RefinementSkip | None]:
    candidate = candidates.get(group.rag_parent_candidate_id)
    if candidate is None or candidate.source_object_id is None:
        return None, RefinementSkip(
            SkipStage.ASSET_JOIN,
            "rough_candidate_missing",
            rag_parent_candidate_id=group.rag_parent_candidate_id,
            model_lane=group.model_lane,
        )
    assets = join_preprocessing_assets(
        request.asset_root, candidate.source_object_id, str(candidate.image_id)
    )
    if assets is None:
        return None, RefinementSkip(
            SkipStage.ASSET_JOIN,
            "preprocessing_assets_missing",
            rag_parent_candidate_id=group.rag_parent_candidate_id,
            model_lane=group.model_lane,
        )
    adapter_request = _adapter_request(request, group, assets)
    try:
        runner = runner_factory(
            RunnerFactoryInput(
                adapter_request.lane,
                assets.roi_image_path,
                request.model_cache_root,
                request.device,
                request.verify_model_hashes,
            )
        )
        receipt = execute_adapter(adapter_request, runner)
        accepted_candidates = _accepted_refined_candidates(
            receipt.candidates,
            assets,
            lazy_dependencies,
            adapter_request.lane_output_dir,
            rough_source_tile_view_id=candidate.source_tile_view_id,
        )
    except (
        ContractValidationError,
        ImportError,
        OSError,
        RuntimeError,
        ValueError,
    ) as error:
        return RefinedExecutionRecord(
            group.rag_parent_candidate_id,
            group.model_lane,
            adapter_request.lane,
            adapter_request.prompts,
            adapter_request.records_json,
            RefinementStatus.FAILED,
            (str(error),),
            (),
            (),
        ), None
    return RefinedExecutionRecord(
        group.rag_parent_candidate_id,
        group.model_lane,
        adapter_request.lane,
        adapter_request.prompts,
        adapter_request.records_json,
        RefinementStatus.EXECUTED,
        receipt.diagnostics,
        tuple(str(item.candidate_id) for item in receipt.candidates),
        accepted_candidates,
    ), None


class _NoEvidenceState(NamedTuple):
    """Budget carried from the first loop plus this loop's progress bounds."""

    processed_groups: int
    progress_offset: int
    progress_total: int


# run_refinement의 두 번째 루프(RAG 근거 없는 후보 전체) 자체를 분리한
# 헬퍼 - for 루프 하나를 통째로 옮겨 run_refinement의 순환 복잡도를
# 낮춘다. processed_groups는 첫 번째 루프에서 이어받아 여기서도 계속
# 소모하고, 최종 값을 그대로 돌려준다(호출자는 반환값만 쓰면 된다).
def _process_no_evidence_candidates(
    request: RefinementRunRequest,
    no_evidence_candidates: tuple[RawDetectorCandidate, ...],
    runner_factory: RefinementRunnerFactory,
    lazy_dependencies: _LazyRefinementDependencies,
    state: _NoEvidenceState,
) -> tuple[list[RefinedExecutionRecord], list[JsonValue], list[RefinementSkip], int]:
    records: list[RefinedExecutionRecord] = []
    passthrough_rows: list[JsonValue] = []
    skips: list[RefinementSkip] = []
    max_groups = request.max_groups
    processed_groups = state.processed_groups
    for candidate_index, candidate in enumerate(no_evidence_candidates):
        within_budget = max_groups is None or processed_groups < max_groups
        if within_budget:
            processed_groups += 1
        refined_record, row, skip = _self_refine_or_passthrough(
            candidate,
            request,
            runner_factory,
            lazy_dependencies,
            within_budget=within_budget,
        )
        if refined_record is not None:
            records.append(refined_record)
        elif skip is not None:
            skips.append(skip)
        elif row is not None:
            passthrough_rows.append(row)
        if request.progress_root is not None:
            update_stage_progress_count(
                request.progress_root,
                state.progress_offset + candidate_index + 1,
                state.progress_total,
            )
    return records, passthrough_rows, skips, processed_groups


def run_refinement(
    request: RefinementRunRequest,
    runner_factory: RefinementRunnerFactory = default_runner_factory,
    qwen_evidence_factory: PostRefinementQwenEvidenceFactory | None = None,
) -> RefinementRunResult:
    """Execute joinable prompt groups and record failures without aborting peers."""
    prompt_result = read_prompt_variants(request.prompt_output_dir)
    candidates = {
        str(item.candidate.candidate_id): item.candidate
        for item in _rough_qwen_candidates(request.rough_root)
    }
    lazy_dependencies = _LazyRefinementDependencies(request, qwen_evidence_factory)
    records: list[RefinedExecutionRecord] = []
    skips = list(prompt_result.skips)
    _prepare_output_dir(request.output_dir)
    processed_groups = 0
    # 후보가 "패스스루 대상"인지 판정하는 기준은 실제로 RAG 근거 프롬프트
    # 그룹이 존재하는지 여부이지, max_groups로 이번 실행에서 처리됐는지가
    # 아니다 - covered_candidate_ids만 쓰면 max_groups로 잘려나간(하지만
    # 실제로는 RAG 근거가 있는) 후보까지 "근거 없음" 패스스루로 잘못
    # 분류된다.
    candidates_with_prompt_groups: frozenset[str] = frozenset(
        group.rag_parent_candidate_id for group in prompt_result.groups
    )
    no_evidence_candidates = tuple(
        candidate
        for candidate_id, candidate in candidates.items()
        if candidate_id not in candidates_with_prompt_groups
    )
    progress_total = len(prompt_result.groups) + len(no_evidence_candidates)
    for group_index, group in enumerate(prompt_result.groups):
        if request.max_groups is not None and processed_groups >= request.max_groups:
            break
        processed_groups += 1
        record, skip = _execute_prompt_group(
            request, group, candidates, runner_factory, lazy_dependencies
        )
        if record is not None:
            records.append(record)
        if skip is not None:
            skips.append(skip)
        if request.progress_root is not None and progress_total > 0:
            update_stage_progress_count(
                request.progress_root, group_index + 1, progress_total
            )
    # RAG 근거가 없는 후보도 예산이 남아있으면 자기 프롬프트로 한 번 더
    # SAM2 정제를 시도한다 - 1차 탐지기가 만든 느슨하거나 조각난 마스크를
    # 타이트한 하나의 마스크로 다시 잡을 기회를 준다(라벨은 여전히 없지만,
    # 정밀도만은 근거 있는 후보와 같은 수준으로 맞춘다). 성공한 시도도
    # processed_groups를 소모해, RAG 그룹과 같은 --max-groups 예산을
    # 공유한다.
    no_evidence_records, passthrough_rows, no_evidence_skips, processed_groups = (
        _process_no_evidence_candidates(
            request,
            no_evidence_candidates,
            runner_factory,
            lazy_dependencies,
            _NoEvidenceState(
                processed_groups, len(prompt_result.groups), progress_total
            ),
        )
    )
    records.extend(no_evidence_records)
    skips.extend(no_evidence_skips)
    _write_passthrough_jsonl(
        request.output_dir / "passthrough_records.jsonl", tuple(passthrough_rows)
    )
    result = RefinementRunResult(tuple(records), tuple(skips))
    _write_jsonl(request.output_dir / "refined_records.jsonl", result.records)
    _write_skips(request.output_dir / "skips.jsonl", result.skips)
    _write_manifest(request, result)
    return result
