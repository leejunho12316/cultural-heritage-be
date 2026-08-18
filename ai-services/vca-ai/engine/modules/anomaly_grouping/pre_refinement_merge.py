"""Merge same-physical-anomaly rough candidates before mask_refining runs.

Runs right after `rag` (needs concept cards for the RAG-corrected
concept_family) and right before `prompt_generating`. Two rough_masking
candidates merge only when, in *original-photo* pixel space, their masks
actually overlap **and** they describe the same *kind* of anomaly
(`concept_family` match **and** `morphology` compatible - `unknown` on either
axis is a wildcard that never blocks a merge on its own).

This supersedes two removed mechanisms:
- rough_masking's own pre-RAG tile-boundary merge (`tile_merge.py`, removed):
  it only had geometry to go on, so it risked merging two genuinely
  different anomalies that happened to be adjacent.
- The post-refinement relation-authority merge (`relations.py`, removed):
  merging that late meant duplicate candidates each independently triggered
  their own re-detection and RAG evidence lookup before ever being merged.

Mechanically this mirrors the old rough_masking tile_merge: it rewrites two
earlier stages' own output in place (suppress-original + synthesize-merged-
row), so `prompt_generating`/`mask_refining` never learn a merge happened -
- `rough_masking`'s `records.json` files (so later readers see one merged
  candidate instead of its fragments), and
- `rag`'s `rag_visual_concept_cards.jsonl` (so `prompt_generating` builds
  exactly one prompt-variant group per final candidate).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from shutil import copyfile
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from modules.anomaly_grouping.startup_json import (
    optional_string,
    read_jsonl_objects,
)
from modules.prompt_generating import Morphology, VisualConceptFamily, VisualCue
from modules.rag.operations.candidate_card_terms import concept_family
from modules.rag.operations.candidate_sidecar_models import (
    RAG_CANDIDATE_SIDECAR_MANIFEST,
)
from modules.rag.qwen import qwen_bridge_visual_cue, read_qwen_bridge_results
from modules.rag.qwen.qwen_bridge_json import parse_json_object
from modules.rough_masking import candidate_view_transform, restore_original_bbox
from modules.rough_masking.artifacts.records import JsonRecord, decode_records
from modules.shared import CandidateId, ContractValidationError
from modules.shared.mask_pixels import (
    load_mask_array,
    mask_union_at_boxes,
    write_mask_png,
)
from modules.visual_cue_generation.rough_records import rough_qwen_candidates

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from numpy.typing import NDArray

    from modules.report_generating.models import JsonObject, JsonValue
    from modules.rough_masking import RawDetectorCandidate
    from modules.shared import QwenBridgeResult
    from modules.visual_cue_generation.rough_records import RoughQwenCandidate

_MASK_SEMANTICS = "anomaly_region"


@dataclass(frozen=True, slots=True)
class _EvaluatedCandidate:
    """One accepted rough candidate plus everything the merge decision needs."""

    item: RoughQwenCandidate
    original_bbox_xyxy: tuple[float, float, float, float]
    mask_array: NDArray[np.bool_]
    concept_family: VisualConceptFamily
    morphology: Morphology
    # 병합 판정엔 morphology만 쓰지만(위), 병합이 실제로 일어나면 이 멤버가
    # 대표(가장 넓은 마스크)로 뽑혔을 때 병합 카드의 visual_cue 전체를
    # 새로 Qwen을 부르지 않고 이 값으로 교체하는 데 쓴다 - visual_cue_generation이
    # 병합 전 이미 이 후보 자신에 대해 계산해 둔 값이라 재사용이 안전하다.
    visual_cue: VisualCue | None


# 프로젝트 전체 rough_masking 산출물을 대상으로 물리적 특이점 병합을
# 수행한다. rag 단계가 끝난 직후, prompt_generating이 시작하기 전에 딱 한 번
# 호출된다(주석 참고: 정확한 위치는 orchestration의 스테이지 순서가 정한다).
def merge_before_refinement(
    rough_root: Path,
    asset_root: Path,
    input_manifest_path: Path,
    rag_concept_cards_path: Path,
    qwen_bridge_results_path: Path,
) -> None:
    """Merge same-physical-anomaly candidates, rewriting rough_masking + rag output."""
    candidates = rough_qwen_candidates(rough_root, asset_root)
    if not candidates:
        return
    seed_families = _card_concept_families(rag_concept_cards_path)
    # visual_cue_generation always creates this directory in production; the
    # is_dir() fallback only matters for callers/tests that skip that stage.
    bridge_results = (
        read_qwen_bridge_results(qwen_bridge_results_path)
        if qwen_bridge_results_path.is_dir()
        else {}
    )
    # 병합은 카드 유무와 상관없이 전체 후보를 대상으로 한다 - RAG 근거가 없는
    # 후보도 _evaluate가 시드 프롬프트로 concept_family를 대체 계산해주므로
    # 병합 판정 자체엔 지장이 없다. 카드 없는 후보를 병합보다 먼저 걸러내면,
    # 카드 있는 이웃과 합쳐져 특이점의 진짜 범위를 완성할 기회를 잃는다(실측:
    # 오브젝트 레벨 "crusty deposit" 후보가 카드가 없어 먼저 걸러지는 바람에
    # 같은 자리의 카드 있는 타일 후보와 못 합쳐져, 리포트에 한쪽이 잘린 채로
    # 남는 사례가 실제로 있었음). 미분류 판정은 병합이 다 끝난 뒤로 미룬다.
    evaluated = tuple(
        _evaluate(item, asset_root, bridge_results, seed_families)
        for item in candidates
    )
    by_image: dict[str, list[_EvaluatedCandidate]] = {}
    for item in evaluated:
        by_image.setdefault(str(item.item.candidate.image_id), []).append(item)
    merge_components = tuple(
        component
        for members in by_image.values()
        for component in _connected_components(members)
        if len(component) >= 2  # noqa: PLR2004
    )
    if merge_components:
        original_image_paths = _original_image_paths(input_manifest_path, asset_root)
        suppressed_by_file: dict[Path, set[int]] = {}
        merged_by_output: dict[Path, list[JsonRecord]] = {}
        member_to_group_id: dict[str, str] = {}
        group_cues: dict[str, VisualCue] = {}
        for component in merge_components:
            output_path, record, group_id, cue = _merge_component(
                rough_root, asset_root, component, original_image_paths
            )
            merged_by_output.setdefault(output_path, []).append(record)
            if cue is not None:
                group_cues[group_id] = cue
            for evaluated_candidate in component:
                rough = evaluated_candidate.item.rough
                suppressed_by_file.setdefault(
                    rough_root / rough.rough_record_path, set()
                ).add(rough.rough_record_index)
                member_to_group_id[str(rough.candidate_id)] = group_id
        for records_path, indices in suppressed_by_file.items():
            _suppress_rows(records_path, indices)
        for output_path, records in merged_by_output.items():
            _write_records(output_path, records)
        new_card_count = _rewrite_concept_cards(
            rag_concept_cards_path, member_to_group_id, group_cues
        )
        _update_concept_card_manifest_count(rag_concept_cards_path, new_card_count)

    # 취합(병합)이 끝난 뒤, 그 결과(병합돼 카드를 물려받은 그룹 포함) 기준으로
    # 다시 읽어서 RAG 근거가 여전히 하나도 없는 후보만 이제 걸러낸다.
    post_merge_candidates = rough_qwen_candidates(rough_root, asset_root)
    post_merge_seed_families = _card_concept_families(rag_concept_cards_path)
    _drop_unclassified_candidates(
        rough_root, post_merge_candidates, post_merge_seed_families
    )


# 취합(병합) 시작 전에, RAG 근거(concept card)가 하나도 없는 후보를 아예
# 걸러낸다. report_trace_assembly의 _candidate_evidence가 "이 candidate_id로
# 매칭되는 카드가 하나도 없으면 concept_family를 기본값 'unknown'(미분류)으로
# 둔다"는 것과 정확히 같은 기준(카드 존재 여부, seed_families의 키 집합)을
# 여기서 미리 적용한다 - 어차피 미분류로 끝날 후보를 prompt_generating/
# mask_refining까지 보낼 필요가 없다. seed_families엔 카드가 있는 후보만
# 들어있으므로(_card_concept_families 참고), 거기 없는 후보를 원본
# rough_masking 레코드에서 accepted=false로 고쳐 쓰고 취합 대상에서 뺀다.
# unknown_visual_anomaly(카드는 있지만 11개 알려진 종류 중 어디에도 안 맞음)는
# 근거 자체는 있는 케이스라 여기서 거르지 않는다 - 카드가 있으면 통과.
def _drop_unclassified_candidates(
    rough_root: Path,
    candidates: tuple[RoughQwenCandidate, ...],
    seed_families: Mapping[str, VisualConceptFamily],
) -> tuple[RoughQwenCandidate, ...]:
    classified: list[RoughQwenCandidate] = []
    dropped_by_file: dict[Path, set[int]] = {}
    for item in candidates:
        if str(item.candidate.candidate_id) in seed_families:
            classified.append(item)
            continue
        rough = item.rough
        dropped_by_file.setdefault(
            rough_root / rough.rough_record_path, set()
        ).add(rough.rough_record_index)
    for records_path, indices in dropped_by_file.items():
        _suppress_rows(records_path, indices, reason="unclassified_no_rag_evidence")
    return tuple(classified)


# rag_visual_concept_cards.jsonl을 한 번 읽어 candidate_id별 concept_family를
# 인덱싱한다. 파일 순서상 먼저 나온 카드를 대표값으로 쓴다(anomaly_grouping의
# 기존 CandidateEvidence가 "cards[0]"을 대표로 쓰는 관례와 동일). RAG 근거가
# 없어 카드가 아예 없는 후보는 여기 없다 - _drop_unclassified_candidates가 이
# 부재를 걸러내는 기준으로 그대로 쓴다.
def _card_concept_families(
    cards_path: Path,
) -> dict[str, VisualConceptFamily]:
    if not cards_path.is_file():
        return {}
    families: dict[str, VisualConceptFamily] = {}
    for card in read_jsonl_objects(cards_path):
        candidate_id = optional_string(card, "rag_parent_candidate_id")
        family_value = optional_string(card, "concept_family")
        if candidate_id is None or family_value is None:
            continue
        if candidate_id in families:
            continue
        try:
            families[candidate_id] = VisualConceptFamily(family_value)
        except ValueError:
            continue
    return families


# 후보 하나를 병합 판정에 필요한 형태로 평가한다: 원본 사진 좌표로 복원한
# bbox, 그 bbox 크기로 잘라낸 마스크 배열(스케일이 항상 1.0이라 크롭 없이는
# 원본 좌표 bbox와 짝지을 수 없음 - load_mask_array가 돌려주는 건 SAM2가 낸
# 뷰(타일/오브젝트) 전체 크기 마스크라서, bbox 원점을 그대로 이 전체 마스크의
# 붙여넣기 기준으로 쓰면 실제 픽셀 위치가 어긋난다. _masks_intersect/
# mask_union_at_boxes 둘 다 "마스크가 자기 bbox 크기만큼만 잘려 있다"고
# 가정하므로 여기서 미리 잘라 그 계약을 맞춘다), concept_family(카드가 있으면
# 그 값, 없으면 시드 프롬프트), morphology(항상 candidate 자신의 Qwen 결과에서
# - RAG 카드 유무와 무관).
def _evaluate(
    item: RoughQwenCandidate,
    asset_root: Path,
    bridge_results: Mapping[CandidateId, QwenBridgeResult],
    seed_families: Mapping[str, VisualConceptFamily],
) -> _EvaluatedCandidate:
    candidate = item.candidate
    transform = candidate_view_transform(candidate)
    original_bbox = restore_original_bbox(candidate.bbox_xyxy, transform, None, None)
    view_mask = load_mask_array(asset_root / candidate.rough_mask.relative_path)
    mask_array = _crop_to_bbox(view_mask, candidate.bbox_xyxy)
    candidate_id = str(candidate.candidate_id)
    family = seed_families.get(candidate_id) or concept_family(
        candidate.executable_prompt
    )
    cue = _visual_cue(candidate.candidate_id, bridge_results)
    morphology = cue.morphology if cue is not None else Morphology.UNKNOWN
    return _EvaluatedCandidate(item, original_bbox, mask_array, family, morphology, cue)


# view_mask(SAM2가 낸, 뷰 전체 크기의 마스크)를 candidate 자신의 뷰-로컬
# bbox_xyxy 영역만큼 잘라낸다. candidate_view_transform의 scale이 항상
# 1.0이므로, 이렇게 잘라낸 마스크의 크기는 restore_original_bbox가 돌려주는
# original_bbox_xyxy의 크기와 정확히 같다.
def _crop_to_bbox(
    view_mask: NDArray[np.bool_],
    bbox_xyxy: tuple[float, float, float, float],
) -> NDArray[np.bool_]:
    height, width = view_mask.shape
    left = min(max(round(bbox_xyxy[0]), 0), width)
    top = min(max(round(bbox_xyxy[1]), 0), height)
    right = min(max(round(bbox_xyxy[2]), left), width)
    bottom = min(max(round(bbox_xyxy[3]), top), height)
    return view_mask[top:bottom, left:right]


def _visual_cue(
    candidate_id: CandidateId, bridge_results: Mapping[CandidateId, QwenBridgeResult]
) -> VisualCue | None:
    bridge = bridge_results.get(candidate_id)
    return None if bridge is None else qwen_bridge_visual_cue(bridge)


# 두 후보가 "같은 종류"인지 판정한다: concept_family/morphology 둘 다, 어느
# 한쪽이라도 unknown이면 그 축은 판단을 보류(와일드카드)하고, 둘 다 확정값을
# 가졌는데 서로 다르면 불일치로 본다.
def _same_kind(left: _EvaluatedCandidate, right: _EvaluatedCandidate) -> bool:
    if (
        left.concept_family is not VisualConceptFamily.UNKNOWN_VISUAL_ANOMALY
        and right.concept_family is not VisualConceptFamily.UNKNOWN_VISUAL_ANOMALY
        and left.concept_family is not right.concept_family
    ):
        return False
    return not (
        left.morphology is not Morphology.UNKNOWN
        and right.morphology is not Morphology.UNKNOWN
        and left.morphology is not right.morphology
    )


def _bbox_overlaps(
    left: tuple[float, float, float, float], right: tuple[float, float, float, float]
) -> bool:
    width = min(left[2], right[2]) - max(left[0], right[0])
    height = min(left[3], right[3]) - max(left[1], right[1])
    return width > 0.0 and height > 0.0


# bbox 겹침은 값싼 1차 필터일 뿐이다 - 실제 판정은 마스크 픽셀이 실제로
# 겹치는지다. 두 후보의 bbox 합집합만큼만 작은 공용 캔버스를 만들어(전체
# 사진 크기 캔버스를 할당하지 않음) 각자의 마스크를 자기 위치에 붙인 뒤
# AND로 실제 교집합이 있는지 확인한다.
def _masks_intersect(left: _EvaluatedCandidate, right: _EvaluatedCandidate) -> bool:
    lb, rb = left.original_bbox_xyxy, right.original_bbox_xyxy
    canvas_left = min(round(lb[0]), round(rb[0]))
    canvas_top = min(round(lb[1]), round(rb[1]))
    canvas_right = max(round(lb[2]), round(rb[2]))
    canvas_bottom = max(round(lb[3]), round(rb[3]))
    height = canvas_bottom - canvas_top
    width = canvas_right - canvas_left
    if height <= 0 or width <= 0:
        return False
    left_canvas = np.zeros((height, width), dtype=np.bool_)
    right_canvas = np.zeros((height, width), dtype=np.bool_)
    _paste(left_canvas, left.mask_array, lb, canvas_left, canvas_top)
    _paste(right_canvas, right.mask_array, rb, canvas_left, canvas_top)
    return bool(np.any(left_canvas & right_canvas))


def _paste(
    canvas: NDArray[np.bool_],
    mask: NDArray[np.bool_],
    bbox: tuple[float, float, float, float],
    canvas_left: int,
    canvas_top: int,
) -> None:
    offset_x = round(bbox[0]) - canvas_left
    offset_y = round(bbox[1]) - canvas_top
    height = min(mask.shape[0], canvas.shape[0] - offset_y)
    width = min(mask.shape[1], canvas.shape[1] - offset_x)
    if height <= 0 or width <= 0:
        return
    canvas[offset_y : offset_y + height, offset_x : offset_x + width] |= mask[
        :height, :width
    ]


# 같은 이미지 안의 후보들에서, bbox 겹침 + 같은 종류 + 실제 마스크 겹침을
# 모두 만족하는 쌍만 union-find로 연결해 컴포넌트를 만든다. 크기 1인
# 컴포넌트(다른 누구와도 안 겹치거나 종류가 다른 후보)도 그대로 포함해
# 반환한다 - 호출부가 len(component) >= 2로 실제 병합 대상만 걸러낸다.
def _connected_components(
    members: list[_EvaluatedCandidate],
) -> tuple[tuple[_EvaluatedCandidate, ...], ...]:
    count = len(members)
    parent = list(range(count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i in range(count):
        left = members[i]
        for j in range(i + 1, count):
            right = members[j]
            if not _bbox_overlaps(left.original_bbox_xyxy, right.original_bbox_xyxy):
                continue
            if not _same_kind(left, right):
                continue
            if not _masks_intersect(left, right):
                continue
            left_root, right_root = find(i), find(j)
            if left_root != right_root:
                parent[right_root] = left_root

    groups: dict[int, list[_EvaluatedCandidate]] = {}
    for index in range(count):
        groups.setdefault(find(index), []).append(members[index])
    return tuple(tuple(group) for group in groups.values())


# 병합 그룹 하나를 마스크 union으로 합쳐 새 records.json 행(및 마스크/오버레이/
# 크롭 파일)을 만든다. 대표(결정론적 최소 candidate_id)의 prompt/lane/
# prompt_pack_id를 그대로 쓴다 - rough_masking/rough_records.py가 이 값들로
# 잠긴 시드 프롬프트와 대조하므로 임의로 새 값을 지어낼 수 없다. 반환하는
# group_id는 rag concept-card 재기록에도 그대로 쓰이고, cue는 그 재기록이
# 카드의 visual_cue를 대표 멤버 아닌 "가장 넓은 마스크를 낸 멤버" 것으로
# 바꿔치기하는 데 쓰인다(아래 _cue_representative 참고 - 병합된 물리적
# 특이점을 가장 잘 대표하는 값이라고 보는 게 prompt/lane 선택 기준인
# "결정론적 최소 candidate_id"보다 합리적이다. 둘을 분리한 이유는
# prompt/prompt_pack_id는 시드 프롬프트 잠금 계약 때문에 대표 하나를 결정론적으로
# 고정해야 하지만, cue는 그런 제약이 없어 더 나은 신호를 골라 쓸 수 있어서다).
def _merge_component(
    rough_root: Path,
    asset_root: Path,
    component: tuple[_EvaluatedCandidate, ...],
    original_image_paths: dict[str, Path],
) -> tuple[Path, JsonRecord, str, VisualCue | None]:
    ordered = sorted(
        component, key=lambda evaluated: str(evaluated.item.candidate.candidate_id)
    )
    cue = _cue_representative(component).visual_cue
    representative: RawDetectorCandidate = ordered[0].item.candidate
    lane = ordered[0].item.rough.lane
    object_id = representative.source_object_id
    output_dir = rough_root / lane / str(object_id) / lane / "anomaly_merged"
    identity = json.dumps(
        sorted(str(evaluated.item.candidate.candidate_id) for evaluated in ordered),
        sort_keys=True,
    )
    digest = sha256(identity.encode()).hexdigest()[:24]
    group_id = f"anomaly-merged:{digest}"
    masks_and_boxes = [
        (evaluated.mask_array, evaluated.original_bbox_xyxy) for evaluated in ordered
    ]
    union_array, union_bbox = mask_union_at_boxes(masks_and_boxes)
    canvas_height, canvas_width = union_array.shape
    mask_relative = f"masks/{digest}.png"
    overlay_relative = f"overlays/{digest}.jpg"
    write_mask_png(union_array, output_dir / mask_relative)
    (output_dir / overlay_relative).parent.mkdir(parents=True, exist_ok=True)
    copyfile(
        asset_root / representative.overlay.relative_path,
        output_dir / overlay_relative,
    )
    view_image_path = _materialize_merged_view_image(
        output_dir, digest, str(representative.image_id), union_bbox,
        original_image_paths,
    )
    record: JsonRecord = {
        "accepted": True,
        "candidate_id": group_id,
        "prompt": representative.executable_prompt,
        "image": str(representative.image_id),
        "generation_lane": lane,
        "prompt_pack_id": representative.prompt_provenance.prompt_pack_id,
        "score": max(evaluated.item.candidate.score for evaluated in ordered),
        "bbox_xyxy": [0.0, 0.0, float(canvas_width), float(canvas_height)],
        "view_origin_xyxy": list(union_bbox),
        "view_image_path": str(view_image_path),
        "mask_semantics": _MASK_SEMANTICS,
        "mask_path": mask_relative,
        "overlay_path": overlay_relative,
    }
    return output_dir / "records.json", record, group_id, cue


# 병합 그룹 안에서 가장 넓은 마스크 면적을 낸 멤버를 고른다 - 여러 조각 중
# 물리적으로 가장 많은 부분을 실제로 관측한 멤버일 가능성이 높으므로, 그
# 멤버의 (이미 visual_cue_generation이 계산해 둔) visual_cue가 병합된 전체
# 모양을 가장 잘 대표한다고 본다. 동률이면 candidate_id로 결정론적으로
# 정한다.
def _cue_representative(
    component: tuple[_EvaluatedCandidate, ...],
) -> _EvaluatedCandidate:
    return max(
        component,
        key=lambda evaluated: (
            int(evaluated.mask_array.sum()),
            str(evaluated.item.candidate.candidate_id),
        ),
    )


# 병합 뷰가 참조할 이미지 파일을 원본 사진에서 새로 잘라 만든다 - 병합은
# 여러 물리 뷰의 마스크를 합친 결과라 대응하는 물리 파일이 원래 없다.
def _materialize_merged_view_image(
    output_dir: Path,
    digest: str,
    image_id: str,
    union_bbox: tuple[float, float, float, float],
    original_image_paths: dict[str, Path],
) -> Path:
    original_path = original_image_paths.get(image_id)
    if original_path is None:
        field = "original_image_paths"
        reason = f"original photo missing for image_id={image_id}"
        raise ContractValidationError(field, reason)
    crop_relative = f"crops/{digest}.jpg"
    crop_path = output_dir / crop_relative
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    left, top, right, bottom = union_bbox
    with Image.open(original_path) as original_image:
        cropped = original_image.convert("RGB").crop(
            (round(left), round(top), round(right), round(bottom))
        )
        cropped.save(crop_path, format="JPEG")
    return crop_path.resolve()


# input_manifest.json을 한 번 읽어 image_id -> 원본 사진 절대경로 매핑을
# 만든다. asset_root를 벗어나거나 실제 파일이 아닌 항목은 조용히 걸러낸다
# (같은 파이프라인 실행 안에서 이전 스테이지가 직전에 쓴 파일을 다시 읽는
# 내부 일관성 조회이므로 해시 재검증까지는 필요 없다).
def _original_image_paths(
    input_manifest_path: Path, asset_root: Path
) -> dict[str, Path]:
    payload = parse_json_object(input_manifest_path.read_text(encoding="utf-8"))
    images = payload.get("images")
    if not isinstance(images, list):
        return {}
    resolved_root = asset_root.resolve()
    paths: dict[str, Path] = {}
    for raw in images:
        entry = _original_image_entry(raw, resolved_root)
        if entry is not None:
            image_id, path = entry
            paths[image_id] = path
    return paths


def _original_image_entry(
    raw: JsonValue, resolved_root: Path
) -> tuple[str, Path] | None:
    if not isinstance(raw, dict):
        return None
    image_id = raw.get("image_id")
    raw_path = raw.get("run_root_asset_path")
    if not isinstance(image_id, str) or not isinstance(raw_path, str):
        return None
    resolved = Path(raw_path).resolve()
    if resolved.is_relative_to(resolved_root) and resolved.is_file():
        return image_id, resolved
    return None


# 병합(또는 미분류 제외)에 걸린 원본 행들을 그 파일 안에서만 accepted=false로
# 고쳐 쓴다 - 다른 행/파일 구조는 그대로 둔다. reason은 호출부가 왜
# 제외됐는지(병합돼 사라짐 vs RAG 근거 없음) 구분해 남긴다.
def _suppress_rows(
    records_path: Path, indices: set[int], reason: str = "merged_into_anomaly_group"
) -> None:
    decoded = decode_records(records_path.read_text(encoding="utf-8"))
    if decoded is None:
        return
    updated = tuple(
        {**record, "accepted": False, "reject_reason": reason}
        if index in indices
        else record
        for index, record in enumerate(decoded)
    )
    _write_records(records_path, updated)


def _write_records(path: Path, records: Iterable[JsonRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(list(records), sort_keys=True, separators=(",", ":"))
    path.write_text(payload, encoding="utf-8")


# rag_visual_concept_cards.jsonl을 한 번 순회하며, 병합된 멤버 후보들의 카드를
# **전부 모아 하나로 합친다** - 같은 물리적 특이점으로 병합됐다면 각 멤버가
# 따로 찾은 문헌 근거도 전부 그 특이점의 근거이지, 그중 하나만 골라 나머지를
# 버릴 이유가 없다. 병합에 관여하지 않은 후보의 카드는 그대로 둔다.
# merge_before_refinement이 병합 대상을 다 정한 뒤 한 번 호출한다.
def _rewrite_concept_cards(
    cards_path: Path,
    member_to_group_id: dict[str, str],
    group_cues: dict[str, VisualCue],
) -> int:
    if not cards_path.is_file():
        return 0
    rows = read_jsonl_objects(cards_path)
    group_member_cards: dict[str, list[JsonObject]] = {}
    output_rows: list[JsonObject] = []
    for row in rows:
        candidate_id = row.get("rag_parent_candidate_id")
        group_id = (
            member_to_group_id.get(candidate_id)
            if isinstance(candidate_id, str)
            else None
        )
        if group_id is None:
            output_rows.append(row)
            continue
        group_member_cards.setdefault(group_id, []).append(row)
    for group_id, member_cards in group_member_cards.items():
        output_rows.append(
            _merge_group_cards(member_cards, group_id, group_cues.get(group_id))
        )
    payload = "" if not output_rows else "\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) for row in output_rows
    ) + "\n"
    cards_path.write_text(payload, encoding="utf-8")
    return len(output_rows)


# 병합 그룹 하나에 속한 멤버 카드 전체를 카드 한 장으로 합친다.
# - 근거 목록(source_citation_ids/descriptor_terms/material_terms/
#   context_terms)은 전부 합쳐 중복 제거한다(정렬해서 결정론적으로) - 어느
#   멤버가 어떤 근거를 찾았든 병합된 특이점 전체의 근거이므로 하나만 남기지
#   않는다.
# - raw_retrieved_sentence/provenance_strength/concept_family/image_id처럼
#   목록이 아니라 "값 하나"만 가질 수 있는 필드는, retrieval_score가 가장
#   높은(가장 신뢰도 높게 매칭된) 멤버의 카드를 대표로 써서 채운다.
# - visual_cue는 근거가 아니라 시각 관측이라 다른 기준(마스크 면적이 가장
#   큰 멤버, _cue_representative)을 쓴다 - group_cues에 있으면 그걸로
#   덮어쓴다.
def _merge_group_cards(
    member_cards: list[JsonObject], group_id: str, cue: VisualCue | None
) -> JsonObject:
    primary = max(
        member_cards, key=lambda card: _float_or_zero(card.get("retrieval_score"))
    )
    card_id = primary.get("concept_card_id")
    return {
        **primary,
        "rag_parent_candidate_id": group_id,
        "concept_card_id": f"{card_id}:{group_id}",
        "descriptor_terms": _union_sorted(
            card.get("descriptor_terms") for card in member_cards
        ),
        "material_terms": _union_sorted(
            card.get("material_terms") for card in member_cards
        ),
        "context_terms": _union_sorted(
            card.get("context_terms") for card in member_cards
        ),
        "source_citation_ids": _union_sorted(
            card.get("source_citation_ids") for card in member_cards
        ),
        **({"visual_cue": _serialize_visual_cue(cue)} if cue else {}),
    }


def _float_or_zero(value: JsonValue) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return value


# term_lists의 각 원소(리스트/튜플이 아니면 빈 것으로 취급)를 합쳐 중복
# 제거 후 정렬한다. 카드 여러 장의 문자열 목록 필드(descriptor_terms 등)를
# 합칠 때 공용으로 쓴다 - 정렬해서 합치는 순서와 무관하게 항상 같은 결과가
# 나오게 한다.
def _union_sorted(term_lists: Iterable[JsonValue]) -> list[str]:
    unioned: set[str] = set()
    for terms in term_lists:
        if not isinstance(terms, list):
            continue
        unioned.update(term for term in terms if isinstance(term, str))
    return sorted(unioned)


# rag/operations/candidate_sidecar_writers.py의 _card_payload가 카드를 처음
# 쓸 때 쓰는 visual_cue 직렬화와 같은 필드 이름/형태를 그대로 맞춘다 - 포맷이
# 어긋나면 그 카드를 나중에 다시 읽는 쪽(prompt_generating의 카드 파서)이
# 깨진다.
def _serialize_visual_cue(cue: VisualCue) -> JsonObject:
    return {
        "boundary_relation": cue.boundary_relation.value,
        "color_bucket": cue.color_bucket.value,
        "confidence": cue.confidence,
        "morphology": cue.morphology.value,
        "reasons": list(cue.reasons),
        "size_class": cue.size_class.value,
        "texture_proxy": cue.texture_proxy.value,
    }


# _rewrite_concept_cards가 rag_visual_concept_cards.jsonl을 다시 쓴 뒤, 같은
# 디렉터리의 sidecar manifest에 기록된 카운트를 새 행 수로 맞춘다.
# rag_candidate_evidence_rows/schema는 이 병합 단계가 건드리지 않는 값이라
# 그대로 보존한다. manifest가 없으면(예: 테스트가 카드 파일만 준비한 경우)
# 아무 것도 하지 않는다 - 만들어야 할 대상이 아니라 이미 있는 걸 고치는
# 역할이기 때문이다.
def _update_concept_card_manifest_count(cards_path: Path, new_count: int) -> None:
    manifest_path = cards_path.parent / RAG_CANDIDATE_SIDECAR_MANIFEST
    if not manifest_path.is_file():
        return
    manifest = parse_json_object(manifest_path.read_text(encoding="utf-8"))
    manifest["rag_visual_concept_cards"] = new_count
    temporary = manifest_path.with_suffix(f"{manifest_path.suffix}.tmp")
    try:
        _ = temporary.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        _ = temporary.replace(manifest_path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
