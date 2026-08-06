"""Strict reader for generated RAG refinement prompt artifacts."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from modules.mask_refining.execution.models import (
    PromptVariantGroup,
    PromptVariantReadResult,
    RefinementSkip,
    SkipStage,
)
from modules.prompt_generating import PromptVariant, VisualConceptFamily
from modules.rag.qwen.qwen_bridge_json import parse_json_object
from modules.rough_masking.artifacts.records import JsonRecord, decode_records
from modules.shared import ContractValidationError, PromptMetadata, PromptRole, RagLane

if TYPE_CHECKING:
    from pathlib import Path


class PromptArtifactInputError(ValueError):
    """Raised when a required prompt artifact file is not a valid input contract."""

    field: str
    reason: str

    def __init__(self, field: str, reason: str) -> None:
        """Store the failing field and machine-readable reason."""
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


def _manifest_schema(path: Path) -> str:
    try:
        decoded = parse_json_object(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        msg = "manifest.json"
        raise PromptArtifactInputError(msg, "file missing") from error
    except ContractValidationError as error:
        msg = "manifest.json"
        raise PromptArtifactInputError(msg, "malformed JSON") from error
    schema = decoded.get("schema")
    if not isinstance(schema, str) or not schema.strip():
        msg = "manifest.json.schema"
        raise PromptArtifactInputError(msg, "must be a non-blank string")
    return schema


def _string(record: JsonRecord, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PromptArtifactInputError(field, "must be a non-blank string")
    return value


def _strings(record: JsonRecord, field: str) -> tuple[str, ...]:
    value = record.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PromptArtifactInputError(field, "must be a string array")
    return tuple(item for item in value if isinstance(item, str))


def _variant(record: JsonRecord) -> PromptVariant:
    try:
        lane = RagLane(_string(record, "model_lane"))
        role = PromptRole(_string(record, "prompt_role"))
        family = VisualConceptFamily(_string(record, "source_concept_family"))
    except ValueError as error:
        msg = "prompt_variant"
        raise PromptArtifactInputError(msg, "unknown enum value") from error
    if role is not PromptRole.RAG_REFINEMENT:
        msg = "prompt_role"
        raise PromptArtifactInputError(msg, "must be rag_refinement")
    model_variant = _string(record, "model_prompt_variant")
    if model_variant != lane.value:
        msg = "model_prompt_variant"
        raise PromptArtifactInputError(msg, "must match model_lane")
    return PromptVariant(
        metadata=PromptMetadata(
            prompt_pack_id=_string(record, "prompt_pack_id"),
            prompt_role=role,
            model_lane=lane,
            generated_prompt_id=_string(record, "generated_prompt_id"),
            source_terms=_strings(record, "source_terms"),
            source_citation_ids=_strings(record, "source_citation_ids"),
        ),
        generated_prompt=_string(record, "generated_prompt"),
        model_prompt_variant=model_variant,
        concept_card_id=_string(record, "concept_card_id"),
        rag_parent_candidate_id=_string(record, "rag_parent_candidate_id"),
        source_concept_family=family,
    )


def read_prompt_variants(prompt_output_dir: Path) -> PromptVariantReadResult:
    """Read valid JSONL prompt variants and retain malformed rows as skips."""
    manifest_schema = _manifest_schema(prompt_output_dir / "manifest.json")
    records_path = prompt_output_dir / "rag_refinement_prompt_variants.jsonl"
    try:
        lines = records_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as error:
        msg = "rag_refinement_prompt_variants.jsonl"
        raise PromptArtifactInputError(msg, "file missing") from error
    grouped: defaultdict[tuple[str, RagLane], list[PromptVariant]] = defaultdict(list)
    skips: list[RefinementSkip] = []
    for line_number, line in enumerate(lines, start=1):
        decoded = decode_records(f"[{line}]")
        if decoded is None or len(decoded) != 1:
            skips.append(
                RefinementSkip(SkipStage.PROMPT_INPUT, "malformed_json", line_number)
            )
            continue
        try:
            variant = _variant(decoded[0])
        except PromptArtifactInputError as error:
            skips.append(
                RefinementSkip(SkipStage.PROMPT_INPUT, str(error), line_number)
            )
            continue
        grouped[(variant.rag_parent_candidate_id, variant.metadata.model_lane)].append(
            variant
        )
    groups = tuple(
        PromptVariantGroup(parent, lane, tuple(variants))
        for (parent, lane), variants in sorted(grouped.items())
    )
    return PromptVariantReadResult(manifest_schema, groups, tuple(skips))
