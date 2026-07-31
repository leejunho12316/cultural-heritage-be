from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class AssemblyConfig:
    # Segmentation
    reference_segmentation: str = "auto"  # auto | grabcut | border_distance
    reference_margin_ratio: float = 0.04
    reference_analysis_max_dimension: int = 1600
    reference_ignore_bottom_ratio: float = 0.0
    reference_remove_scale_bar: bool = True
    reference_scale_bar_min_y_ratio: float = 0.55
    reference_scale_bar_max_height_ratio: float = 0.08
    reference_scale_bar_min_width_ratio: float = 0.01
    reference_scale_bar_max_width_ratio: float = 0.35
    reference_scale_bar_max_saturation: float = 45.0
    min_component_area_ratio: float = 0.002
    fragment_border_width_ratio: float = 0.06
    fragment_morph_kernel: int = 3
    fragment_frame_border_px: int = 3

    # When the artifact fills an X-ray frame, ordinary foreground/background
    # thresholding can mistake a faint crack for the whole artifact.  The
    # decision is made only after the dominant X-ray polarity is inferred from
    # the complete fragment set.
    fragment_frame_fill_enabled: bool = True
    fragment_frame_fill_fraction: float = 0.97
    fragment_frame_fill_bright_threshold: int = 180
    fragment_frame_fill_dark_threshold: int = 75
    fragment_frame_fill_min_polarity_votes: int = 3


    # X-ray tile-to-tile rigid registration. Fragment pixels are never scaled.
    pair_analysis_max_dimension: int = 420
    pair_all_limit: int = 12
    pair_index_window: int = 3
    pair_min_score: float = 0.56
    pair_min_overlap: float = 0.10
    pair_fallback_min_score: float = 0.46
    pair_fallback_min_overlap: float = 0.05

    # SIFT + fixed-scale RANSAC candidate
    sift_features: int = 5000
    sift_contrast_threshold: float = 0.001
    sift_ratio_test: float = 0.78
    sift_ransac_threshold_px: float = 4.0
    sift_min_inliers: int = 5
    sift_min_scale: float = 0.96
    sift_max_scale: float = 1.04
    sift_early_accept_score: float = 0.72

    # Rotation search + phase correlation fallback
    phase_angle_radius_deg: int = 15
    phase_angle_step_deg: int = 5
    phase_full_rotation_step_deg: int = 0
    frame_continuation_overlap_px: int = 24
    # Legacy continuation score is retained for short/simple acquisitions.
    frame_continuation_score: float = 0.74
    # Long scanner sequences use evidence-based boundary scoring instead of
    # the legacy unconditional continuation prior.
    frame_boundary_base_score: float = 0.0
    # Weak scanner-tile continuation must not invent a large relative rotation.
    # Large rotations require image evidence (normally SIFT/RANSAC).
    frame_same_direction_max_deg: float = 4.0
    frame_orientation_penalty_per_deg: float = 0.006
    frame_min_accept_score: float = 0.45
    frame_max_weak_overlap: float = 0.35
    frame_boundary_strip_px: int = 56
    frame_boundary_max_shift_ratio: float = 0.45
    frame_boundary_min_tangent_overlap_ratio: float = 0.50
    frame_boundary_min_variance: float = 0.002

    # Consecutive scanner frames are often captured as a raster/serpentine
    # path.  Candidate directions are therefore resolved jointly instead of
    # selecting each pair independently.
    frame_sequence_enabled: bool = True
    frame_sequence_beam_width: int = 96
    frame_sequence_same_direction_bonus: float = 0.16
    frame_sequence_serpentine_bonus: float = 0.14
    frame_sequence_turn_bonus: float = 0.04
    frame_sequence_reverse_penalty: float = 0.34
    frame_sequence_repeated_vertical_penalty: float = 0.14
    frame_sequence_collision_penalty: float = 0.30
    frame_sequence_low_confidence_threshold: float = 0.52
    frame_sequence_min_chain_length: int = 8
    # A low-evidence adjacent pair separates two scanner runs instead of
    # forcing an artificial continuation through an independent fragment.
    frame_sequence_break_score: float = 0.52

    # Search a wider index window for image-supported SIFT anchors only.
    # These non-adjacent anchors connect overlapping rows and provide cycles
    # that can replace weak, frame-size-based continuation edges.
    frame_extended_sift_enabled: bool = True
    frame_extended_sift_window: int = 6
    frame_extended_sift_min_inliers: int = 8
    frame_extended_sift_min_inlier_ratio: float = 0.45
    frame_extended_sift_min_intensity_ncc: float = 0.45
    frame_extended_sift_min_gradient_ncc: float = 0.04
    frame_cycle_translation_tolerance_px: float = 55.0
    frame_cycle_rotation_tolerance_deg: float = 3.0
    frame_cycle_inconsistent_edge_penalty: float = 0.20

    # Robust rigid pose-graph refinement.  It uses all accepted SIFT anchors
    # plus low-weight continuation edges and never changes fragment scale.
    frame_pose_graph_enabled: bool = True
    frame_pose_graph_iterations: int = 4
    frame_pose_graph_prior_weight: float = 0.02
    frame_pose_graph_weak_edge_weight: float = 0.25
    frame_pose_graph_translation_huber_px: float = 80.0
    frame_pose_graph_rotation_huber_deg: float = 4.0
    # Pose-graph refinement is useful only when image-supported edges cover most
    # of the component.  Otherwise the graph can be self-consistent but still
    # geometrically wrong.
    frame_pose_graph_min_strong_edges: int = 3
    frame_pose_graph_min_strong_node_coverage: float = 0.60
    # When a sufficiently large scanner component has several image-supported
    # edges whose relative rotations are consistently small, keep individual
    # frames near one shared acquisition orientation.  The whole component may
    # still rotate freely during color-reference alignment.
    frame_pose_graph_orientation_lock_min_fragments: int = 14
    frame_pose_graph_orientation_lock_min_sift_edges: int = 4
    frame_pose_graph_orientation_lock_max_edge_deg: float = 5.0
    frame_pose_graph_orientation_lock_weight: float = 0.85

    # Whole stitched X-ray mosaic to color-reference alignment
    global_angle_radius_deg: int = 30
    global_angle_step_deg: int = 5
    global_refine_angle_step_deg: float = 1.0
    global_full_rotation_step_deg: int = 0
    global_alignment_max_dimension: int = 520

    # Reference-to-X-ray scale. Only the reference mask is scaled.
    reference_fill_ratio: float = 0.97
    reference_scale_override: float | None = None
    canvas_margin_ratio: float = 0.10
    max_output_dimension: int = 10000

    # Search resolution and initialization
    search_max_dimension: int = 220
    greedy_angle_step_deg: int = 30
    greedy_position_step_px: int = 10
    greedy_max_positions: int = 500

    # Simulated annealing
    anneal_iterations: int = 1200
    anneal_restarts: int = 3
    anneal_initial_temperature: float = 0.20
    anneal_final_temperature: float = 0.002
    anneal_translation_sigma_px: float = 9.0
    anneal_rotation_sigma_deg: float = 15.0

    # Deterministic local refinement
    refine_translation_steps_px: tuple[int, ...] = (4, 2, 1)
    refine_rotation_steps_deg: tuple[int, ...] = (4, 2, 1)
    refine_passes_per_level: int = 2

    # Objective weights
    weight_iou: float = 3.0
    weight_outside: float = 5.0
    weight_overlap: float = 7.0
    weight_missing: float = 1.5
    weight_boundary: float = 1.0
    boundary_tolerance_px: int = 2

    # Data-semantic routing. User selections are strong priors, but high-confidence
    # image evidence may override a conflicting selection and force review.
    route_fragment_array_min_components: int = 20
    route_fragment_array_max_largest_fraction: float = 0.35
    route_image_override_confidence: float = 0.90

    # Fragment-array mode. Registration accepts only direct image evidence; weak
    # filename/sequence continuation cannot connect separate physical fragments.
    fragment_array_min_sift_inliers: int = 7
    fragment_array_min_sift_inlier_ratio: float = 0.45
    fragment_array_min_registration_overlap: float = 0.08
    fragment_array_min_intensity_ncc: float = 0.45
    fragment_array_min_gradient_ncc: float = 0.16
    fragment_array_min_phase_score: float = 0.66
    fragment_array_min_phase_response: float = 0.02
    # Phase rotation search is bounded to moderate-size cases. Large arrays use
    # SIFT-only direct evidence to avoid quadratic temporary-memory growth.
    fragment_array_phase_max_fragments: int = 40
    # Reference raster is a coordinate guide only. Array scans can contain the
    # same physical material in several overlapping source frames, so raw summed
    # X-ray area can overestimate the required reference scale.
    fragment_array_component_scale_multiplier: float = 1.30
    fragment_array_reference_max_dimension: int = 6500
    fragment_array_alignment_max_dimension: int = 520
    fragment_array_full_rotation_step_deg: int = 30
    fragment_array_occupied_overlap_weight: float = 0.70
    fragment_array_min_inside_ratio: float = 0.52
    fragment_array_review_inside_ratio: float = 0.72
    fragment_array_max_occupied_overlap_ratio: float = 0.28
    fragment_array_min_candidate_score_margin: float = 0.02
    fragment_array_min_response_margin: float = 0.02
    fragment_array_reference_min_component_area_ratio: float = 0.001
    fragment_array_min_slot_coverage: float = 0.12
    fragment_array_packing_margin_px: int = 24
    fragment_array_packing_gap_px: int = 28
    fragment_array_packing_target_width_px: int = 0
    fragment_array_split_multi_object_sources: bool = True
    fragment_array_split_min_component_area_ratio: float = 0.020
    fragment_array_split_min_component_pixels: int = 24
    # In fragment-array reference mode, a foreground component that reaches the
    # original acquisition-frame boundary is treated as a partial-capture
    # candidate.  Registration pairs are generated only when at least one side
    # is such a candidate; two fully enclosed fragments remain independent.
    fragment_array_border_guided_registration: bool = True
    # After partial captures are consolidated, align each resulting X-ray
    # component to the color fragment-array silhouette.  Disallowed X-ray scale
    # changes are never introduced; only the reference raster may be scaled.
    fragment_array_reference_matching_enabled: bool = True
    # Optional stronger fragment-array placement mode: split the color reference
    # into per-fragment slots and match X-ray components to those slots before
    # any broad whole-array search.  This is intended for color images that are
    # themselves fragment arrays rather than complete restored silhouettes.
    fragment_array_color_slot_matching_enabled: bool = False
    fragment_array_color_slot_top_k: int = 4
    fragment_array_color_slot_max_shape_cost: float = 1.25
    fragment_array_color_slot_search_padding_ratio: float = 0.35
    fragment_array_color_slot_min_score: float = 0.10
    # Optional conservative approval layer for v9/fast color-slot matching.
    # Baseline geometric validity still generates candidates, but a candidate is
    # committed only when it clearly beats explicit unassignment and is not
    # ambiguous from either the X-ray-component or color-slot perspective.
    # Disabled by default to preserve all existing configs and routes.
    fragment_array_color_slot_conservative_assignment_enabled: bool = False
    fragment_array_color_slot_unassigned_score: float = 0.18
    fragment_array_color_slot_require_mutual_best: bool = True
    # Voronoi search regions surround each color-reference fragment with black
    # context while stopping at the nearest neighbouring fragment.  Shape
    # comparison still uses the exact fragment mask; the polygon only bounds
    # translation/rotation search and never participates in X-ray capture-role
    # classification.
    fragment_array_color_slot_voronoi_search_enabled: bool = False
    fragment_array_color_slot_voronoi_expansion_ratio: float = 0.35
    fragment_array_color_slot_voronoi_min_expansion_px: int = 18
    fragment_array_color_slot_voronoi_max_expansion_px: int = 120
    fragment_array_color_slot_voronoi_min_inside_ratio: float = 0.90
    fragment_array_color_slot_voronoi_polygon_epsilon_ratio: float = 0.008
    # Fragment-array references can contain low-contrast lower-row fragments
    # that a global Otsu threshold drops.  This optional route-specific mask
    # rebuild uses a fixed Lab distance from the reference border background,
    # removes scale bars / thin frame artifacts, and keeps every detached color
    # fragment before Voronoi slot generation.
    fragment_array_full_reference_segmentation_enabled: bool = False
    fragment_array_full_reference_analysis_max_dimension: int = 2200
    fragment_array_full_reference_border_distance_threshold: float = 7.0
    fragment_array_full_reference_min_component_area_ratio: float = 0.003
    fragment_array_full_reference_min_component_pixels: int = 50
    fragment_array_full_reference_border_artifact_max_thickness_ratio: float = 0.006
    # Accuracy-focused v10 options.  The master switch remains disabled by
    # default so the previous v9 behavior is restored by using its old config.
    fragment_array_color_slot_accuracy_v10_enabled: bool = False
    fragment_array_color_slot_robust_scale_enabled: bool = False
    fragment_array_color_slot_anchor_count: int = 8
    fragment_array_color_slot_anchor_max_shape_cost: float = 0.55
    fragment_array_color_slot_chamfer_weight: float = 0.0
    fragment_array_color_slot_mutual_best_enabled: bool = False
    fragment_array_color_slot_min_bundle_margin: float = 0.025
    fragment_array_color_slot_min_slot_margin: float = 0.015
    fragment_array_color_slot_refine_max_dimension: int = 0
    fragment_array_pair_neighbors: int = 6
    fragment_array_pair_area_ratio_limit: float = 4.0
    fragment_array_pair_aspect_ratio_limit: float = 4.0
    fragment_array_large_pair_threshold: int = 180
    fragment_array_large_pair_neighbors: int = 2
    fragment_array_large_source_index_window: int = 14
    # Two or more fragments from the same pair of source frames must agree on
    # one translation before texture-poor duplicate observations are linked.
    fragment_array_source_consensus_enabled: bool = True
    fragment_array_source_consensus_window: int = 8
    fragment_array_source_consensus_translation_tolerance_px: float = 28.0
    fragment_array_source_consensus_min_matches: int = 2
    fragment_array_source_consensus_max_shape_distance: float = 0.28
    fragment_array_source_consensus_max_area_ratio: float = 2.40
    fragment_array_source_consensus_min_overlap: float = 0.72
    fragment_array_source_consensus_min_intensity_ncc: float = 0.15
    fragment_array_source_consensus_min_gradient_ncc: float = 0.10

    # Optional v14 source-frame transform propagation.  A direct rigid image
    # registration between two source frames defines one scanner-frame transform.
    # The same transform is then verified against every other foreground object
    # in those two source frames.  At least two one-to-one object correspondences
    # must support the transform, so one visually similar fragment cannot connect
    # two unrelated frames by itself.  Disabled by default for regression safety.
    fragment_array_source_transform_propagation_enabled: bool = False
    fragment_array_source_transform_max_source_gap: int = 14
    fragment_array_source_transform_rotation_tolerance_deg: float = 2.5
    fragment_array_source_transform_translation_tolerance_px: float = 24.0
    fragment_array_source_transform_single_anchor_min_inliers: int = 12
    fragment_array_source_transform_single_anchor_min_inlier_ratio: float = 0.55
    fragment_array_source_transform_min_verified_matches: int = 2
    fragment_array_source_transform_max_area_ratio: float = 3.00
    fragment_array_source_transform_min_overlap: float = 0.58
    fragment_array_source_transform_min_intensity_ncc: float = 0.12
    fragment_array_source_transform_min_gradient_ncc: float = 0.08
    fragment_array_source_transform_min_match_margin: float = 0.04

    # Optional v14 boundary-profile consensus for texture-poor split scans.
    # Only component contact with the original X-ray frame is used.  A weak
    # filename sequence is never sufficient: two component correspondences from
    # the same source-frame pair must agree on one cardinal translation, or one
    # boundary correspondence must agree with a direct SIFT source transform.
    fragment_array_boundary_consensus_enabled: bool = False
    fragment_array_boundary_source_window: int = 8
    fragment_array_boundary_max_area_ratio: float = 6.0
    fragment_array_boundary_min_score: float = 0.58
    fragment_array_boundary_min_reliability: float = 0.50
    fragment_array_boundary_min_ncc: float = 0.10
    fragment_array_boundary_min_gradient_ncc: float = 0.05
    fragment_array_boundary_min_occupancy_ncc: float = 0.10
    fragment_array_boundary_min_matches: int = 2
    fragment_array_boundary_translation_tolerance_px: float = 36.0
    fragment_array_boundary_tangent_search_radius_px: int = 48
    fragment_array_boundary_anchor_translation_tolerance_px: float = 48.0
    fragment_array_boundary_anchor_rotation_tolerance_deg: float = 3.0

    # Optional v15 reference-anchored source-frame placement.  Fragment-array
    # source images are not stitched to one another first.  Every foreground
    # object from one original X-ray frame retains its source-frame coordinates
    # and shares one rigid transform into the color-reference coordinate system.
    # Several source frames may therefore support the same physical color slot.
    # Disabled by default so existing complete/split/mixed routes are unchanged.
    fragment_array_reference_anchored_partial_enabled: bool = False
    fragment_array_reference_anchored_angle_step_deg: int = 45
    fragment_array_reference_anchored_refine_angle_step_deg: float = 3.0
    fragment_array_reference_anchored_alignment_max_dimension: int = 220
    fragment_array_reference_anchored_min_source_inside_ratio: float = 0.68
    fragment_array_reference_anchored_review_source_inside_ratio: float = 0.80
    fragment_array_reference_anchored_min_boundary_precision: float = 0.08
    fragment_array_reference_anchored_min_multi_response_margin: float = 0.004
    fragment_array_reference_anchored_min_single_response_margin: float = 0.025
    fragment_array_reference_anchored_min_fragment_inside_ratio: float = 0.58
    fragment_array_reference_anchored_min_independent_inside_ratio: float = 0.70
    fragment_array_reference_anchored_min_fragment_slot_margin: float = 0.10
    fragment_array_reference_anchored_min_supported_fraction: float = 0.50
    fragment_array_reference_anchored_min_supported_fragments: int = 2
    fragment_array_reference_anchored_single_min_inside_ratio: float = 0.84
    fragment_array_reference_anchored_single_min_boundary_precision: float = 0.14
    fragment_array_reference_anchored_min_slot_intersection_px: int = 24
    fragment_array_reference_anchored_parking_gap_px: int = 20

    # Complete-reference component assignment.  The legacy path greedily pairs
    # every X-ray component with a color component before checking the actual
    # rigid alignment, so a failed alignment (score=-1 / IoU=0) can still be
    # presented as assigned.  This optional path evaluates only a few low-cost
    # candidates, includes explicit unassignment, and commits only candidates
    # whose rigid alignment is independently acceptable.
    complete_reference_conservative_assignment_enabled: bool = False
    complete_reference_candidate_top_k: int = 3
    complete_reference_max_assignment_cost: float = 2.20
    complete_reference_min_alignment_score: float = 0.28
    complete_reference_min_alignment_iou: float = 0.30
    complete_reference_unassigned_utility: float = 0.18
    complete_reference_assignment_cost_weight: float = 0.08

    # Complete body + detached fragments mode. The dominant X-ray registration
    # component is aligned to the dominant color-reference component first.
    # Remaining X-ray components can only compete for detached reference slots;
    # low-confidence components remain explicitly unassigned for Konva/HITL.
    mixed_reference_min_component_area_ratio: float = 0.001
    mixed_body_min_dominance_ratio: float = 1.25
    mixed_min_alignment_score: float = 0.18
    mixed_review_alignment_score: float = 0.42
    mixed_min_inside_ratio: float = 0.52
    mixed_review_inside_ratio: float = 0.72
    mixed_max_assignment_cost: float = 2.60
    mixed_min_candidate_score_margin: float = 0.025
    mixed_occupied_overlap_weight: float = 0.55
    mixed_max_occupied_overlap_ratio: float = 0.28
    # v7 body-first path.  Source frames are preclassified with image-derived
    # fill/extent/border-contact evidence before expensive registration.  Only
    # body-like consecutive runs are registered initially; detached sources
    # remain independent unless later reference evidence is strong.
    mixed_body_preclassification_enabled: bool = True
    mixed_body_registration_max_dimension: int = 180
    mixed_body_registration_sift_features: int = 1600
    mixed_body_registration_phase_angle_radius_deg: int = 0
    mixed_body_registration_min_run_length: int = 2
    mixed_body_registration_pair_index_window: int = 1
    mixed_body_run_score_drop_threshold: float = 0.14
    mixed_body_run_low_score_margin: float = 0.10
    mixed_secondary_body_min_source_fraction: float = 0.50
    mixed_secondary_body_min_alignment_score: float = 0.30
    mixed_secondary_body_min_inside_ratio: float = 0.62
    mixed_secondary_body_max_overlap_ratio: float = 0.58
    mixed_secondary_body_min_new_coverage_ratio: float = 0.12
    mixed_detached_min_alignment_score: float = 0.30
    mixed_detached_min_inside_ratio: float = 0.60
    mixed_detached_max_assignment_cost: float = 2.20
    mixed_detached_candidate_limit: int = 3
    mixed_component_alignment_max_dimension: int = 260
    mixed_component_alignment_angle_radius_deg: int = 10
    mixed_component_alignment_angle_step_deg: int = 10
    mixed_component_alignment_refine_step_deg: float = 2.0
    mixed_large_assignment_greedy_threshold: int = 16
    mixed_review_panel_gap_px: int = 24

    # Review thresholds do not alter the generated pixels or transforms.  They
    # only identify results that are too uncertain to present as an automatic
    # initial placement without human inspection.
    review_min_global_iou: float = 0.60
    review_min_boundary_f1: float = 0.03

    # Rendering. Final X-ray fragments are never scaled.
    final_interpolation: str = "nearest"
    overlap_policy: str = "overwrite_last"  # overwrite_last | keep_first
    random_seed: int = 42

    @classmethod
    def from_json(cls, path: str | Path | None) -> "AssemblyConfig":
        if path is None:
            return cls()
        with Path(path).open("r", encoding="utf-8") as f:
            raw: dict[str, Any] = json.load(f)
        tuple_fields = {
            "refine_translation_steps_px",
            "refine_rotation_steps_deg",
        }
        for key in tuple_fields:
            if key in raw:
                raw[key] = tuple(raw[key])
        unknown = sorted(set(raw) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"지원하지 않는 config 항목: {', '.join(unknown)}")
        return cls(**raw)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["refine_translation_steps_px"] = list(self.refine_translation_steps_px)
        data["refine_rotation_steps_deg"] = list(self.refine_rotation_steps_deg)
        return data
