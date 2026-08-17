from __future__ import annotations

from typing import TYPE_CHECKING

from modules.anomaly_grouping import (
    AnomalyCandidate,
    BoundingBox,
    CandidateEvidence,
    merge_tile_split_candidates,
)
from modules.anomaly_grouping.tests.mask_test_support import rect_mask
from modules.shared import CandidateId, RagAccountingStatus

if TYPE_CHECKING:
    from pathlib import Path


def _candidate(  # noqa: PLR0913
    tmp_path: Path,
    candidate_id: str,
    bbox: BoundingBox,
    *,
    source_object_id: str = "object-1",
    source_tile_view_id: str | None,
    concept: str = "crack",
) -> AnomalyCandidate:
    return AnomalyCandidate(
        candidate_id=CandidateId(candidate_id),
        image_id="image-1",
        source_object_id=source_object_id,
        source_view_id="view-1",
        seed_lane="owlv2_sam2",
        seed_prompt="surface crack",
        bbox=bbox,
        mask=rect_mask(tmp_path, candidate_id, bbox),
        evidence=CandidateEvidence(
            concept_family=concept,
            concept_card_ids=(f"card-{concept}",),
            provenance_strength="strong",
            rag_status=RagAccountingStatus.COMPLETED,
        ),
        source_tile_view_id=source_tile_view_id,
    )


def test_merges_overlapping_candidates_from_different_tiles_of_one_object(
    tmp_path: Path,
) -> None:
    # Given: two candidates from different tiles of the same object, with
    # overlapping bboxes (as tiling's 0.33 overlap ratio would produce for a
    # single physical anomaly split across a tile boundary).
    left = _candidate(
        tmp_path,
        "b-left",
        BoundingBox(0, 0, 60, 60),
        source_tile_view_id="tile-a",
    )
    right = _candidate(
        tmp_path,
        "a-right",
        BoundingBox(40, 40, 100, 100),
        source_tile_view_id="tile-b",
    )

    # When: tile merge runs.
    merged = merge_tile_split_candidates((left, right), tmp_path)

    # Then: exactly one candidate survives, keyed by the deterministic
    # minimum candidate_id, with a mask/bbox covering both fragments.
    assert len(merged) == 1
    result = merged[0]
    assert str(result.candidate_id) == "a-right"
    assert result.bbox.x_min == 0
    assert result.bbox.y_min == 0
    assert result.bbox.x_max == 100
    assert result.bbox.y_max == 100


def test_does_not_merge_candidates_from_the_same_tile(tmp_path: Path) -> None:
    # Given: two overlapping candidates that both come from the *same* tile -
    # this is relations.py's job (it has real concept evidence to judge
    # with), not a tile-boundary artifact.
    left = _candidate(
        tmp_path,
        "same-tile-1",
        BoundingBox(0, 0, 60, 60),
        source_tile_view_id="tile-a",
    )
    right = _candidate(
        tmp_path,
        "same-tile-2",
        BoundingBox(40, 40, 100, 100),
        source_tile_view_id="tile-a",
    )

    # When: tile merge runs.
    merged = merge_tile_split_candidates((left, right), tmp_path)

    # Then: both candidates pass through unmerged.
    assert {str(c.candidate_id) for c in merged} == {"same-tile-1", "same-tile-2"}


def test_does_not_merge_candidates_without_a_tile_origin(tmp_path: Path) -> None:
    # Given: two overlapping candidates that both come straight from the
    # whole-object crop (no tiling involved) - merging these on overlap
    # alone would resurrect the old pre_rag.py's over-merging bug.
    left = _candidate(
        tmp_path,
        "object-crop-1",
        BoundingBox(0, 0, 60, 60),
        source_tile_view_id=None,
    )
    right = _candidate(
        tmp_path,
        "object-crop-2",
        BoundingBox(40, 40, 100, 100),
        source_tile_view_id=None,
    )

    # When: tile merge runs.
    merged = merge_tile_split_candidates((left, right), tmp_path)

    # Then: both candidates pass through unmerged.
    assert {str(c.candidate_id) for c in merged} == {
        "object-crop-1",
        "object-crop-2",
    }


def test_does_not_merge_non_overlapping_tiles(tmp_path: Path) -> None:
    # Given: two candidates from different tiles of the same object whose
    # bboxes do not actually overlap.
    left = _candidate(
        tmp_path,
        "far-left",
        BoundingBox(0, 0, 20, 20),
        source_tile_view_id="tile-a",
    )
    right = _candidate(
        tmp_path,
        "far-right",
        BoundingBox(150, 150, 170, 170),
        source_tile_view_id="tile-b",
    )

    # When: tile merge runs.
    merged = merge_tile_split_candidates((left, right), tmp_path)

    # Then: both candidates pass through unmerged.
    assert {str(c.candidate_id) for c in merged} == {"far-left", "far-right"}


def test_does_not_merge_across_different_objects(tmp_path: Path) -> None:
    # Given: two overlapping, different-tile candidates that belong to two
    # different physical objects (e.g. two separate fragments in one photo).
    left = _candidate(
        tmp_path,
        "object-a-candidate",
        BoundingBox(0, 0, 60, 60),
        source_object_id="object-a",
        source_tile_view_id="tile-1",
    )
    right = _candidate(
        tmp_path,
        "object-b-candidate",
        BoundingBox(40, 40, 100, 100),
        source_object_id="object-b",
        source_tile_view_id="tile-2",
    )

    # When: tile merge runs.
    merged = merge_tile_split_candidates((left, right), tmp_path)

    # Then: both candidates pass through unmerged.
    assert {str(c.candidate_id) for c in merged} == {
        "object-a-candidate",
        "object-b-candidate",
    }


def test_merge_survives_inconsistent_rag_evidence_between_tile_fragments(
    tmp_path: Path,
) -> None:
    # Given: the motivating scenario - one tile-side fragment got real RAG
    # evidence ("crack"), the other got none ("unknown"). Without tile
    # merge, relations.py's concept-match gate would keep these as two
    # separate, inconsistently-labeled findings for the same real damage.
    evidenced = _candidate(
        tmp_path,
        "a-evidenced",
        BoundingBox(0, 0, 60, 60),
        source_tile_view_id="tile-a",
        concept="crack",
    )
    unevidenced = _candidate(
        tmp_path,
        "b-unevidenced",
        BoundingBox(40, 40, 100, 100),
        source_tile_view_id="tile-b",
        concept="unknown",
    )

    # When: tile merge runs before relations.py ever sees these candidates.
    merged = merge_tile_split_candidates((evidenced, unevidenced), tmp_path)

    # Then: they collapse into one candidate regardless of the concept
    # mismatch - tile origin alone is sufficient justification here.
    assert len(merged) == 1
    assert merged[0].evidence.concept_family == "crack"
