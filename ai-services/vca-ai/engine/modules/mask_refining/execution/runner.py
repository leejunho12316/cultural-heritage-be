"""Prompt-grouped local refinement orchestration using rough-masking seams."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from modules.mask_refining.execution.assets import join_preprocessing_assets
from modules.mask_refining.execution.models import (
    AcceptedRefinedCandidate,
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
    ensure_no_symlink_leaf,
    resolve_model_path,
)

if TYPE_CHECKING:
    from modules.mask_refining.execution.models import (
        JoinedRefinementAssets,
        PromptVariantGroup,
    )
    from modules.rough_masking import RawDetectorCandidate
    from modules.visual_cue_generation.rough_records import RoughQwenCandidate


def _detector_lane(lane: RagLane) -> DetectorLane:
    match lane:
        case RagLane.OWLV2:
            return DetectorLane.OWLV2_SAM2
        case RagLane.FLORENCE2:
            return DetectorLane.FLORENCE2_SAM2
        case RagLane.GROUNDINGDINO:
            return DetectorLane.GROUNDED_SAM2


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


def _records_path(output_dir: Path, parent_id: str, lane: DetectorLane) -> Path:
    digest = sha256(parent_id.encode("utf-8")).hexdigest()[:16]
    return output_dir / "refined" / digest / lane.value / "records.json"


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
                        "prompt": candidate.prompt,
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


def _accepted_refined_candidates(
    candidates: tuple[RawDetectorCandidate, ...],
) -> tuple[AcceptedRefinedCandidate, ...]:
    records: list[AcceptedRefinedCandidate] = []
    for candidate in candidates:
        if candidate.source_object_id is None:
            field = "source_object_id"
            reason = "accepted candidate handoff required"
            raise ContractValidationError(field, reason)
        records.append(
            AcceptedRefinedCandidate(
                str(candidate.candidate_id),
                str(candidate.image_id),
                candidate.source_object_id,
                candidate.source_view_id,
                candidate.bbox_xyxy,
                candidate.executable_prompt,
            )
        )
    return tuple(records)


def run_refinement(
    request: RefinementRunRequest,
    runner_factory: RefinementRunnerFactory = default_runner_factory,
) -> RefinementRunResult:
    """Execute joinable prompt groups and record failures without aborting peers."""
    prompt_result = read_prompt_variants(request.prompt_output_dir)
    candidates = {
        str(item.candidate.candidate_id): item.candidate
        for item in _rough_qwen_candidates(request.rough_root)
    }
    records: list[RefinedExecutionRecord] = []
    skips = list(prompt_result.skips)
    _prepare_output_dir(request.output_dir)
    processed_groups = 0
    for group in prompt_result.groups:
        if request.max_groups is not None and processed_groups >= request.max_groups:
            break
        processed_groups += 1
        candidate = candidates.get(group.rag_parent_candidate_id)
        if candidate is None or candidate.source_object_id is None:
            skips.append(
                RefinementSkip(
                    SkipStage.ASSET_JOIN,
                    "rough_candidate_missing",
                    rag_parent_candidate_id=group.rag_parent_candidate_id,
                    model_lane=group.model_lane,
                )
            )
            continue
        assets = join_preprocessing_assets(
            request.asset_root, candidate.source_object_id, str(candidate.image_id)
        )
        if assets is None:
            skips.append(
                RefinementSkip(
                    SkipStage.ASSET_JOIN,
                    "preprocessing_assets_missing",
                    rag_parent_candidate_id=group.rag_parent_candidate_id,
                    model_lane=group.model_lane,
                )
            )
            continue
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
            accepted_candidates = _accepted_refined_candidates(receipt.candidates)
        except (
            ContractValidationError,
            ImportError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            records.append(
                RefinedExecutionRecord(
                    group.rag_parent_candidate_id,
                    group.model_lane,
                    adapter_request.lane,
                    adapter_request.prompts,
                    adapter_request.records_json,
                    RefinementStatus.FAILED,
                    (str(error),),
                    (),
                    (),
                )
            )
            continue
        records.append(
            RefinedExecutionRecord(
                group.rag_parent_candidate_id,
                group.model_lane,
                adapter_request.lane,
                adapter_request.prompts,
                adapter_request.records_json,
                RefinementStatus.EXECUTED,
                receipt.diagnostics,
                tuple(str(candidate.candidate_id) for candidate in receipt.candidates),
                accepted_candidates,
            )
        )
    result = RefinementRunResult(tuple(records), tuple(skips))
    _write_jsonl(request.output_dir / "refined_records.jsonl", result.records)
    _write_skips(request.output_dir / "skips.jsonl", result.skips)
    _write_manifest(request, result)
    return result
