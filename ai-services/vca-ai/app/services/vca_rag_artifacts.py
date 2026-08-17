from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.vca_artifacts import ReportArtifact, VcaReportArtifactError


RagArtifactModel = TypeVar("RagArtifactModel", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class VcaRagQuery:
    lane: str
    prompt_text: str
    query_id: str


@dataclass(frozen=True, slots=True)
class VcaRagRetrievalResult:
    chunk_id: str
    citation_id: str
    lane: str
    matched_terms: tuple[str, ...]
    page_number: int | None
    prompt_text: str
    query_id: str
    rank: int
    score: float
    snippet_text: str
    source_citation: str


@dataclass(frozen=True, slots=True)
class VcaRagEvidenceRow:
    evidence_state: str
    lane: str
    matched_citation_ids: tuple[str, ...]
    prompt_text: str
    query_id: str | None
    rag_parent_candidate_id: str
    top_citation_id: str | None
    top_retrieval_score: float | None


@dataclass(frozen=True, slots=True)
class VcaRagVisualConceptCard:
    concept_card_id: str
    concept_family: str | None
    context_terms: tuple[str, ...]
    descriptor_terms: tuple[str, ...]
    material_terms: tuple[str, ...]
    provenance_strength: str
    rag_parent_candidate_id: str
    raw_retrieved_sentence: str
    retrieval_score: float
    source_citation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VcaRagArtifacts:
    schema: Literal["rag_candidate_evidence_v1"]
    queries: tuple[VcaRagQuery, ...]
    retrieval_results: tuple[VcaRagRetrievalResult, ...]
    evidence_rows: tuple[VcaRagEvidenceRow, ...]
    visual_concept_cards: tuple[VcaRagVisualConceptCard, ...]


class RagSidecarManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    # alias 처리한 이유: 필드명을 그대로 `schema`로 두면 BaseModel의
    # (deprecated) .schema() 메서드를 가린다는 pydantic 경고가 발생한다.
    schema_: Literal["rag_candidate_evidence_v1"] = Field(alias="schema")
    rag_candidate_evidence_rows: int = Field(ge=0)
    rag_visual_concept_cards: int = Field(ge=0)


class RagQueryArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    lane: str = Field(min_length=1)
    prompt_text: str = Field(min_length=1)
    query_id: str = Field(min_length=1)


class RagRetrievalResultArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    chunk_id: str = Field(min_length=1)
    citation_id: str = Field(min_length=1)
    lane: str = Field(min_length=1)
    matched_terms: tuple[str, ...]
    page_number: int | None = None
    prompt_text: str = Field(min_length=1)
    query_id: str = Field(min_length=1)
    rank: int = Field(ge=1)
    score: float
    snippet_text: str = Field(min_length=1)
    source_citation: str = Field(min_length=1)


class RagEvidenceArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    evidence_state: str = Field(min_length=1)
    lane: str = Field(min_length=1)
    matched_citation_ids: tuple[str, ...]
    prompt_text: str = Field(min_length=1)
    query_id: str | None = None
    rag_parent_candidate_id: str = Field(min_length=1)
    top_citation_id: str | None = None
    top_retrieval_score: float | None = None


class RagVisualConceptCardArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    concept_card_id: str = Field(min_length=1)
    concept_family: str | None = None
    context_terms: tuple[str, ...]
    descriptor_terms: tuple[str, ...]
    material_terms: tuple[str, ...]
    provenance_strength: str = Field(min_length=1)
    rag_parent_candidate_id: str = Field(min_length=1)
    raw_retrieved_sentence: str = Field(min_length=1)
    retrieval_score: float
    source_citation_ids: tuple[str, ...]


# RAG 스테이지 산출물(queries/retrieval 결과/evidence/concept card)을 읽어
# VcaRagArtifacts로 조립한다. manifest 파일이 없으면 RAG 스테이지가 실행되지
# 않은 것으로 보고 None을 반환한다(선택적 산출물).
# vca_artifacts.load_vca_report()에서 호출된다.
def load_optional_vca_rag_artifacts(
    output_root: Path,
    project_name: str,
) -> VcaRagArtifacts | None:
    rag_root = output_root / "rag" / project_name
    manifest_path = rag_root / "rag_candidate_sidecars_manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = _read_required_rag_artifact(
        ReportArtifact(manifest_path, project_name, "RAG sidecar manifest"),
        RagSidecarManifest,
    )
    queries = _read_rag_jsonl(
        ReportArtifact(rag_root / "queries.jsonl", project_name, "RAG queries"),
        RagQueryArtifact,
    )
    retrieval_results = _read_rag_jsonl(
        ReportArtifact(
            rag_root / "prompt_rag_results.jsonl",
            project_name,
            "RAG prompt retrieval results",
        ),
        RagRetrievalResultArtifact,
    )
    evidence_rows = _read_rag_jsonl(
        ReportArtifact(
            rag_root / "rag_candidate_evidence.jsonl",
            project_name,
            "RAG candidate evidence",
        ),
        RagEvidenceArtifact,
    )
    visual_concept_cards = _read_rag_jsonl(
        ReportArtifact(
            rag_root / "rag_visual_concept_cards.jsonl",
            project_name,
            "RAG visual concept cards",
        ),
        RagVisualConceptCardArtifact,
    )
    _verify_manifest_counts(
        ReportArtifact(manifest_path, project_name, "RAG sidecar manifest"),
        manifest,
        evidence_rows,
        visual_concept_cards,
    )
    return VcaRagArtifacts(
        schema=manifest.schema_,
        queries=tuple(_rag_query(row) for row in queries),
        retrieval_results=tuple(_retrieval_result(row) for row in retrieval_results),
        evidence_rows=tuple(_evidence_row(row) for row in evidence_rows),
        visual_concept_cards=tuple(
            _visual_concept_card(row) for row in visual_concept_cards
        ),
    )


# RAG 단일 JSON 산출물을 읽어 pydantic 모델로 검증한다.
def _read_required_rag_artifact(
    artifact: ReportArtifact,
    model_type: type[RagArtifactModel],
) -> RagArtifactModel:
    try:
        payload = artifact.path.read_text(encoding="utf-8")
    except OSError as error:
        raise VcaReportArtifactError(
            artifact.project_name,
            f"required VCA report artifact is unavailable: {artifact.artifact_name}",
        ) from error
    try:
        return model_type.model_validate_json(payload)
    except ValidationError as error:
        raise _invalid_artifact_error(artifact) from error


# JSONL 파일을 한 줄씩 읽어 각 줄을 pydantic 모델로 검증한다. 빈 줄은
# 건너뛴다.
def _read_rag_jsonl(
    artifact: ReportArtifact,
    model_type: type[RagArtifactModel],
) -> tuple[RagArtifactModel, ...]:
    try:
        lines = artifact.path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise VcaReportArtifactError(
            artifact.project_name,
            f"required VCA report artifact is unavailable: {artifact.artifact_name}",
        ) from error
    try:
        return tuple(
            model_type.model_validate_json(line) for line in lines if line.strip()
        )
    except ValidationError as error:
        raise _invalid_artifact_error(artifact) from error


# manifest에 기록된 개수와 실제로 읽은 evidence/concept card 행 수가
# 일치하는지 검증한다. load_optional_vca_rag_artifacts()에서 호출된다.
def _verify_manifest_counts(
    artifact: ReportArtifact,
    manifest: RagSidecarManifest,
    evidence_rows: tuple[RagEvidenceArtifact, ...],
    visual_concept_cards: tuple[RagVisualConceptCardArtifact, ...],
) -> None:
    # manifest는 두 JSONL sidecar 파일을 모두 쓴 뒤 마지막에 쓰여지므로,
    # 개수가 어긋난다는 것은 정상적으로 비어있는 결과가 아니라 쓰기 도중
    # 깨진(partial/torn write) 상황을 뜻한다 - 파싱된 행을 그대로 신뢰하지
    # 않고 무효로 처리한다.
    if len(evidence_rows) != manifest.rag_candidate_evidence_rows:
        raise _invalid_artifact_error(artifact)
    if len(visual_concept_cards) != manifest.rag_visual_concept_cards:
        raise _invalid_artifact_error(artifact)


def _invalid_artifact_error(artifact: ReportArtifact) -> VcaReportArtifactError:
    return VcaReportArtifactError(
        artifact.project_name,
        f"required VCA report artifact is invalid: {artifact.artifact_name}",
    )


def _rag_query(row: RagQueryArtifact) -> VcaRagQuery:
    return VcaRagQuery(
        lane=row.lane,
        prompt_text=row.prompt_text,
        query_id=row.query_id,
    )


def _retrieval_result(row: RagRetrievalResultArtifact) -> VcaRagRetrievalResult:
    return VcaRagRetrievalResult(
        chunk_id=row.chunk_id,
        citation_id=row.citation_id,
        lane=row.lane,
        matched_terms=row.matched_terms,
        page_number=row.page_number,
        prompt_text=row.prompt_text,
        query_id=row.query_id,
        rank=row.rank,
        score=row.score,
        snippet_text=row.snippet_text,
        source_citation=row.source_citation,
    )


def _evidence_row(row: RagEvidenceArtifact) -> VcaRagEvidenceRow:
    return VcaRagEvidenceRow(
        evidence_state=row.evidence_state,
        lane=row.lane,
        matched_citation_ids=row.matched_citation_ids,
        prompt_text=row.prompt_text,
        query_id=row.query_id,
        rag_parent_candidate_id=row.rag_parent_candidate_id,
        top_citation_id=row.top_citation_id,
        top_retrieval_score=row.top_retrieval_score,
    )


def _visual_concept_card(
    row: RagVisualConceptCardArtifact,
) -> VcaRagVisualConceptCard:
    return VcaRagVisualConceptCard(
        concept_card_id=row.concept_card_id,
        concept_family=row.concept_family,
        context_terms=row.context_terms,
        descriptor_terms=row.descriptor_terms,
        material_terms=row.material_terms,
        provenance_strength=row.provenance_strength,
        rag_parent_candidate_id=row.rag_parent_candidate_id,
        raw_retrieved_sentence=row.raw_retrieved_sentence,
        retrieval_score=row.retrieval_score,
        source_citation_ids=row.source_citation_ids,
    )
