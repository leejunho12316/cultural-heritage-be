"""rough_masking 후보들에 대한, RAG 이전 단계의 타일 경계 병합.

원본 스펙(`context/text-prompt-segmentation-rag-pipeline.md:353`,
`.omo/plans/artifact-visual-inspection-pipeline.md`)의 T5는, 타일 경계로
쪼개진 하나의 물리적 이상 소견이 타일 조각 수만큼이 아니라 정확히 RAG
쿼리 1개만 받도록, RAG 이전 속성(seed prompt, seed lane, source object,
geometry)만으로 RAG 이전에 후보들을 그룹핑했다. 이 모듈은 그 단계를 좁은
범위로 되살린다: RAG 근거는 전혀 보지 않으며(파이프라인의 이 시점에는
아직 RAG 근거가 없다 - `EXECUTED_STAGE_NAMES` 기준으로 `rag`/
`visual_cue_generation`은 rough_masking의 출력을 직접 읽는다), *같은*
object, *같은* seed lane, *같은* seed prompt에서 나왔지만 *다른* 타일이고
bbox가 겹치는 쌍만 병합한다.

예전의 (제거된) `pre_rag.py`와 달리, 아무것도 "억제(suppress)"하지 않고
크기나 lane 우선순위로 부모를 고르지도 않는다 - 병합된 그룹의 마스크/bbox는
구성원들의 픽셀 *합집합*이며, 이 합집합은 마치 평범한 accepted 후보인 것처럼
그대로 `visual_cue_generation`/`rag`/`prompt_generating`/`mask_refining`으로
이어진다. 구성원들은 원래의 records.json 행에서 `accepted: false`
(`reject_reason: "merged_into_tile_group"`)로 표시되므로, 기존의 모든
리더(`read_rough_records`, `rough_qwen_candidates`)는 다운스트림 코드 변경
없이 병합된 후보를 정확히 한 번씩만 보게 된다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from shutil import copyfile
from typing import TYPE_CHECKING

from modules.rough_masking.artifacts.records import JsonRecord, decode_records
from modules.shared.mask_pixels import (
    load_mask_array,
    mask_union_at_boxes,
    write_mask_png,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

_MASK_SEMANTICS = "anomaly_region"
_OBJECT_VIEW_SEGMENT = "object"
_REQUIRED_PATH_PARTS = 5  # <lane>/<object_id>/<lane>/<view_segment>/records.json


@dataclass(frozen=True, slots=True)
class _RowLocation:
    """Where one records.json row lives, and what its path implies."""

    records_path: Path
    index: int
    lane: str
    object_id: str
    tile_view_id: str | None


@dataclass(frozen=True, slots=True)
class _RoughRow:
    """One accepted rough_masking candidate row, with its on-disk location."""

    records_path: Path
    record_index: int
    lane: str
    image_id: str
    prompt_text: str
    generation_lane: str
    prompt_pack_id: str
    score: float
    bbox_xyxy: tuple[float, float, float, float]
    mask_path: Path
    overlay_path: Path
    source_object_id: str
    source_tile_view_id: str | None


# 프로젝트 전체 rough_masking 산출물을 대상으로 타일 경계 병합을 수행한다.
# run_rough_masking_stage()가 모든 오브젝트의 탐지 루프를 마친 직후, 성공
# 종료 직전에 딱 한 번 호출한다.
def merge_tile_split_rough_candidates(root: Path) -> None:
    """Merge same-prompt, same-object, different-tile overlapping candidates."""
    rows = _read_accepted_rows(root)
    buckets: dict[tuple[str, str, str, str], list[_RoughRow]] = {}
    for row in rows:
        key = (row.image_id, row.source_object_id, row.lane, row.prompt_text)
        buckets.setdefault(key, []).append(row)
    merged_by_output: dict[Path, list[JsonRecord]] = {}
    suppressed_by_file: dict[Path, set[int]] = {}
    for members in buckets.values():
        for component in _tile_overlap_components(members):
            if len(component) < 2:  # noqa: PLR2004
                continue
            output_path, record = _merge_component(root, component)
            merged_by_output.setdefault(output_path, []).append(record)
            for row in component:
                suppressed_by_file.setdefault(row.records_path, set()).add(
                    row.record_index
                )
    for records_path, indices in suppressed_by_file.items():
        _suppress_rows(records_path, indices)
    for output_path, records in merged_by_output.items():
        _write_records(output_path, records)


# root 아래 모든 records.json을 순회하며 accepted=true 행만 _RoughRow로
# 파싱한다. 위치(경로 구조)에서 lane/source_object_id/source_tile_view_id를
# 파생시킨다 - 이 값들은 records.json 필드로 저장되지 않기 때문이다
# (rough_masking/startup_runner.py:_lane_view_paths의 디렉터리 규약에 의존).
def _read_accepted_rows(root: Path) -> tuple[_RoughRow, ...]:
    rows: list[_RoughRow] = []
    for records_path in sorted(root.glob("**/records.json")):
        parts = records_path.relative_to(root).parts
        if len(parts) < _REQUIRED_PATH_PARTS:
            continue
        lane, object_id, _, view_segment = parts[:4]
        tile_view_id = None if view_segment == _OBJECT_VIEW_SEGMENT else view_segment
        decoded = decode_records(records_path.read_text(encoding="utf-8"))
        if decoded is None:
            continue
        for index, record in enumerate(decoded):
            location = _RowLocation(records_path, index, lane, object_id, tile_view_id)
            row = _row_from_record(location, record)
            if row is not None:
                rows.append(row)
    return tuple(rows)


def _row_from_record(location: _RowLocation, record: JsonRecord) -> _RoughRow | None:
    if record.get("accepted") is not True or record.get("mask_semantics") != (
        _MASK_SEMANTICS
    ):
        return None
    bbox = _bbox_or_none(record.get("bbox_xyxy"))
    strings = _required_strings(record)
    score = record.get("score")
    if bbox is None or strings is None or not isinstance(score, int | float):
        return None
    prompt_text, image_id, generation_lane, prompt_pack_id, mask_path, overlay_path = (
        strings
    )
    lane_root = location.records_path.parent
    return _RoughRow(
        location.records_path,
        location.index,
        location.lane,
        image_id,
        prompt_text,
        generation_lane,
        prompt_pack_id,
        float(score),
        bbox,
        lane_root / mask_path,
        lane_root / overlay_path,
        location.object_id,
        location.tile_view_id,
    )


def _bbox_or_none(
    value: object,
) -> tuple[float, float, float, float] | None:
    coordinate_count = 4
    if not isinstance(value, list) or len(value) != coordinate_count:
        return None
    if not all(isinstance(item, int | float) for item in value):
        return None
    return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))


def _required_strings(record: JsonRecord) -> tuple[str, str, str, str, str, str] | None:
    fields = (
        "prompt",
        "image",
        "generation_lane",
        "prompt_pack_id",
        "mask_path",
        "overlay_path",
    )
    values: list[str] = []
    for field in fields:
        value = record.get(field)
        if not isinstance(value, str) or not value:
            return None
        values.append(value)
    return values[0], values[1], values[2], values[3], values[4], values[5]


def _overlaps(
    left: tuple[float, float, float, float], right: tuple[float, float, float, float]
) -> bool:
    width = min(left[2], right[2]) - max(left[0], right[0])
    height = min(left[3], right[3]) - max(left[1], right[1])
    return width > 0.0 and height > 0.0


# 같은 버킷(오브젝트+레인+시드 프롬프트) 안에서, 서로 다른 타일 출처 +
# bbox 겹침 쌍만 union-find로 연결한다. source_tile_view_id가 None인 행
# (오브젝트 크롭 전체 탐지)은 언제나 자기 자신만의 컴포넌트로 남는다 -
# 오브젝트 크롭은 사실상 모든 타일과 겹치므로 병합 대상에 넣으면 무관한
# 후보까지 줄줄이 엮인다.
def _tile_overlap_components(
    members: list[_RoughRow],
) -> tuple[tuple[_RoughRow, ...], ...]:
    parent: dict[int, int] = {index: index for index in range(len(members))}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    _connect_overlapping_tile_pairs(members, find, parent)

    components: dict[int, list[_RoughRow]] = {}
    for index, row in enumerate(members):
        components.setdefault(find(index), []).append(row)
    return tuple(tuple(group) for group in components.values())


# _tile_overlap_components의 순환 복잡도를 낮추기 위해 분리한 내부 이중
# 루프 - 서로 다른 타일 출처 + bbox 겹침 쌍만 union-find로 연결한다.
def _connect_overlapping_tile_pairs(
    members: list[_RoughRow],
    find: Callable[[int], int],
    parent: dict[int, int],
) -> None:
    for i, left in enumerate(members):
        if left.source_tile_view_id is None:
            continue
        for j in range(i + 1, len(members)):
            right = members[j]
            if right.source_tile_view_id is None:
                continue
            if left.source_tile_view_id == right.source_tile_view_id:
                continue
            if not _overlaps(left.bbox_xyxy, right.bbox_xyxy):
                continue
            left_root, right_root = find(i), find(j)
            if left_root != right_root:
                parent[right_root] = left_root


# 병합 그룹 하나를 마스크 union으로 합쳐 새 records.json 행(및 마스크/오버레이
# 파일)을 만든다. 대표는 결정론적 정렬 최소값일 뿐 "더 나은 후보를 골랐다"는
# 의미는 없다 - 마스크/bbox는 항상 union이고, prompt/lane/image는 그룹 전체가
# 이미 공유하는 값이다(버킷 키 자체가 이걸 보장한다).
def _merge_component(
    root: Path, component: tuple[_RoughRow, ...]
) -> tuple[Path, JsonRecord]:
    ordered = sorted(
        component, key=lambda row: (str(row.records_path), row.record_index)
    )
    representative = ordered[0]
    output_dir = (
        root
        / representative.lane
        / representative.source_object_id
        / representative.lane
        / "tile_merged"
    )
    identity = json.dumps(
        sorted(f"{row.records_path}:{row.record_index}" for row in ordered),
        sort_keys=True,
    )
    digest = sha256(identity.encode()).hexdigest()[:24]
    candidate_id = f"tile-merged:{digest}"
    masks_and_boxes = [
        (load_mask_array(row.mask_path), row.bbox_xyxy) for row in ordered
    ]
    union_array, union_bbox = mask_union_at_boxes(masks_and_boxes)
    mask_relative = f"masks/{digest}.png"
    overlay_relative = f"overlays/{digest}{representative.overlay_path.suffix}"
    write_mask_png(union_array, output_dir / mask_relative)
    (output_dir / overlay_relative).parent.mkdir(parents=True, exist_ok=True)
    copyfile(representative.overlay_path, output_dir / overlay_relative)
    record: JsonRecord = {
        "accepted": True,
        "candidate_id": candidate_id,
        "prompt": representative.prompt_text,
        "image": representative.image_id,
        "generation_lane": representative.generation_lane,
        "prompt_pack_id": representative.prompt_pack_id,
        "score": max(row.score for row in ordered),
        "bbox_xyxy": list(union_bbox),
        "mask_semantics": _MASK_SEMANTICS,
        "mask_path": mask_relative,
        "overlay_path": overlay_relative,
    }
    return output_dir / "records.json", record


# 병합에 참여한 원본 행들을 그 파일 안에서만 accepted=false로 고쳐 쓴다 -
# 다른 행/파일 구조는 그대로 둔다. 이후 어떤 리더도 records.json을
# 재파싱할 때 accepted=true만 보므로, 원본 타일 조각은 다시는 독립적인
# 후보로 나타나지 않는다.
def _suppress_rows(records_path: Path, indices: set[int]) -> None:
    decoded = decode_records(records_path.read_text(encoding="utf-8"))
    if decoded is None:
        return
    updated = tuple(
        {**record, "accepted": False, "reject_reason": "merged_into_tile_group"}
        if index in indices
        else record
        for index, record in enumerate(decoded)
    )
    _write_records(records_path, updated)


def _write_records(path: Path, records: Iterable[JsonRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        list(records), sort_keys=True, separators=(",", ":")
    )
    path.write_text(payload, encoding="utf-8")
