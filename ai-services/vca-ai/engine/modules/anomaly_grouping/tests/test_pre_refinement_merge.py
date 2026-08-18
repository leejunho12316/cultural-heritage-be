from __future__ import annotations

import json
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from modules.anomaly_grouping.pre_refinement_merge import merge_before_refinement
from modules.rag.operations.candidate_sidecar_artifacts import read_rough_records
from modules.visual_cue_generation.rough_records import rough_qwen_candidates

if TYPE_CHECKING:
    from pathlib import Path

_IMAGE_ID = "image-1"
_BRIDGE_SCHEMA = "qwen-rag-bridge-v1"


def _write_input_manifest(root: Path, image_id: str = _IMAGE_ID) -> Path:
    image_path = root / f"{image_id}.jpg"
    Image.new("RGB", (200, 200), (10, 20, 30)).save(image_path, format="JPEG")
    manifests_dir = root / "manifests"
    manifests_dir.mkdir(exist_ok=True)
    manifest_path = manifests_dir / "input_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {"images": [{"image_id": image_id, "run_root_asset_path": str(image_path)}]}
        ),
        encoding="utf-8",
    )
    return manifest_path


def _write_rough_candidate(  # noqa: PLR0913
    rough_root: Path,
    *,
    lane: str,
    object_id: str,
    view_segment: str,
    prompt: str,
    view_origin_xyxy: tuple[float, float, float, float],
    mask: np.ndarray,
) -> Path:
    """Write one accepted rough_masking candidate record with a real view image."""
    view_dir = rough_root / lane / object_id / lane / view_segment
    view_dir.mkdir(parents=True, exist_ok=True)
    (view_dir / "masks").mkdir(exist_ok=True)
    (view_dir / "overlays").mkdir(exist_ok=True)
    mask_bytes = (mask.astype(np.uint8)) * 255
    Image.fromarray(mask_bytes).save(view_dir / "masks" / "anomaly-0000.png")
    Image.fromarray(mask_bytes).convert("RGB").save(
        view_dir / "overlays" / "anomaly-0000.jpg", format="JPEG"
    )
    view_image_path = view_dir / "view.jpg"
    height, width = mask.shape
    Image.new("RGB", (width, height), (100, 100, 100)).save(
        view_image_path, format="JPEG"
    )
    record = {
        "accepted": True,
        "prompt": prompt,
        "image": _IMAGE_ID,
        "generation_lane": "owlv2" if lane == "owlv2_sam2" else "groundingdino",
        "prompt_pack_id": "static-seed-minimal-v1",
        "score": 0.5,
        "bbox_xyxy": [0.0, 0.0, float(width), float(height)],
        "view_origin_xyxy": list(view_origin_xyxy),
        "view_image_path": str(view_image_path),
        "mask_semantics": "anomaly_region",
        "mask_path": "masks/anomaly-0000.png",
        "overlay_path": "overlays/anomaly-0000.jpg",
    }
    records_path = view_dir / "records.json"
    records_path.write_text(json.dumps([record]), encoding="utf-8")
    return records_path


def _write_qwen_bridge_result(
    qwen_dir: Path, candidate_id: str, selected_terms: tuple[str, ...]
) -> None:
    result_dir = qwen_dir / candidate_id
    result_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "candidate_id": candidate_id,
        "status": "success",
        "selected_terms": list(selected_terms),
        "extracted_descriptors": [],
        "confidence": 0.9,
        "reason": "fixture",
        "qwen_observation_id": "obs-1",
        "input_view_hashes": ["hash-1", "hash-2"],
        "failure_code": None,
        "schema": _BRIDGE_SCHEMA,
    }
    (result_dir / "qwen_bridge_result.jsonl").write_text(
        json.dumps(payload, sort_keys=True), encoding="utf-8"
    )


def _write_concept_card(cards_path: Path, candidate_id: str, family: str) -> None:
    row = {
        "concept_card_id": f"card-{candidate_id}",
        "concept_family": family,
        "context_terms": ["surface"],
        "descriptor_terms": [],
        "image_id": _IMAGE_ID,
        "material_terms": [],
        "provenance_strength": "strong",
        "rag_parent_candidate_id": candidate_id,
        "raw_retrieved_sentence": "fixture sentence",
        "retrieval_score": 0.9,
        "source_citation_ids": ["citation-1"],
        "visual_cue": {
            "boundary_relation": "unknown",
            "color_bucket": "unknown",
            "confidence": 0.6,
            "morphology": "unknown",
            "reasons": [],
            "size_class": "unknown",
            "texture_proxy": "unknown",
        },
    }
    cards_path.parent.mkdir(parents=True, exist_ok=True)
    existing = cards_path.read_text(encoding="utf-8") if cards_path.is_file() else ""
    cards_path.write_text(
        existing + json.dumps(row, sort_keys=True) + "\n", encoding="utf-8"
    )


def _mask_with_foreground(
    shape: tuple[int, int], region: tuple[int, int, int, int]
) -> np.ndarray:
    """Build a boolean mask array with foreground only inside `region` (y0,y1,x0,x1)."""
    array = np.zeros(shape, dtype=np.bool_)
    y0, y1, x0, x1 = region
    array[y0:y1, x0:x1] = True
    return array


def _candidate_id_for(rough_root: Path, records_path: Path) -> str:
    candidates = read_rough_records(rough_root)
    matches = [
        candidate
        for candidate in candidates
        if rough_root / candidate.rough_record_path == records_path
    ]
    assert len(matches) == 1
    return str(matches[0].candidate_id)


def test_merges_overlapping_masks_of_the_same_kind(tmp_path: Path) -> None:
    # Given: two candidates from different tiles of the same object whose
    # masks genuinely overlap in original-photo coordinates, both crack-family
    # with matching "line" morphology.
    rough_root = tmp_path / "rough"
    left_path = _write_rough_candidate(
        rough_root,
        lane="grounded_sam2",
        object_id="object-1",
        view_segment="tile-a",
        prompt="crack or fissure on the surface",
        view_origin_xyxy=(0.0, 0.0, 40.0, 40.0),
        mask=_mask_with_foreground((40, 40), (10, 30, 10, 30)),
    )
    right_path = _write_rough_candidate(
        rough_root,
        lane="grounded_sam2",
        object_id="object-1",
        view_segment="tile-b",
        prompt="crack or fissure on the surface",
        view_origin_xyxy=(20.0, 20.0, 60.0, 60.0),
        mask=_mask_with_foreground((40, 40), (0, 20, 0, 20)),
    )
    left_id = _candidate_id_for(rough_root, left_path)
    right_id = _candidate_id_for(rough_root, right_path)

    manifest_path = _write_input_manifest(tmp_path)
    qwen_dir = tmp_path / "rag" / "qwen_bridge_results"
    _write_qwen_bridge_result(qwen_dir, left_id, ("line",))
    _write_qwen_bridge_result(qwen_dir, right_id, ("line",))
    cards_path = tmp_path / "rag" / "rag_visual_concept_cards.jsonl"
    _write_concept_card(cards_path, left_id, "crack")
    _write_concept_card(cards_path, right_id, "crack")

    # When: pre-refinement merge runs.
    merge_before_refinement(rough_root, tmp_path, manifest_path, cards_path, qwen_dir)

    # Then: both original rows are suppressed, and exactly one merged
    # candidate survives project-wide.
    assert json.loads(left_path.read_text())[0]["accepted"] is False
    assert json.loads(right_path.read_text())[0]["accepted"] is False
    candidates = rough_qwen_candidates(rough_root, rough_root)
    assert len(candidates) == 1
    merged = candidates[0].candidate
    assert merged.source_tile_view_id is None
    assert merged.candidate_id.startswith("anomaly-merged:")

    # And: the rag concept card sidecar now has exactly one card, relabeled
    # to the merged candidate id.
    card_rows = [
        json.loads(line)
        for line in cards_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(card_rows) == 1
    assert card_rows[0]["rag_parent_candidate_id"] == str(merged.candidate_id)


def test_does_not_merge_when_concept_family_differs(tmp_path: Path) -> None:
    # Given: two candidates with genuinely overlapping masks, but one is
    # crack-family and the other is deposit-family.
    rough_root = tmp_path / "rough"
    left_path = _write_rough_candidate(
        rough_root,
        lane="owlv2_sam2",
        object_id="object-1",
        view_segment="tile-a",
        prompt="surface crack",
        view_origin_xyxy=(0.0, 0.0, 40.0, 40.0),
        mask=_mask_with_foreground((40, 40), (10, 30, 10, 30)),
    )
    right_path = _write_rough_candidate(
        rough_root,
        lane="owlv2_sam2",
        object_id="object-1",
        view_segment="tile-b",
        prompt="crusty deposit",
        view_origin_xyxy=(0.0, 0.0, 40.0, 40.0),
        mask=_mask_with_foreground((40, 40), (10, 30, 10, 30)),
    )
    left_id = _candidate_id_for(rough_root, left_path)
    right_id = _candidate_id_for(rough_root, right_path)

    manifest_path = _write_input_manifest(tmp_path)
    qwen_dir = tmp_path / "rag" / "qwen_bridge_results"
    cards_path = tmp_path / "rag" / "rag_visual_concept_cards.jsonl"
    _write_concept_card(cards_path, left_id, "crack")
    _write_concept_card(cards_path, right_id, "deposit")

    # When: pre-refinement merge runs.
    merge_before_refinement(rough_root, tmp_path, manifest_path, cards_path, qwen_dir)

    # Then: both rows remain accepted (untouched) - different kinds, no merge.
    assert json.loads(left_path.read_text())[0]["accepted"] is True
    assert json.loads(right_path.read_text())[0]["accepted"] is True


def test_does_not_merge_when_morphology_differs(tmp_path: Path) -> None:
    # Given: two candidates with overlapping masks and the same concept
    # family, but Qwen describes them with incompatible morphologies.
    rough_root = tmp_path / "rough"
    left_path = _write_rough_candidate(
        rough_root,
        lane="owlv2_sam2",
        object_id="object-1",
        view_segment="tile-a",
        prompt="surface crack",
        view_origin_xyxy=(0.0, 0.0, 40.0, 40.0),
        mask=_mask_with_foreground((40, 40), (10, 30, 10, 30)),
    )
    right_path = _write_rough_candidate(
        rough_root,
        lane="owlv2_sam2",
        object_id="object-1",
        view_segment="tile-b",
        prompt="surface crack",
        view_origin_xyxy=(0.0, 0.0, 40.0, 40.0),
        mask=_mask_with_foreground((40, 40), (10, 30, 10, 30)),
    )
    left_id = _candidate_id_for(rough_root, left_path)
    right_id = _candidate_id_for(rough_root, right_path)

    manifest_path = _write_input_manifest(tmp_path)
    qwen_dir = tmp_path / "rag" / "qwen_bridge_results"
    _write_qwen_bridge_result(qwen_dir, left_id, ("line",))
    _write_qwen_bridge_result(qwen_dir, right_id, ("broad",))
    cards_path = tmp_path / "rag" / "rag_visual_concept_cards.jsonl"
    _write_concept_card(cards_path, left_id, "crack")
    _write_concept_card(cards_path, right_id, "crack")

    # When: pre-refinement merge runs.
    merge_before_refinement(rough_root, tmp_path, manifest_path, cards_path, qwen_dir)

    # Then: both rows remain accepted (untouched).
    assert json.loads(left_path.read_text())[0]["accepted"] is True
    assert json.loads(right_path.read_text())[0]["accepted"] is True


def test_does_not_merge_when_bboxes_overlap_but_masks_do_not_touch(
    tmp_path: Path,
) -> None:
    # Given: two candidates whose *bboxes* overlap in original coordinates,
    # but whose actual foreground pixels land in disjoint regions once
    # restored - a naive bbox-only check would wrongly merge these.
    rough_root = tmp_path / "rough"
    left_path = _write_rough_candidate(
        rough_root,
        lane="owlv2_sam2",
        object_id="object-1",
        view_segment="tile-a",
        prompt="surface crack",
        view_origin_xyxy=(0.0, 0.0, 40.0, 40.0),
        mask=_mask_with_foreground((40, 40), (0, 10, 0, 10)),
    )
    right_path = _write_rough_candidate(
        rough_root,
        lane="owlv2_sam2",
        object_id="object-1",
        view_segment="tile-b",
        prompt="surface crack",
        view_origin_xyxy=(20.0, 20.0, 60.0, 60.0),
        mask=_mask_with_foreground((40, 40), (30, 40, 30, 40)),
    )
    left_id = _candidate_id_for(rough_root, left_path)
    right_id = _candidate_id_for(rough_root, right_path)

    manifest_path = _write_input_manifest(tmp_path)
    qwen_dir = tmp_path / "rag" / "qwen_bridge_results"
    _write_qwen_bridge_result(qwen_dir, left_id, ("line",))
    _write_qwen_bridge_result(qwen_dir, right_id, ("line",))
    cards_path = tmp_path / "rag" / "rag_visual_concept_cards.jsonl"
    _write_concept_card(cards_path, left_id, "crack")
    _write_concept_card(cards_path, right_id, "crack")

    # When: pre-refinement merge runs.
    merge_before_refinement(rough_root, tmp_path, manifest_path, cards_path, qwen_dir)

    # Then: bboxes overlapped but masks never touched - no merge.
    assert json.loads(left_path.read_text())[0]["accepted"] is True
    assert json.loads(right_path.read_text())[0]["accepted"] is True


def test_does_not_merge_non_overlapping_candidates(tmp_path: Path) -> None:
    rough_root = tmp_path / "rough"
    left_path = _write_rough_candidate(
        rough_root,
        lane="owlv2_sam2",
        object_id="object-1",
        view_segment="tile-a",
        prompt="surface crack",
        view_origin_xyxy=(0.0, 0.0, 20.0, 20.0),
        mask=_mask_with_foreground((20, 20), (0, 20, 0, 20)),
    )
    right_path = _write_rough_candidate(
        rough_root,
        lane="owlv2_sam2",
        object_id="object-1",
        view_segment="tile-b",
        prompt="surface crack",
        view_origin_xyxy=(150.0, 150.0, 170.0, 170.0),
        mask=_mask_with_foreground((20, 20), (0, 20, 0, 20)),
    )
    left_id = _candidate_id_for(rough_root, left_path)
    right_id = _candidate_id_for(rough_root, right_path)

    manifest_path = _write_input_manifest(tmp_path)
    qwen_dir = tmp_path / "rag" / "qwen_bridge_results"
    _write_qwen_bridge_result(qwen_dir, left_id, ("line",))
    _write_qwen_bridge_result(qwen_dir, right_id, ("line",))
    cards_path = tmp_path / "rag" / "rag_visual_concept_cards.jsonl"
    _write_concept_card(cards_path, left_id, "crack")
    _write_concept_card(cards_path, right_id, "crack")

    merge_before_refinement(rough_root, tmp_path, manifest_path, cards_path, qwen_dir)

    assert json.loads(left_path.read_text())[0]["accepted"] is True
    assert json.loads(right_path.read_text())[0]["accepted"] is True
