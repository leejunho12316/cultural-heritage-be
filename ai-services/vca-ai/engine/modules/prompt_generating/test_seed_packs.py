from __future__ import annotations

import pytest

from modules.prompt_generating import (
    PromptSafetyError,
    primary_refinement_role,
    static_fallback_ablation_pack,
    static_object_detection_pack,
    static_seed_minimal_pack,
)
from modules.shared import PromptRole, RagLane


def test_static_seed_pack_matches_locked_values_and_rough_anchor_contract() -> None:
    # Given: the locked rough-target seed prompt contract.
    expected = (
        (RagLane.OWLV2, "surface crack"),
        (RagLane.OWLV2, "spalled surface"),
        (RagLane.OWLV2, "dark stain"),
        (RagLane.OWLV2, "crusty deposit"),
        (RagLane.OWLV2, "corrosion pit"),
        (RagLane.GROUNDINGDINO, "crack or fissure on the surface"),
        (RagLane.GROUNDINGDINO, "flaking or spalling area"),
        (RagLane.GROUNDINGDINO, "dark discoloration or stain"),
        (RagLane.GROUNDINGDINO, "hard mineral deposit or accretion"),
        (RagLane.GROUNDINGDINO, "corrosion spot with pitting"),
        (RagLane.FLORENCE2, "a thin crack or fissure on the surface"),
        (RagLane.FLORENCE2, "an area of flaking, spalling or loss"),
        (RagLane.FLORENCE2, "a dark stain or area of discoloration"),
        (RagLane.FLORENCE2, "a crust of mineral deposit or accretion"),
        (RagLane.FLORENCE2, "a spot of corrosion or metal pitting"),
    )

    # When: rough masking reads the exported prompt records.
    actual = tuple(
        (record.metadata.model_lane, record.prompt_text)
        for record in static_seed_minimal_pack.records
    )

    # Then: every byte and rough-anchor provenance field remains locked.
    assert actual == expected
    assert static_seed_minimal_pack.prompt_role is PromptRole.STATIC_SEED
    for record in static_seed_minimal_pack.records:
        assert record.metadata.prompt_pack_id == "static-seed-minimal-v1"
        assert record.metadata.prompt_role is PromptRole.STATIC_SEED
        assert record.metadata.source_terms == (record.prompt_text,)
        assert record.metadata.source_citation_ids == ()
        assert record.rough_target_anchor_only
        assert not record.final_conservation_vocabulary
        assert not record.anomaly_class_proof


def test_object_detection_pack_matches_locked_values_without_rough_anchor() -> None:
    # Given: the locked object-detection seed prompt contract.
    expected = (
        (RagLane.OWLV2, "artifact object"),
        (RagLane.OWLV2, "individual artifact"),
        (RagLane.OWLV2, "whole artifact"),
        (RagLane.OWLV2, "separate object on white background"),
        (RagLane.OWLV2, "metal artifact on white background"),
        (RagLane.GROUNDINGDINO, "artifact object"),
        (RagLane.GROUNDINGDINO, "individual artifact"),
        (RagLane.GROUNDINGDINO, "whole artifact"),
        (RagLane.GROUNDINGDINO, "separate object on white background"),
        (RagLane.GROUNDINGDINO, "metal artifact on white background"),
        (RagLane.FLORENCE2, "artifact object"),
        (RagLane.FLORENCE2, "individual artifact"),
        (RagLane.FLORENCE2, "whole artifact"),
        (RagLane.FLORENCE2, "separate object on white background"),
        (RagLane.FLORENCE2, "metal artifact on white background"),
    )

    # When: preprocessing reads the exported object-detection prompt records.
    actual = tuple(
        (record.metadata.model_lane, record.prompt_text)
        for record in static_object_detection_pack.records
    )

    # Then: object prompts are distinct from rough-mask anchors.
    assert actual == expected
    assert static_object_detection_pack.prompt_pack_id == "static-object-detection-v1"
    assert static_object_detection_pack.prompt_role is PromptRole.STATIC_SEED
    for record in static_object_detection_pack.records:
        assert record.metadata.prompt_role is PromptRole.STATIC_SEED
        assert record.metadata.source_terms == (record.prompt_text,)
        assert not record.rough_target_anchor_only
        assert not record.final_conservation_vocabulary
        assert not record.anomaly_class_proof


def test_seed_prompt_ids_are_stable_and_unique() -> None:
    # Given: the immutable exported seed pack.
    records = static_seed_minimal_pack.records

    # When: generated prompt IDs are read twice.
    first_ids = tuple(record.metadata.generated_prompt_id for record in records)
    second_ids = tuple(record.metadata.generated_prompt_id for record in records)

    # Then: exact seed IDs are stable and no two records collide.
    assert first_ids == second_ids
    assert first_ids == (
        "static-seed-minimal-v1-owlv2-01",
        "static-seed-minimal-v1-owlv2-02",
        "static-seed-minimal-v1-owlv2-03",
        "static-seed-minimal-v1-owlv2-04",
        "static-seed-minimal-v1-owlv2-05",
        "static-seed-minimal-v1-groundingdino-01",
        "static-seed-minimal-v1-groundingdino-02",
        "static-seed-minimal-v1-groundingdino-03",
        "static-seed-minimal-v1-groundingdino-04",
        "static-seed-minimal-v1-groundingdino-05",
        "static-seed-minimal-v1-florence2-01",
        "static-seed-minimal-v1-florence2-02",
        "static-seed-minimal-v1-florence2-03",
        "static-seed-minimal-v1-florence2-04",
        "static-seed-minimal-v1-florence2-05",
    )
    assert len(first_ids) == len(set(first_ids))


def test_fallback_ablation_pack_requires_explicit_ablation_mode() -> None:
    # Given: the fallback pack's metadata role.
    fallback_role = static_fallback_ablation_pack.prompt_role

    # When: it is selected for primary refinement without an ablation declaration.
    # Then: the unsafe default is blocked while explicit ablation remains possible.
    with pytest.raises(PromptSafetyError, match="ablation"):
        _ = primary_refinement_role(fallback_role, ablation_mode=False)
    assert static_fallback_ablation_pack.records == ()
    assert (
        primary_refinement_role(fallback_role, ablation_mode=True)
        is PromptRole.STATIC_FALLBACK_ABLATION
    )
    assert (
        primary_refinement_role(PromptRole.RAG_REFINEMENT)
        is PromptRole.RAG_REFINEMENT
    )
