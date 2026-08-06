"""Deterministic JSONL writers for candidate RAG sidecars."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from modules.rag.operations.candidate_sidecar_models import (
    RAG_CANDIDATE_EVIDENCE_SIDECAR,
    RAG_CANDIDATE_SIDECAR_MANIFEST,
    RAG_VISUAL_CONCEPT_CARDS_SIDECAR,
    CandidateRagEvidenceRow,
    CandidateRagSidecarResult,
    JsonObject,
)
from modules.shared import (
    ensure_no_symlink_leaf,
    ensure_source_document_is_not_write_target,
)

if TYPE_CHECKING:
    from pathlib import Path

    from modules.rag.evidence.concept_cards import RagVisualConceptCard


def write_candidate_rag_sidecars(
    output_dir: Path,
    result: CandidateRagSidecarResult,
    source_document_root: Path | None = None,
) -> None:
    """Write deterministic JSONL sidecars plus a small manifest."""
    if source_document_root is not None:
        output_dir = ensure_source_document_is_not_write_target(
            source_document_root, output_dir
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        output_dir / RAG_CANDIDATE_EVIDENCE_SIDECAR,
        tuple(_evidence_payload(row) for row in result.evidence_rows),
    )
    _write_jsonl(
        output_dir / RAG_VISUAL_CONCEPT_CARDS_SIDECAR,
        tuple(_card_payload(card) for card in result.cards),
    )
    manifest = {
        "rag_candidate_evidence_rows": len(result.evidence_rows),
        "rag_visual_concept_cards": len(result.cards),
        "schema": "rag_candidate_evidence_v1",
    }
    _write_text_atomic(
        output_dir / RAG_CANDIDATE_SIDECAR_MANIFEST,
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
    )


def _write_jsonl(path: Path, rows: tuple[JsonObject, ...]) -> None:
    payload = "" if not rows else "\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows
    ) + "\n"
    _write_text_atomic(path, payload)


def _write_text_atomic(path: Path, payload: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    _ = ensure_no_symlink_leaf(path, "sidecar leaf is a symlink")
    _ = ensure_no_symlink_leaf(temporary, "sidecar leaf is a symlink")
    try:
        _ = temporary.write_text(payload, encoding="utf-8")
        _ = temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise

def _evidence_payload(row: CandidateRagEvidenceRow) -> JsonObject:
    return {
        "evidence_reason": row.evidence_reason,
        "evidence_state": row.evidence_state,
        "lane": row.lane,
        "matched_chunk_ids": list(row.matched_chunk_ids),
        "matched_citation_ids": list(row.matched_citation_ids),
        "prompt_text": row.prompt_text,
        "query_id": row.query_id,
        "rag_parent_candidate_id": row.rag_parent_candidate_id,
        "rough_record_index": row.rough_record_index,
        "rough_record_path": row.rough_record_path,
        "schema": "rag_candidate_evidence_v1",
        "top_chunk_id": row.top_chunk_id,
        "top_citation_id": row.top_citation_id,
        "top_result_rank": row.top_result_rank,
        "top_retrieval_score": row.top_retrieval_score,
    }


def _card_payload(card: RagVisualConceptCard) -> JsonObject:
    return {
        "concept_card_id": card.concept_card_id,
        "concept_family": (
            card.concept_family.value if card.concept_family is not None else None
        ),
        "context_terms": list(card.context_terms),
        "descriptor_terms": list(card.descriptor_terms),
        "material_terms": list(card.material_terms),
        "provenance_strength": card.provenance_strength,
        "rag_parent_candidate_id": card.rag_parent_candidate_id,
        "raw_retrieved_sentence": card.raw_retrieved_sentence,
        "retrieval_score": card.retrieval_score,
        "source_citation_ids": list(card.source_citation_ids),
        "visual_cue": {
            "boundary_relation": card.visual_cue.boundary_relation.value,
            "color_bucket": card.visual_cue.color_bucket.value,
            "confidence": card.visual_cue.confidence,
            "morphology": card.visual_cue.morphology.value,
            "reasons": list(card.visual_cue.reasons),
            "size_class": card.visual_cue.size_class.value,
            "texture_proxy": card.visual_cue.texture_proxy.value,
        },
    }
