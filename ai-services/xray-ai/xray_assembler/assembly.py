from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

from .config import AssemblyConfig
from .image_ops import (
    ensure_output_channels,
    normalize_preview,
    paste_center,
    principal_angle_deg,
    rotate_bound,
)
from .models import Fragment, Placement, ScoreBreakdown, SearchGeometry


def normalize_angle(angle_deg: float) -> float:
    return ((float(angle_deg) + 180.0) % 360.0) - 180.0


def build_search_geometry(
    reference_image: np.ndarray,
    reference_mask: np.ndarray,
    fragments: list[Fragment],
    config: AssemblyConfig,
) -> SearchGeometry:
    reference_area = int(np.count_nonzero(reference_mask))
    fragment_area = int(sum(f.mask_area for f in fragments))
    if reference_area <= 0 or fragment_area <= 0:
        raise ValueError("reference 또는 fragment 마스크 면적이 0입니다.")

    if config.reference_scale_override is not None:
        reference_scale = float(config.reference_scale_override)
    else:
        desired_reference_area = fragment_area / max(config.reference_fill_ratio, 1e-6)
        reference_scale = math.sqrt(desired_reference_area / reference_area)

    if not np.isfinite(reference_scale) or reference_scale <= 0:
        raise ValueError(f"잘못된 reference scale: {reference_scale}")

    src_h, src_w = reference_mask.shape
    scaled_w = max(1, int(round(src_w * reference_scale)))
    scaled_h = max(1, int(round(src_h * reference_scale)))
    max_dim = max(scaled_w, scaled_h)
    if max_dim > config.max_output_dimension:
        raise ValueError(
            f"예상 출력 크기 {scaled_w}x{scaled_h}가 max_output_dimension="
            f"{config.max_output_dimension}을 초과합니다. reference_scale_override를 지정하세요."
        )

    scaled_mask = cv2.resize(
        reference_mask,
        (scaled_w, scaled_h),
        interpolation=cv2.INTER_NEAREST,
    )
    reference_preview = normalize_preview(reference_image)
    scaled_preview = cv2.resize(
        reference_preview,
        (scaled_w, scaled_h),
        interpolation=cv2.INTER_AREA if reference_scale < 1.0 else cv2.INTER_LINEAR,
    )

    margin = max(8, int(round(max(scaled_w, scaled_h) * config.canvas_margin_ratio)))
    canvas_w = scaled_w + 2 * margin
    canvas_h = scaled_h + 2 * margin
    target_full = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
    target_full[margin : margin + scaled_h, margin : margin + scaled_w] = scaled_mask
    target_preview = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    target_preview[margin : margin + scaled_h, margin : margin + scaled_w] = scaled_preview

    opt_ratio = min(1.0, config.search_max_dimension / max(canvas_w, canvas_h))
    opt_w = max(32, int(round(canvas_w * opt_ratio)))
    opt_h = max(32, int(round(canvas_h * opt_ratio)))
    target_opt = cv2.resize(target_full, (opt_w, opt_h), interpolation=cv2.INTER_NEAREST)

    return SearchGeometry(
        target_mask_full=target_full,
        target_preview_full=target_preview,
        target_mask_opt=target_opt,
        search_scale_x=opt_w / canvas_w,
        search_scale_y=opt_h / canvas_h,
        canvas_width_full=canvas_w,
        canvas_height_full=canvas_h,
        reference_scale=reference_scale,
        target_offset_xy=(margin, margin),
    )


class RotatedMaskCache:
    def __init__(
        self,
        fragments: list[Fragment],
        geometry: SearchGeometry,
        angle_quantization_deg: float = 1.0,
    ) -> None:
        self.fragments = fragments
        self.angle_quantization_deg = max(0.25, float(angle_quantization_deg))
        self.base_masks: list[np.ndarray] = []
        for fragment in fragments:
            h, w = fragment.mask.shape
            opt_w = max(1, int(round(w * geometry.search_scale_x)))
            opt_h = max(1, int(round(h * geometry.search_scale_y)))
            resized = cv2.resize(
                fragment.mask,
                (opt_w, opt_h),
                interpolation=cv2.INTER_NEAREST,
            )
            self.base_masks.append((resized > 0).astype(np.uint8))
        self.cache: dict[tuple[int, int], np.ndarray] = {}

    def _angle_key(self, angle_deg: float) -> int:
        normalized = normalize_angle(angle_deg)
        return int(round(normalized / self.angle_quantization_deg))

    def get(self, fragment_index: int, angle_deg: float) -> np.ndarray:
        key = (fragment_index, self._angle_key(angle_deg))
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        actual_angle = key[1] * self.angle_quantization_deg
        rotated = rotate_bound(
            self.base_masks[fragment_index],
            actual_angle,
            interpolation=cv2.INTER_NEAREST,
            border_value=0,
        )
        rotated = (rotated > 0).astype(np.uint8)
        self.cache[key] = rotated
        return rotated


@dataclass
class ObjectiveContext:
    target: np.ndarray
    target_area: int
    target_edge: np.ndarray
    target_edge_dilated: np.ndarray
    kernel: np.ndarray
    cache: RotatedMaskCache
    config: AssemblyConfig


def make_objective_context(
    geometry: SearchGeometry,
    cache: RotatedMaskCache,
    config: AssemblyConfig,
) -> ObjectiveContext:
    target = (geometry.target_mask_opt > 0).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    target_edge = cv2.morphologyEx(target, cv2.MORPH_GRADIENT, kernel)
    tol = max(1, int(config.boundary_tolerance_px))
    tol_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * tol + 1, 2 * tol + 1))
    target_edge_dilated = cv2.dilate(target_edge, tol_kernel)
    return ObjectiveContext(
        target=target,
        target_area=int(target.sum()),
        target_edge=target_edge,
        target_edge_dilated=target_edge_dilated,
        kernel=kernel,
        cache=cache,
        config=config,
    )


def _add_patch_count(
    counts: np.ndarray,
    patch: np.ndarray,
    center_x: float,
    center_y: float,
) -> None:
    ph, pw = patch.shape
    x0 = int(round(center_x - (pw - 1) / 2.0))
    y0 = int(round(center_y - (ph - 1) / 2.0))
    x1 = x0 + pw
    y1 = y0 + ph
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(counts.shape[1], x1), min(counts.shape[0], y1)
    if cx0 >= cx1 or cy0 >= cy1:
        return
    px0, py0 = cx0 - x0, cy0 - y0
    px1, py1 = px0 + (cx1 - cx0), py0 + (cy1 - cy0)
    counts[cy0:cy1, cx0:cx1] += patch[py0:py1, px0:px1].astype(counts.dtype)


def render_counts(
    placements: list[Placement],
    ctx: ObjectiveContext,
) -> np.ndarray:
    counts = np.zeros(ctx.target.shape, dtype=np.uint16)
    for placement in placements:
        patch = ctx.cache.get(placement.fragment_index, placement.rotation_deg)
        _add_patch_count(counts, patch, placement.center_x, placement.center_y)
    return counts


def score_placements(
    placements: list[Placement],
    ctx: ObjectiveContext,
) -> ScoreBreakdown:
    counts = render_counts(placements, ctx)
    union = counts > 0
    target = ctx.target > 0
    intersection = int(np.count_nonzero(union & target))
    union_area = int(np.count_nonzero(union | target))
    iou = intersection / max(union_area, 1)

    total_layer_pixels = int(counts.sum())
    outside_pixels = int(counts[~target].sum())
    overlap_pixels = int(np.maximum(counts.astype(np.int32) - 1, 0).sum())
    missing_pixels = int(np.count_nonzero(target & ~union))
    outside_ratio = outside_pixels / max(total_layer_pixels, 1)
    overlap_ratio = overlap_pixels / max(total_layer_pixels, 1)
    missing_ratio = missing_pixels / max(ctx.target_area, 1)

    union_u8 = union.astype(np.uint8)
    union_edge = cv2.morphologyEx(union_u8, cv2.MORPH_GRADIENT, ctx.kernel)
    if int(union_edge.sum()) == 0 or int(ctx.target_edge.sum()) == 0:
        boundary_f1 = 0.0
    else:
        tol = max(1, int(ctx.config.boundary_tolerance_px))
        tol_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * tol + 1, 2 * tol + 1)
        )
        union_edge_dilated = cv2.dilate(union_edge, tol_kernel)
        precision = float((union_edge & ctx.target_edge_dilated).sum()) / max(
            int(union_edge.sum()), 1
        )
        recall = float((ctx.target_edge & union_edge_dilated).sum()) / max(
            int(ctx.target_edge.sum()), 1
        )
        boundary_f1 = 2.0 * precision * recall / max(precision + recall, 1e-9)

    cfg = ctx.config
    total = (
        cfg.weight_iou * (1.0 - iou)
        + cfg.weight_outside * outside_ratio
        + cfg.weight_overlap * overlap_ratio
        + cfg.weight_missing * missing_ratio
        + cfg.weight_boundary * (1.0 - boundary_f1)
    )
    return ScoreBreakdown(
        total=float(total),
        iou=float(iou),
        outside_ratio=float(outside_ratio),
        overlap_ratio=float(overlap_ratio),
        missing_ratio=float(missing_ratio),
        boundary_f1=float(boundary_f1),
    )


def _sample_candidate_positions(
    target: np.ndarray,
    step: int,
    max_positions: int,
    rng: random.Random,
) -> list[tuple[float, float]]:
    ys, xs = np.where(target > 0)
    if xs.size == 0:
        h, w = target.shape
        return [(w / 2.0, h / 2.0)]
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    positions: list[tuple[float, float]] = []
    for y in range(y0, y1 + 1, max(1, step)):
        for x in range(x0, x1 + 1, max(1, step)):
            if target[y, x] > 0:
                positions.append((float(x), float(y)))
    centroid = (float(xs.mean()), float(ys.mean()))
    positions.append(centroid)
    if len(positions) > max_positions:
        kept = rng.sample(positions[:-1], max_positions - 1)
        positions = kept + [centroid]
    return positions


def _local_candidate_score(
    patch: np.ndarray,
    center_x: float,
    center_y: float,
    target: np.ndarray,
    occupancy: np.ndarray,
    target_edge_dilated: np.ndarray,
) -> float:
    ph, pw = patch.shape
    x0 = int(round(center_x - (pw - 1) / 2.0))
    y0 = int(round(center_y - (ph - 1) / 2.0))
    x1, y1 = x0 + pw, y0 + ph
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(target.shape[1], x1), min(target.shape[0], y1)
    patch_area = max(int(patch.sum()), 1)
    if cx0 >= cx1 or cy0 >= cy1:
        return 1e9
    px0, py0 = cx0 - x0, cy0 - y0
    px1, py1 = px0 + (cx1 - cx0), py0 + (cy1 - cy0)
    local_patch = patch[py0:py1, px0:px1] > 0
    local_target = target[cy0:cy1, cx0:cx1] > 0
    local_occ = occupancy[cy0:cy1, cx0:cx1] > 0
    clipped_area = int(local_patch.sum())
    out_of_canvas = patch_area - clipped_area
    outside = out_of_canvas + int(np.count_nonzero(local_patch & ~local_target))
    overlap = int(np.count_nonzero(local_patch & local_occ))
    new_inside = int(np.count_nonzero(local_patch & local_target & ~local_occ))

    patch_u8 = local_patch.astype(np.uint8)
    edge = cv2.morphologyEx(
        patch_u8,
        cv2.MORPH_GRADIENT,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    )
    target_boundary = float(
        np.count_nonzero(edge & (target_edge_dilated[cy0:cy1, cx0:cx1] > 0))
    ) / max(int(edge.sum()), 1)

    if int(occupancy.sum()) > 0:
        occ_dilated = cv2.dilate(
            occupancy.astype(np.uint8),
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
        )
        adjacency = float(
            np.count_nonzero(edge & (occ_dilated[cy0:cy1, cx0:cx1] > 0))
        ) / max(int(edge.sum()), 1)
    else:
        adjacency = 0.0

    return (
        5.0 * outside / patch_area
        + 8.0 * overlap / patch_area
        - 1.5 * new_inside / patch_area
        - 1.2 * adjacency
        - 0.8 * target_boundary
    )


def greedy_initialize(
    fragments: list[Fragment],
    ctx: ObjectiveContext,
    config: AssemblyConfig,
) -> list[Placement]:
    rng = random.Random(config.random_seed)
    target_angle = principal_angle_deg((ctx.target * 255).astype(np.uint8))
    positions = _sample_candidate_positions(
        ctx.target,
        config.greedy_position_step_px,
        config.greedy_max_positions,
        rng,
    )
    occupancy = np.zeros_like(ctx.target, dtype=np.uint8)
    order = sorted(fragments, key=lambda f: f.mask_area, reverse=True)
    placements_by_index: dict[int, Placement] = {}

    for fragment in order:
        base = target_angle - fragment.principal_angle_deg
        angle_values = set()
        step = max(1, int(config.greedy_angle_step_deg))
        for angle in range(-180, 180, step):
            angle_values.add(int(angle))
        for offset in (-45, -30, -15, 0, 15, 30, 45, 90, 180, -90):
            angle_values.add(int(round(normalize_angle(base + offset))))

        best: tuple[float, float, float, float] | None = None
        for angle in sorted(angle_values):
            patch = ctx.cache.get(fragment.index, angle)
            for cx, cy in positions:
                value = _local_candidate_score(
                    patch,
                    cx,
                    cy,
                    ctx.target,
                    occupancy,
                    ctx.target_edge_dilated,
                )
                if best is None or value < best[0]:
                    best = (value, cx, cy, float(angle))

        if best is None:
            ys, xs = np.where(ctx.target > 0)
            cx = float(xs.mean()) if xs.size else ctx.target.shape[1] / 2.0
            cy = float(ys.mean()) if ys.size else ctx.target.shape[0] / 2.0
            placement = Placement(fragment.index, cx, cy, normalize_angle(base))
        else:
            _, cx, cy, angle = best
            placement = Placement(fragment.index, cx, cy, normalize_angle(angle))
        placements_by_index[fragment.index] = placement
        patch = ctx.cache.get(fragment.index, placement.rotation_deg)
        _add_patch_count(occupancy, patch, placement.center_x, placement.center_y)
        occupancy = (occupancy > 0).astype(np.uint8)

    return [placements_by_index[i] for i in range(len(fragments))]


def anneal_refine(
    placements: list[Placement],
    ctx: ObjectiveContext,
    config: AssemblyConfig,
) -> tuple[list[Placement], ScoreBreakdown]:
    rng = random.Random(config.random_seed + 17)
    current = [Placement(**vars(p)) for p in placements]
    current_score = score_placements(current, ctx)
    best = [Placement(**vars(p)) for p in current]
    best_score = current_score
    h, w = ctx.target.shape
    iterations = max(0, int(config.anneal_iterations))

    for i in range(iterations):
        progress = i / max(iterations - 1, 1)
        temperature = config.anneal_initial_temperature * (
            config.anneal_final_temperature / max(config.anneal_initial_temperature, 1e-9)
        ) ** progress
        sigma_scale = 0.15 + 0.85 * (1.0 - progress)
        index = rng.randrange(len(current))
        old = current[index]
        proposed = Placement(
            fragment_index=old.fragment_index,
            center_x=float(
                np.clip(
                    old.center_x
                    + rng.gauss(0.0, config.anneal_translation_sigma_px * sigma_scale),
                    0,
                    w - 1,
                )
            ),
            center_y=float(
                np.clip(
                    old.center_y
                    + rng.gauss(0.0, config.anneal_translation_sigma_px * sigma_scale),
                    0,
                    h - 1,
                )
            ),
            rotation_deg=normalize_angle(
                old.rotation_deg
                + rng.gauss(0.0, config.anneal_rotation_sigma_deg * sigma_scale)
            ),
        )
        current[index] = proposed
        proposed_score = score_placements(current, ctx)
        delta = proposed_score.total - current_score.total
        accept = delta <= 0.0 or rng.random() < math.exp(-delta / max(temperature, 1e-9))
        if accept:
            current_score = proposed_score
            if proposed_score.total < best_score.total:
                best = [Placement(**vars(p)) for p in current]
                best_score = proposed_score
        else:
            current[index] = old

    return best, best_score


def local_refine(
    placements: list[Placement],
    ctx: ObjectiveContext,
    config: AssemblyConfig,
) -> tuple[list[Placement], ScoreBreakdown]:
    current = [Placement(**vars(p)) for p in placements]
    current_score = score_placements(current, ctx)
    h, w = ctx.target.shape
    levels = zip(config.refine_translation_steps_px, config.refine_rotation_steps_deg)

    for translation_step, rotation_step in levels:
        for _ in range(max(1, config.refine_passes_per_level)):
            improved_any = False
            for index, old in enumerate(list(current)):
                candidates = [
                    (translation_step, 0, 0),
                    (-translation_step, 0, 0),
                    (0, translation_step, 0),
                    (0, -translation_step, 0),
                    (translation_step, translation_step, 0),
                    (translation_step, -translation_step, 0),
                    (-translation_step, translation_step, 0),
                    (-translation_step, -translation_step, 0),
                    (0, 0, rotation_step),
                    (0, 0, -rotation_step),
                ]
                best_local = old
                best_local_score = current_score
                for dx, dy, da in candidates:
                    candidate = Placement(
                        fragment_index=old.fragment_index,
                        center_x=float(np.clip(old.center_x + dx, 0, w - 1)),
                        center_y=float(np.clip(old.center_y + dy, 0, h - 1)),
                        rotation_deg=normalize_angle(old.rotation_deg + da),
                    )
                    current[index] = candidate
                    candidate_score = score_placements(current, ctx)
                    if candidate_score.total < best_local_score.total:
                        best_local = candidate
                        best_local_score = candidate_score
                current[index] = best_local
                if best_local_score.total < current_score.total:
                    current_score = best_local_score
                    improved_any = True
            if not improved_any:
                break
    return current, current_score


def map_placements_to_full(
    placements_opt: list[Placement],
    geometry: SearchGeometry,
) -> list[Placement]:
    return [
        Placement(
            fragment_index=p.fragment_index,
            center_x=p.center_x / geometry.search_scale_x,
            center_y=p.center_y / geometry.search_scale_y,
            rotation_deg=p.rotation_deg,
        )
        for p in placements_opt
    ]


def render_final_assembly(
    fragments: list[Fragment],
    placements_full: list[Placement],
    geometry: SearchGeometry,
    config: AssemblyConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    output_channels = 3 if any(f.image.ndim == 3 and f.image.shape[2] >= 3 for f in fragments) else 1
    output_dtype = np.result_type(*[f.image.dtype for f in fragments])
    shape = (
        (geometry.canvas_height_full, geometry.canvas_width_full)
        if output_channels == 1
        else (geometry.canvas_height_full, geometry.canvas_width_full, 3)
    )
    canvas = np.zeros(shape, dtype=output_dtype)
    owner = np.full(
        (geometry.canvas_height_full, geometry.canvas_width_full), -1, dtype=np.int32
    )
    overlap = np.zeros(owner.shape, dtype=np.uint16)

    interpolation = cv2.INTER_NEAREST
    for placement in placements_full:
        fragment = fragments[placement.fragment_index]
        image = ensure_output_channels(fragment.image, output_channels, output_dtype)
        mask = (fragment.mask > 0).astype(np.uint8) * 255
        rotated_image = rotate_bound(
            image,
            placement.rotation_deg,
            interpolation=interpolation,
            border_value=0,
        )
        rotated_mask = rotate_bound(
            mask,
            placement.rotation_deg,
            interpolation=cv2.INTER_NEAREST,
            border_value=0,
        )
        rotated_mask = rotated_mask > 0

        ph, pw = rotated_mask.shape
        x0 = int(round(placement.center_x - (pw - 1) / 2.0))
        y0 = int(round(placement.center_y - (ph - 1) / 2.0))
        x1, y1 = x0 + pw, y0 + ph
        cx0, cy0 = max(0, x0), max(0, y0)
        cx1, cy1 = min(owner.shape[1], x1), min(owner.shape[0], y1)
        if cx0 >= cx1 or cy0 >= cy1:
            continue
        px0, py0 = cx0 - x0, cy0 - y0
        px1, py1 = px0 + (cx1 - cx0), py0 + (cy1 - cy0)
        local_mask = rotated_mask[py0:py1, px0:px1]
        local_owner = owner[cy0:cy1, cx0:cx1]
        collision = local_mask & (local_owner >= 0)
        local_overlap = overlap[cy0:cy1, cx0:cx1]
        local_overlap[collision] += 1

        if config.overlap_policy == "keep_first":
            write_mask = local_mask & (local_owner < 0)
        elif config.overlap_policy == "overwrite_last":
            write_mask = local_mask
        else:
            raise ValueError(f"지원하지 않는 overlap_policy: {config.overlap_policy}")

        local_canvas = canvas[cy0:cy1, cx0:cx1]
        local_image = rotated_image[py0:py1, px0:px1]
        if output_channels == 1:
            local_canvas[write_mask] = local_image[write_mask]
        else:
            local_canvas[write_mask, :] = local_image[write_mask, :]
        local_owner[write_mask] = fragment.index

    assembly_mask = (owner >= 0).astype(np.uint8) * 255
    return canvas, assembly_mask, overlap


def make_alignment_overlay(
    target_preview: np.ndarray,
    target_mask: np.ndarray,
    assembly_mask: np.ndarray,
    overlap: np.ndarray,
) -> np.ndarray:
    preview = target_preview.copy()
    target = target_mask > 0
    assembly = assembly_mask > 0
    overlay = preview.astype(np.float32) * 0.45
    # BGR: target-only blue, assembly-only red, matched green, overlap magenta.
    matched = target & assembly
    target_only = target & ~assembly
    assembly_only = assembly & ~target
    overlay[matched] = (40, 190, 40)
    overlay[target_only] = (220, 80, 40)
    overlay[assembly_only] = (40, 60, 220)
    overlay[overlap > 0] = (220, 40, 220)
    return np.clip(overlay, 0, 255).astype(np.uint8)
