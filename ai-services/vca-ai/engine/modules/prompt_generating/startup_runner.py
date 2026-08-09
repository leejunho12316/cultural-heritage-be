"""Project-level startup runner for RAG refinement prompt artifacts."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, NoReturn, Protocol

from modules.prompt_generating import (
    RAG_REFINEMENT_PACK_ID,
    BoundaryRelation,
    ColorBucket,
    Morphology,
    PromptSafetyError,
    PromptVariant,
    SizeClass,
    TextureProxy,
    VisualConceptFamily,
    VisualCue,
    validate_unique_prompt_texts,
)
from modules.rag.evidence.concept_cards import RagVisualConceptCard
from modules.rag.evidence.prompt_adapter import render_rag_prompt_variants
from modules.rag.operations.candidate_sidecar_models import (
    RAG_VISUAL_CONCEPT_CARDS_SIDECAR,
)
from modules.rag.qwen.qwen_bridge_json import JsonObject, JsonValue, parse_json_object
from modules.shared import ContractValidationError, ExitCode, ensure_no_symlink_leaf

if TYPE_CHECKING:
    from pathlib import Path

    from modules.orchestration.stage_paths import StagePathMap


PROMPT_VARIANTS_SIDECAR = "rag_refinement_prompt_variants.jsonl"
PROMPT_GENERATING_MANIFEST = "manifest.json"
PROMPT_GENERATING_SCHEMA = "rag_refinement_prompt_variants_v1"


class _PromptGeneratingStageRequest(Protocol):
    @property
    def paths(self) -> StagePathMap: ...

    @property
    def dry_run(self) -> bool: ...


def run_prompt_generating_stage(request: _PromptGeneratingStageRequest) -> int:
    """Render mask-refining prompt variants from RAG concept cards."""
    try:
        cards_path = request.paths.rag / RAG_VISUAL_CONCEPT_CARDS_SIDECAR
        if request.dry_run and not cards_path.is_file():
            return int(ExitCode.OK)
        cards = _read_cards(cards_path)
        # 카드가 0장이어도(문헌 근거를 못 찾은 경우) 더 이상 여기서 전체 run을
        # 실패시키지 않는다 - RAG 근거 없이도 리포트는 나가야 한다는 결정.
        # mask_refining이 이 빈 variants를 받으면 정제할 게 없으니 rough 후보를
        # 그대로 통과시키고(passthrough_records.jsonl), anomaly_grouping이 그걸
        # 읽어 RAG 근거 없는 후보로나마 리포트에 반영한다.
        variants = tuple(
            variant for card in cards for variant in render_rag_prompt_variants(card)
        )
        validate_unique_prompt_texts(variants)
        _write_prompt_artifacts(request.paths.prompt_generating, cards, variants)
    except (ContractValidationError, OSError, PromptSafetyError) as error:
        print(  # noqa: T201
            f"prompt_generating: failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return int(ExitCode.INCOMPLETE_OR_FAILURE)
    return int(ExitCode.OK)


# rag 단계가 만든 concept card jsonl 파일을 줄 단위로 읽어 파싱한다.
# run_prompt_generating_stage에서 호출되며, 파일이 없으면
# ContractValidationError로 실패를 명확히 알린다.
def _read_cards(path: Path) -> tuple[RagVisualConceptCard, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as error:
        field = "rag_visual_concept_cards"
        reason = "file missing"
        raise ContractValidationError(field, reason) from error
    return tuple(_card(parse_json_object(line)) for line in lines if line.strip())


# JSON 레코드 한 줄을 RagVisualConceptCard로 변환한다. 각 필드는
# 아래의 _string/_strings/_visual_cue 등 검증 헬퍼에 위임한다.
def _card(record: JsonObject) -> RagVisualConceptCard:
    return RagVisualConceptCard(
        concept_card_id=_string(record, "concept_card_id"),
        rag_parent_candidate_id=_string(record, "rag_parent_candidate_id"),
        image_id=_string(record, "image_id"),
        concept_family=_concept_family(record.get("concept_family")),
        descriptor_terms=_strings(record, "descriptor_terms"),
        material_terms=_strings(record, "material_terms"),
        context_terms=_strings(record, "context_terms"),
        source_citation_ids=_strings(record, "source_citation_ids"),
        raw_retrieved_sentence=_string(record, "raw_retrieved_sentence"),
        visual_cue=_visual_cue(_object(record, "visual_cue")),
        retrieval_score=_float(record, "retrieval_score"),
        provenance_strength=_string(record, "provenance_strength"),
    )


# concept card에 중첩된 visual_cue 객체를 파싱한다. 각 enum 값이 닫힌
# 어휘집합을 벗어나면 ValueError를 ContractValidationError로 바꿔 올린다.
def _visual_cue(record: JsonObject) -> VisualCue:
    try:
        return VisualCue(
            color_bucket=ColorBucket(_string(record, "color_bucket")),
            morphology=Morphology(_string(record, "morphology")),
            texture_proxy=TextureProxy(_string(record, "texture_proxy")),
            size_class=SizeClass(_string(record, "size_class")),
            boundary_relation=BoundaryRelation(_string(record, "boundary_relation")),
            confidence=_float(record, "confidence"),
            reasons=_strings(record, "reasons"),
        )
    except ValueError as error:
        field = "visual_cue"
        reason = "unknown enum value"
        raise ContractValidationError(field, reason) from error


# PromptVariant를 jsonl 출력 스키마로 직렬화한다. 이 딕셔너리의 필드명은
# mask_refining의 execution/prompts.py가 그대로 다시 읽어 들이는 계약이므로
# 이름을 바꾸면 하위 단계가 깨진다.
def _prompt_payload(variant: PromptVariant) -> JsonObject:
    metadata = variant.metadata
    return {
        "concept_card_id": variant.concept_card_id,
        "generated_prompt": variant.generated_prompt,
        "generated_prompt_id": metadata.generated_prompt_id,
        "model_lane": metadata.model_lane.value,
        "model_prompt_variant": variant.model_prompt_variant,
        "prompt_pack_id": metadata.prompt_pack_id,
        "prompt_role": metadata.prompt_role.value,
        "rag_parent_candidate_id": variant.rag_parent_candidate_id,
        "source_citation_ids": list(metadata.source_citation_ids),
        "source_concept_family": variant.source_concept_family.value,
        "source_terms": list(metadata.source_terms),
    }


# 이 단계의 최종 산출물(프롬프트 변형 jsonl + manifest.json)을 함께
# 기록한다. run_prompt_generating_stage에서 호출되며, mask_refining이
# 읽는 prompt_generating 출력 디렉터리 계약을 이룬다.
def _write_prompt_artifacts(
    output_dir: Path,
    cards: tuple[RagVisualConceptCard, ...],
    variants: tuple[PromptVariant, ...],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        output_dir / PROMPT_VARIANTS_SIDECAR,
        tuple(_prompt_payload(variant) for variant in variants),
    )
    manifest: JsonObject = {
        "prompt_pack_id": RAG_REFINEMENT_PACK_ID,
        "prompt_variants": len(variants),
        "rag_visual_concept_cards": len(cards),
        "schema": PROMPT_GENERATING_SCHEMA,
    }
    _write_text_atomic(
        output_dir / PROMPT_GENERATING_MANIFEST,
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
    )


def _write_jsonl(path: Path, rows: tuple[JsonObject, ...]) -> None:
    payload = "" if not rows else "\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows
    ) + "\n"
    _write_text_atomic(path, payload)


# 임시 파일에 먼저 쓰고 replace로 교체하는 원자적 쓰기 패턴이다. 실패
# 시 임시 파일을 정리하고, 대상 경로가 심볼릭 링크가 아닌지도 먼저
# 검증해 안전하게 덮어쓴다.
def _write_text_atomic(path: Path, payload: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    _ = ensure_no_symlink_leaf(path, "prompt artifact leaf is a symlink")
    _ = ensure_no_symlink_leaf(temporary, "prompt artifact leaf is a symlink")
    try:
        _ = temporary.write_text(payload, encoding="utf-8")
        _ = temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _object(record: JsonObject, field: str) -> JsonObject:
    value = record.get(field)
    if not isinstance(value, dict):
        _raise_contract(field, "must be a JSON object")
    return value


def _string(record: JsonObject, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        _raise_contract(field, "must be a non-blank string")
    return value


def _strings(record: JsonObject, field: str) -> tuple[str, ...]:
    value = record.get(field)
    if not isinstance(value, list):
        _raise_contract(field, "must be a string array")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            _raise_contract(field, "must be a string array")
        items.append(item)
    return tuple(items)


def _float(record: JsonObject, field: str) -> float:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        _raise_contract(field, "must be numeric")
    return float(value)


def _concept_family(value: JsonValue | None) -> VisualConceptFamily | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _raise_contract("concept_family", "must be a string or null")
    try:
        return VisualConceptFamily(value)
    except ValueError as error:
        field = "concept_family"
        reason = "unknown enum value"
        raise ContractValidationError(field, reason) from error


def _raise_contract(field: str, reason: str) -> NoReturn:
    raise ContractValidationError(field, reason)
