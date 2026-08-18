"""Project-level startup adapter for anomaly grouping artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from modules.anomaly_grouping.io import write_result
from modules.anomaly_grouping.models import (
    AnomalyCandidate,
    AnomalyGroupingRequest,
    BoundingBox,
    CandidateEvidence,
    MaskReference,
)
from modules.anomaly_grouping.pipeline import run_anomaly_grouping
from modules.anomaly_grouping.startup_json import (
    bool_value,
    float_value,
    json_object,
    numbers,
    optional_string,
    raise_contract,
    read_jsonl_objects,
    string,
    strings,
    unique_strings,
)
from modules.anomaly_grouping.startup_trace_source import (
    StartupCandidate,
    startup_trace_source_payload,
)
from modules.report_generating.io import parse_trace_source, write_json
from modules.shared import (
    CandidateId,
    ContractValidationError,
    ExitCode,
    PathSafetyError,
    ensure_no_symlink_leaf,
)
from modules.shared.mask_pixels import load_mask_array

if TYPE_CHECKING:
    from modules.orchestration.stage_paths import StagePathMap
    from modules.report_generating.models import JsonObject

_BBOX_COORDINATE_COUNT: Final = 4
# rough_masking의 타일 단위 탐지는 오브젝트의 사각형 bbox 크롭 위에서
# 돌기 때문에(오브젝트 실루엣 마스크가 아니라), bbox 모서리의 배경 영역에서
# 잘못 잡힌 탐지가 후보로 승인될 수 있다 - 실측(2026-08-10): 한 실행에서
# kept 후보 8개 중 4개가 마스크 픽셀의 91~100%가 오브젝트 실루엣 바깥이었고
# 전부 같은 bbox 모서리에 몰려 있었다. 여기서 최종 안전장치로 후보 자신의
# 마스크와 오브젝트 실루엣 마스크(둘 다 "마스크 우선" 설계상 원본 이미지
# 좌표계여야 함)의 픽셀 겹침을 다시 확인해 절반도 안 겹치면 버린다.
_MIN_OBJECT_MASK_OVERLAP_RATIO: Final = 0.5


class _ProjectStageRequest(Protocol):
    @property
    def paths(self) -> StagePathMap: ...

    @property
    def dry_run(self) -> bool: ...


# 오케스트레이션이 (새 위치의) anomaly_grouping 스테이지를 실행할 때 호출하는
# 프로젝트 어댑터 진입점 - rag 직후, prompt_generating 이전. 물리적으로 같은
# 특이점인 rough_masking 후보들을 병합해 rough_masking/rag 산출물을 그
# 자리에서 다시 쓴다(pre_refinement_merge.py). PIL/numpy을 이 함수 안에서만
# 지역 import하는 이유는 stage_runner_contracts.py를 그냥 import만 해도
# 무거운 비전 런타임이 따라 로드되면 안 되기 때문이다
# (test_stage_runner_contract_imports.py가 강제).
def run_pre_refinement_grouping_stage(request: _ProjectStageRequest) -> int:
    """Merge same-physical-anomaly rough candidates before mask_refining runs."""
    if request.dry_run:
        return int(ExitCode.OK)
    from modules.anomaly_grouping.pre_refinement_merge import (  # noqa: PLC0415
        merge_before_refinement,
    )

    try:
        paths = request.paths
        merge_before_refinement(
            paths.rough_masking,
            paths.preprocessing.parent.parent,
            paths.preprocessing / "manifests" / "input_manifest.json",
            paths.rag / "rag_visual_concept_cards.jsonl",
            paths.rag / "qwen_bridge_results",
        )
    except (ContractValidationError, OSError) as error:
        print(  # noqa: T201
            f"anomaly_grouping: failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return int(ExitCode.INCOMPLETE_OR_FAILURE)
    return int(ExitCode.OK)


# 오케스트레이션이 report_trace_assembly 스테이지를 실행할 때 호출하는 프로젝트
# 어댑터 진입점 - mask_refining 직후, report_generating 이전. 예전에는 이
# 함수 이름 그대로("anomaly_grouping") 병합까지 담당했지만, 병합은 이제
# 위의 run_pre_refinement_grouping_stage로 옮겨갔다 - mask_refining 출력
# 시점엔 이미 병합이 끝나 있으므로, 여기서는 report_generating이 기대하는
# report_trace_source.json 조립만 한다(mask_refining/rag 산출물을 읽어서).
# 자기 전용 출력 디렉터리 없이 report_generating의 디렉터리에 함께 쓴다 -
# report_generating이 유일한 소비자라서다.
def run_report_trace_assembly_stage(request: _ProjectStageRequest) -> int:
    """Assemble report_trace_source.json from finalized mask-refining output."""
    if request.dry_run:
        return int(ExitCode.OK)
    try:
        stage_candidates, citation_details = _read_startup_inputs(request)
        output_root = request.paths.report_generating
        result = run_anomaly_grouping(
            AnomalyGroupingRequest(
                tuple(item.candidate for item in stage_candidates),
                output_root / "masks",
            )
        )
        trace_source = startup_trace_source_payload(
            stage_candidates, result, citation_details
        )
        _ = parse_trace_source(trace_source)
        result_path = ensure_no_symlink_leaf(
            output_root / "anomaly_grouping_result.json",
            "anomaly grouping startup artifact leaf is a symlink",
        )
        trace_path = ensure_no_symlink_leaf(
            output_root / "report_trace_source.json",
            "anomaly grouping startup artifact leaf is a symlink",
        )
        write_result(result_path, result)
        write_json(trace_path, trace_source)
    except (
        ContractValidationError,
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
        PathSafetyError,
    ) as error:
        print(  # noqa: T201
            f"report_trace_assembly: failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return int(ExitCode.INCOMPLETE_OR_FAILURE)
    return int(ExitCode.OK)


# run_anomaly_grouping_stage에서 호출. mask_refining/rag 단계의 JSONL 사이드카를
# 모두 읽어 AnomalyGroupingRequest를 구성하는 데 필요한 두 조각(후보, 인용
# 상세)으로 정리한다.
def _read_startup_inputs(
    request: _ProjectStageRequest,
) -> tuple[tuple[StartupCandidate, ...], dict[str, JsonObject]]:
    mask_rows = read_jsonl_objects(
        request.paths.mask_refining / "refined_records.jsonl"
    )
    # RAG 근거가 하나도 없어 정제를 거치지 못한 후보들이 여기 실린다(mask_refining
    # 의 통과(passthrough) 사이드카) - 없어도 실패시키지 않고 빈 튜플로 폴백한다,
    # 이 사이드카가 없던 예전 mask_refining 산출물을 다시 돌릴 수도 있어서다.
    passthrough_rows = _optional_jsonl_objects(
        request.paths.mask_refining / "passthrough_records.jsonl"
    )
    retrieval_rows = read_jsonl_objects(
        request.paths.rag / "prompt_rag_results.jsonl"
    )
    cards = _cards_by_candidate(
        read_jsonl_objects(request.paths.rag / "rag_visual_concept_cards.jsonl")
    )
    candidates = _discard_off_object_candidates(
        _stage_candidates((*mask_rows, *passthrough_rows), cards),
        request.paths.preprocessing,
    )
    if not candidates:
        raise_contract("candidates", "must not be empty")
    return (
        candidates,
        _citation_details_by_id(retrieval_rows),
    )


def _optional_jsonl_objects(path: Path) -> tuple[JsonObject, ...]:
    try:
        return read_jsonl_objects(path)
    except FileNotFoundError:
        return ()


# _read_startup_inputs에서 호출된다. 같은 citation_id에 여러 검색 결과가 있을
# 때 startup_trace_source.py가 인용을 내보낼지 판단할 근거로 최고 점수 항목만
# 남긴다.
def _citation_details_by_id(
    retrieval_rows: tuple[JsonObject, ...],
) -> dict[str, JsonObject]:
    """Return the highest-scoring retrieval detail per citation_id.

    Citations start non-exportable unless a matching retrieval row supplies
    a real page number; missing/unresolved ids simply stay absent here.
    """
    details: dict[str, JsonObject] = {}
    for row in retrieval_rows:
        citation_id = string(row, "citation_id")
        current_best = details.get(citation_id)
        if current_best is None or float_value(row, "score") > float_value(
            current_best, "score"
        ):
            details[citation_id] = row
    return details


# _read_startup_inputs에서 호출된다. mask_refining이 승인한(accepted) 후보
# 레코드들을 순회하며 AnomalyCandidate로 변환하고, rag_parent_candidate_id로
# 매칭되는 concept card 증거를 붙인다.
#
# 같은 실제 손상이 서로 다른 rag_parent 계보(예: 오브젝트 크롭 탐지 하나와
# 타일 병합 탐지 하나)로 두 번 정제될 수 있다 - 같은 prompt/lane으로 같은
# 영역을 정제하면 SAM2가 byte-identical한 bbox/mask로 수렴하고,
# normalize_candidate는 그걸 그대로 같은 candidate_id로 해시한다. 이 충돌은
# 조작이 아니라 진짜 신호이므로(같은 후보가 두 번 보고됨), report_generating의
# no-fake-claim 감사에 걸리기 전에 여기서 하나로 합친다 - 두 계보가 각자
# 모은 concept card 증거는 버리지 않고 합친다.
def _stage_candidates(
    mask_rows: tuple[JsonObject, ...],
    cards: dict[str, tuple[JsonObject, ...]],
) -> tuple[StartupCandidate, ...]:
    records_by_id: dict[str, tuple[JsonObject, str]] = {}
    cards_by_id: dict[str, list[JsonObject]] = {}
    order: list[str] = []
    for row in mask_rows:
        accepted_ids = frozenset(strings(row, "accepted_candidate_ids"))
        rag_parent_candidate_id = string(row, "rag_parent_candidate_id")
        seed_lane = string(row, "detector_lane")
        candidate_cards = cards.get(rag_parent_candidate_id, ())
        for record in _accepted_records(row, accepted_ids):
            candidate_id = string(record, "candidate_id")
            if candidate_id not in records_by_id:
                records_by_id[candidate_id] = (record, seed_lane)
                cards_by_id[candidate_id] = []
                order.append(candidate_id)
            merged_cards = cards_by_id[candidate_id]
            seen_card_ids = {string(card, "concept_card_id") for card in merged_cards}
            merged_cards.extend(
                card
                for card in candidate_cards
                if string(card, "concept_card_id") not in seen_card_ids
            )
    candidates: list[StartupCandidate] = []
    for candidate_id in order:
        record, seed_lane = records_by_id[candidate_id]
        record_image_id = string(record, "image_id")
        candidate_cards = tuple(cards_by_id[candidate_id])
        candidates.append(
            StartupCandidate(
                AnomalyCandidate(
                    CandidateId(candidate_id),
                    record_image_id,
                    string(record, "source_object_id"),
                    string(record, "source_view_id"),
                    seed_lane,
                    string(record, "prompt"),
                    _bbox(record),
                    _mask_reference(record),
                    _candidate_evidence(candidate_cards),
                    qwen_final_success=bool_value(record, "qwen_final_success"),
                    qwen_report_display_text=string(
                        record, "qwen_report_display_text"
                    ),
                    qwen_confidence=_optional_float(record, "qwen_confidence"),
                    source_tile_view_id=optional_string(
                        record, "source_tile_view_id"
                    ),
                ),
                record_image_id,
                candidate_cards,
            )
        )
    return tuple(candidates)


# _read_startup_inputs에서 _stage_candidates 직후에 호출된다. 후보 자신의
# 마스크(mask_refining이 원본 좌표로 복원했거나, 통과 경로가 rough_masking
# 원본을 그대로 넘긴 것)가 그 오브젝트의 실루엣 마스크와 절반도 안 겹치면
# bbox 모서리 배경에서 잘못 잡힌 탐지로 보고 버린다.
def _discard_off_object_candidates(
    candidates: tuple[StartupCandidate, ...], preprocessing_root: Path
) -> tuple[StartupCandidate, ...]:
    return tuple(
        item
        for item in candidates
        if _object_mask_overlap_ratio(item.candidate, preprocessing_root)
        >= _MIN_OBJECT_MASK_OVERLAP_RATIO
    )


# _discard_off_object_candidates에서 호출된다. 후보 마스크 픽셀 중 오브젝트
# 실루엣 마스크 안에 있는 비율을 계산한다. 둘 다 "마스크 우선" 설계상 항상
# 원본 이미지 좌표계이므로(추가 좌표 변환 없이) 그대로 겹쳐 셀 수 있다.
def _object_mask_overlap_ratio(
    candidate: AnomalyCandidate, preprocessing_root: Path
) -> float:
    object_mask_path = (
        preprocessing_root
        / "assets"
        / "objects"
        / candidate.image_id
        / candidate.source_object_id
        / "mask.png"
    )
    if not object_mask_path.is_file():
        field = "object_mask_path"
        reason = f"missing preprocessing object mask at {object_mask_path}"
        raise ContractValidationError(field, reason)
    candidate_mask = load_mask_array(Path(candidate.mask.path))
    object_mask = load_mask_array(object_mask_path)
    if candidate_mask.shape != object_mask.shape:
        field = "candidate_mask"
        reason = (
            f"shape {candidate_mask.shape} does not match object mask "
            f"shape {object_mask.shape}"
        )
        raise ContractValidationError(field, reason)
    total = int(candidate_mask.sum())
    if total == 0:
        return 0.0
    inside = int((candidate_mask & object_mask).sum())
    return inside / total


# _stage_candidates에서 호출된다. mask_refining 행(row)의
# accepted_candidates 배열 중 accepted_ids에 포함된 레코드만 추린다.
def _accepted_records(
    row: JsonObject,
    accepted_ids: frozenset[str],
) -> tuple[JsonObject, ...]:
    accepted_candidates = row.get("accepted_candidates")
    if not isinstance(accepted_candidates, list):
        raise_contract("accepted_candidates", "must be a list of objects")
    return tuple(
        record
        for item in accepted_candidates
        if (record := json_object(item, "accepted_candidates"))
        and string(record, "candidate_id") in accepted_ids
    )


# _stage_candidates에서 호출된다. 후보에 붙은 concept card들로 CandidateEvidence
# 를 만든다. concept_family는 첫 카드 값을 쓰고(없으면
# "unknown_visual_anomaly" - prompt_generating.VisualConceptFamily의 허용
# 어휘와 일치해야 함), descriptor는 여러 카드의 용어를 합쳐 중복 제거한다.
def _candidate_evidence(cards: tuple[JsonObject, ...]) -> CandidateEvidence:
    if not cards:
        return CandidateEvidence()
    first = cards[0]
    return CandidateEvidence(
        optional_string(first, "concept_family") or "unknown_visual_anomaly",
        unique_strings(
            term
            for card in cards
            for term in (
                *strings(card, "descriptor_terms"),
                *strings(card, "material_terms"),
                *strings(card, "context_terms"),
            )
        ),
        tuple(string(card, "concept_card_id") for card in cards),
        string(first, "provenance_strength"),
        ("rag_visual_concept_card",),
        visual_cue_confidence=max(_visual_cue_confidence(card) for card in cards),
    )


# _read_startup_inputs에서 호출된다. concept card 목록을
# rag_parent_candidate_id 기준으로 그룹핑해 _stage_candidates가 조회할 수 있게
# 만든다.
def _cards_by_candidate(
    cards: tuple[JsonObject, ...],
) -> dict[str, tuple[JsonObject, ...]]:
    grouped: dict[str, list[JsonObject]] = {}
    for card in cards:
        key = string(card, "rag_parent_candidate_id")
        grouped.setdefault(key, []).append(card)
    return {key: tuple(value) for key, value in grouped.items()}


def _bbox(record: JsonObject) -> BoundingBox:
    # Read the coordinate-transform-restored original-image-space bbox, not
    # the crop-local "bbox_xyxy" mask_refining also writes for provenance -
    # every downstream consumer of AnomalyCandidate.bbox expects real pixel
    # coordinates on the source image.
    values = numbers(record, "original_bbox_xyxy")
    if len(values) != _BBOX_COORDINATE_COUNT:
        raise_contract("original_bbox_xyxy", "must contain four numbers")
    return BoundingBox(*values)


# _stage_candidates에서 호출된다. mask_refining이 원본 이미지 좌표계로 복원한
# 정제 마스크(mask_path/mask_sha256)를 읽는다. 마스크가 없으면(원본 이미지
# 크기를 몰라 복원 못 한 구 산출물 등) 지금은 명확히 실패시킨다 -
# rough_masking 원본 마스크로의 자동 폴백은 아직 연결되지 않았다.
def _mask_reference(record: JsonObject) -> MaskReference:
    mask_path = optional_string(record, "mask_path")
    if mask_path is None:
        field = "mask_path"
        reason = (
            "mask_refining produced no restorable mask for this candidate; "
            "rough_masking fallback is not wired yet"
        )
        raise_contract(field, reason)
    return MaskReference(mask_path, string(record, "mask_sha256"))


def _optional_float(record: JsonObject, field: str) -> float | None:
    value = record.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise_contract(field, "must be a number or null")
    return float(value)


def _visual_cue_confidence(card: JsonObject) -> float:
    return float_value(json_object(card.get("visual_cue"), "visual_cue"), "confidence")
