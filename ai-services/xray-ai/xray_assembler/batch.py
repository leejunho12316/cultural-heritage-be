from __future__ import annotations

import csv
import json
import shutil
import time
import traceback
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

from .config import AssemblyConfig
from .image_ops import normalize_preview, read_image, save_json, write_image
from .pipeline import run_assembly
from .segmentation import segment_reference


@dataclass(frozen=True)
class MappingEntry:
    artifact_id: str
    color_file: str
    enabled: bool = True
    default_test: bool = False
    review_recommended: bool = False
    note: str = ""


def _safe_extract_zip(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            target = (destination / info.filename).resolve()
            if root != target and root not in target.parents:
                raise ValueError(f"안전하지 않은 ZIP 경로: {info.filename}")
        zf.extractall(destination)


def _find_one(root: Path, name: str) -> Path:
    direct = root / name
    if direct.exists():
        return direct
    matches = sorted(root.rglob(name))
    if not matches:
        raise FileNotFoundError(f"{root} 아래에서 {name}을 찾을 수 없습니다.")
    if len(matches) > 1:
        raise RuntimeError(f"{name}이 여러 개 발견되었습니다: {matches}")
    return matches[0]


def prepare_dataset_root(
    dataset_root: Path | None,
    dataset_zip: Path | None,
    cache_root: Path,
) -> Path:
    if dataset_root is not None:
        manifest = _find_one(dataset_root, "dataset_manifest.json")
        return manifest.parent
    if dataset_zip is None:
        raise ValueError("--dataset-root 또는 --dataset-zip 중 하나가 필요합니다.")
    extracted = cache_root / "dataset"
    marker = extracted / ".source.json"
    signature = {"path": str(dataset_zip.resolve()), "size": dataset_zip.stat().st_size}
    if marker.exists():
        try:
            if json.loads(marker.read_text(encoding="utf-8")) == signature:
                manifest = _find_one(extracted, "dataset_manifest.json")
                return manifest.parent
        except Exception:
            pass
    shutil.rmtree(extracted, ignore_errors=True)
    _safe_extract_zip(dataset_zip, extracted)
    marker.write_text(json.dumps(signature, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = _find_one(extracted, "dataset_manifest.json")
    return manifest.parent


def prepare_color_root(
    color_root: Path | None,
    color_zip: Path | None,
    cache_root: Path,
) -> Path:
    if color_root is not None:
        if not color_root.is_dir():
            raise FileNotFoundError(f"컬러 이미지 폴더가 없습니다: {color_root}")
        return color_root
    if color_zip is None:
        raise ValueError("--color-root 또는 --color-zip 중 하나가 필요합니다.")
    extracted = cache_root / "color"
    marker = extracted / ".source.json"
    signature = {"path": str(color_zip.resolve()), "size": color_zip.stat().st_size}
    if marker.exists():
        try:
            if json.loads(marker.read_text(encoding="utf-8")) == signature:
                return extracted
        except Exception:
            pass
    shutil.rmtree(extracted, ignore_errors=True)
    _safe_extract_zip(color_zip, extracted)
    marker.write_text(json.dumps(signature, ensure_ascii=False, indent=2), encoding="utf-8")
    return extracted


def load_manifest(dataset_root: Path) -> dict[str, Any]:
    path = dataset_root / "dataset_manifest.json"
    with path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("dataset_manifest.json에 samples가 없습니다.")
    return manifest


def load_mapping(path: Path) -> dict[str, MappingEntry]:
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    entries = raw.get("mappings") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError("mapping JSON은 mappings 배열을 포함해야 합니다.")
    result: dict[str, MappingEntry] = {}
    for item in entries:
        entry = MappingEntry(
            artifact_id=str(item["artifactId"]),
            color_file=str(item["colorFile"]),
            enabled=bool(item.get("enabled", True)),
            default_test=bool(item.get("defaultTest", False)),
            review_recommended=bool(item.get("reviewRecommended", False)),
            note=str(item.get("note", "")),
        )
        if entry.artifact_id in result:
            raise ValueError(f"중복 mapping artifactId: {entry.artifact_id}")
        result[entry.artifact_id] = entry
    return result


def find_color_file(color_root: Path, relative_name: str) -> Path:
    direct = color_root / relative_name
    if direct.is_file():
        return direct
    matches = [p for p in color_root.rglob(Path(relative_name).name) if p.is_file()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"컬러 완성본을 찾을 수 없습니다: {relative_name}")
    raise RuntimeError(f"동일 이름의 컬러 완성본이 여러 개입니다: {relative_name}")


def validate_mapping(
    dataset_root: Path,
    color_root: Path,
    manifest: dict[str, Any],
    mapping: dict[str, MappingEntry],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    manifest_ids = {str(sample["id"]) for sample in manifest["samples"]}
    for sample in manifest["samples"]:
        artifact_id = str(sample["id"])
        entry = mapping.get(artifact_id)
        errors: list[str] = []
        color_path: Path | None = None
        fragment_dir = dataset_root / str(sample["fragment_dir"])
        if entry is None:
            errors.append("mapping 누락")
        else:
            try:
                color_path = find_color_file(color_root, entry.color_file)
            except Exception as exc:
                errors.append(str(exc))
        if not fragment_dir.is_dir():
            errors.append(f"파편 폴더 누락: {fragment_dir}")
        actual_count = (
            len([p for p in fragment_dir.iterdir() if p.is_file()])
            if fragment_dir.is_dir()
            else 0
        )
        expected_count = int(sample.get("fragment_count", 0))
        if fragment_dir.is_dir() and actual_count != expected_count:
            errors.append(f"파편 수 불일치 manifest={expected_count}, actual={actual_count}")
        rows.append(
            {
                "artifact_id": artifact_id,
                "artifact_name": str(sample.get("artifact_name", "")),
                "fragment_count": expected_count,
                "fragment_dir": str(fragment_dir),
                "color_file": entry.color_file if entry else "",
                "color_path": str(color_path) if color_path else "",
                "enabled": entry.enabled if entry else False,
                "default_test": entry.default_test if entry else False,
                "review_recommended": entry.review_recommended if entry else True,
                "note": entry.note if entry else "",
                "status": "ok" if not errors else "error",
                "error": " | ".join(errors),
            }
        )
    extras = sorted(set(mapping) - manifest_ids)
    for artifact_id in extras:
        entry = mapping[artifact_id]
        rows.append(
            {
                "artifact_id": artifact_id,
                "artifact_name": "",
                "fragment_count": 0,
                "fragment_dir": "",
                "color_file": entry.color_file,
                "color_path": "",
                "enabled": entry.enabled,
                "default_test": entry.default_test,
                "review_recommended": entry.review_recommended,
                "note": entry.note,
                "status": "error",
                "error": "manifest에 없는 artifactId",
            }
        )
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def _draw_reference_mask_overlay(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    preview = normalize_preview(image)
    green = np.zeros_like(preview)
    green[:, :, 1] = 255
    alpha = (mask > 0).astype(np.float32)[..., None] * 0.35
    overlay = np.clip(preview * (1.0 - alpha) + green * alpha, 0, 255).astype(np.uint8)
    contours, _ = cv2.findContours(
        (mask > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    cv2.drawContours(overlay, contours, -1, (0, 255, 0), 3)
    return overlay


def _fit_tile(image: np.ndarray, width: int, height: int) -> np.ndarray:
    if image is None or image.size == 0:
        return np.zeros((height, width, 3), dtype=np.uint8)
    preview = normalize_preview(image)
    h, w = preview.shape[:2]
    scale = min(width / max(w, 1), height / max(h, 1))
    resized = cv2.resize(
        preview,
        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_NEAREST,
    )
    canvas = np.full((height, width, 3), 245, dtype=np.uint8)
    y = (height - resized.shape[0]) // 2
    x = (width - resized.shape[1]) // 2
    canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    return canvas


def make_contact_sheet(
    items: Iterable[tuple[str, Path]],
    output_path: Path,
    columns: int = 3,
    tile_width: int = 520,
    tile_height: int = 420,
    label_height: int = 54,
) -> None:
    loaded: list[tuple[str, np.ndarray]] = []
    for label, path in items:
        image = read_image(path, cv2.IMREAD_COLOR)
        if image is not None:
            loaded.append((label, image))
    if not loaded:
        return
    rows = (len(loaded) + columns - 1) // columns
    sheet = np.full(
        (rows * (tile_height + label_height), columns * tile_width, 3),
        255,
        dtype=np.uint8,
    )
    for idx, (label, image) in enumerate(loaded):
        row, col = divmod(idx, columns)
        x0 = col * tile_width
        y0 = row * (tile_height + label_height)
        sheet[y0 : y0 + tile_height, x0 : x0 + tile_width] = _fit_tile(
            image, tile_width, tile_height
        )
        safe_label = label.encode("ascii", errors="replace").decode("ascii")
        cv2.putText(
            sheet,
            safe_label[:72],
            (x0 + 8, y0 + tile_height + 34),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (20, 20, 20),
            1,
            cv2.LINE_AA,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_image(output_path, sheet)


def select_samples(
    manifest: dict[str, Any],
    mapping: dict[str, MappingEntry],
    ids: set[str] | None,
    run_all: bool,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for sample in manifest["samples"]:
        artifact_id = str(sample["id"])
        entry = mapping.get(artifact_id)
        if entry is None or not entry.enabled:
            continue
        if ids is not None:
            if artifact_id in ids:
                selected.append(sample)
        elif run_all:
            selected.append(sample)
        elif entry.default_test:
            selected.append(sample)
    if ids is not None:
        found = {str(s["id"]) for s in selected}
        missing = sorted(ids - found)
        if missing:
            raise ValueError(f"선택할 수 없는 artifact ID: {', '.join(missing)}")
    return selected


def run_reference_mask_preview(
    samples: list[dict[str, Any]],
    color_root: Path,
    mapping: dict[str, MappingEntry],
    output_root: Path,
    config: AssemblyConfig,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    contact_items: list[tuple[str, Path]] = []
    mask_root = output_root / "reference_masks"
    mask_root.mkdir(parents=True, exist_ok=True)
    for sample in samples:
        artifact_id = str(sample["id"])
        entry = mapping[artifact_id]
        started = time.perf_counter()
        row: dict[str, Any] = {
            "artifact_id": artifact_id,
            "artifact_name": sample.get("artifact_name", ""),
            "color_file": entry.color_file,
            "review_recommended": entry.review_recommended,
            "note": entry.note,
        }
        try:
            color_path = find_color_file(color_root, entry.color_file)
            image = read_image(color_path)
            mask, diagnostics = segment_reference(image, config)
            item_dir = mask_root / artifact_id
            item_dir.mkdir(parents=True, exist_ok=True)
            mask_path = item_dir / "reference_mask.png"
            overlay_path = item_dir / "reference_mask_overlay.jpg"
            write_image(mask_path, mask)
            write_image(overlay_path, _draw_reference_mask_overlay(image, mask))
            row.update(
                {
                    "status": "completed",
                    "elapsed_seconds": time.perf_counter() - started,
                    "mask_area_ratio": float(np.mean(mask > 0)),
                    "segmentation_method": diagnostics.get("method"),
                    "segmentation_quality": diagnostics.get("quality"),
                    "mask_path": str(mask_path),
                    "overlay_path": str(overlay_path),
                    "error": "",
                }
            )
            contact_items.append((artifact_id, overlay_path))
        except Exception as exc:
            row.update(
                {
                    "status": "error",
                    "elapsed_seconds": time.perf_counter() - started,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        rows.append(row)
    _write_csv(output_root / "reference_mask_summary.csv", rows)
    save_json(output_root / "reference_mask_summary.json", rows)
    make_contact_sheet(contact_items, output_root / "reference_mask_contact_sheet.jpg")
    return rows


def run_batch_assembly(
    samples: list[dict[str, Any]],
    dataset_root: Path,
    color_root: Path,
    mapping: dict[str, MappingEntry],
    output_root: Path,
    config: AssemblyConfig,
    resume: bool = True,
    continue_on_error: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    contact_items: list[tuple[str, Path]] = []
    for sample in samples:
        artifact_id = str(sample["id"])
        entry = mapping[artifact_id]
        item_root = output_root / "artifacts" / artifact_id
        report_path = item_root / "report.json"
        started = time.perf_counter()
        row: dict[str, Any] = {
            "artifact_id": artifact_id,
            "artifact_name": sample.get("artifact_name", ""),
            "fragment_count": int(sample.get("fragment_count", 0)),
            "color_file": entry.color_file,
            "review_recommended": entry.review_recommended,
            "note": entry.note,
        }
        try:
            if resume and report_path.exists():
                report = json.loads(report_path.read_text(encoding="utf-8"))
                status = "resumed"
            else:
                color_path = find_color_file(color_root, entry.color_file)
                fragment_dir = dataset_root / str(sample["fragment_dir"])
                report = run_assembly(
                    reference_path=color_path,
                    fragments_dir=fragment_dir,
                    output_dir=item_root,
                    config=config,
                )
                status = "completed"
                report["batchMetadata"] = {
                    "artifactId": artifact_id,
                    "artifactName": sample.get("artifact_name", ""),
                    "manifestFragmentDir": sample.get("fragment_dir"),
                    "manifestFragmentCount": sample.get("fragment_count"),
                    "colorFile": entry.color_file,
                    "reviewRecommended": entry.review_recommended,
                    "mappingNote": entry.note,
                }
                save_json(report_path, report)

                # Enrich layout.json for the later Konva HITL editor without
                # altering any transform or source pixel.
                layout_path = item_root / "layout.json"
                if layout_path.exists():
                    layout = json.loads(layout_path.read_text(encoding="utf-8"))
                    relative_by_name = {
                        Path(str(value)).name: str(value)
                        for value in sample.get("fragments", [])
                    }
                    layout["artifact"] = {
                        "id": artifact_id,
                        "name": sample.get("artifact_name", ""),
                        "manifestFragmentDir": sample.get("fragment_dir"),
                        "colorFile": entry.color_file,
                        "reviewRecommended": entry.review_recommended,
                    }
                    for z_index, fragment in enumerate(layout.get("fragments", [])):
                        fragment["datasetRelativePath"] = relative_by_name.get(
                            str(fragment.get("file", "")), ""
                        )
                        fragment["zIndex"] = z_index
                        fragment["reviewed"] = False
                    save_json(layout_path, layout)
            refined = report.get("scores", {}).get("refined", {})
            registration = report.get("registration", {})
            global_alignment = report.get("scores", {}).get("globalAlignment", {})
            quality_flags = report.get("qualityFlags", {})
            row.update(
                {
                    "status": status,
                    "elapsed_seconds": float(report.get("elapsedSeconds", time.perf_counter() - started)),
                    "final_iou": report.get("scores", {}).get("finalFullResolutionIoU"),
                    "search_iou": refined.get("iou"),
                    "outside_ratio": refined.get("outside_ratio"),
                    "overlap_ratio": refined.get("overlap_ratio"),
                    "missing_ratio": refined.get("missing_ratio"),
                    "boundary_f1": refined.get("boundary_f1"),
                    "candidate_pair_count": registration.get("candidatePairCount"),
                    "selected_pair_count": registration.get("selectedPairCount"),
                    "component_count": registration.get("componentCount"),
                    "assigned_component_count": global_alignment.get("assignedComponentCount"),
                    "unassigned_component_count": registration.get("unassignedComponentCount"),
                    "multiple_physical_components": registration.get("multiplePhysicalComponents"),
                    "needs_hitl_for_unassigned_component": registration.get(
                        "needsHitlForUnassignedComponent"
                    ),
                    "overlap_pixels": quality_flags.get("overlapPixels"),
                    "reference_segmentation_needs_review": quality_flags.get(
                        "referenceSegmentationNeedsReview"
                    ),
                    "fragment_registration_needs_review": quality_flags.get(
                        "fragmentRegistrationNeedsReview"
                    ),
                    "global_alignment_needs_review": quality_flags.get(
                        "globalAlignmentNeedsReview"
                    ),
                    "initial_placement_needs_review": quality_flags.get(
                        "initialPlacementNeedsReview"
                    ),
                    "output_dir": str(item_root),
                    "error": "",
                }
            )
            overlay_path = item_root / "reference_vs_assembly_overlay.png"
            if overlay_path.exists():
                label = f"{artifact_id} IoU={row.get('final_iou', 0):.3f}"
                contact_items.append((label, overlay_path))
        except Exception as exc:
            row.update(
                {
                    "status": "error",
                    "elapsed_seconds": time.perf_counter() - started,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
            )
            if not continue_on_error:
                rows.append(row)
                break
        rows.append(row)
        _write_csv(output_root / "batch_summary.csv", rows)
        save_json(output_root / "batch_summary.json", rows)
    _write_csv(output_root / "batch_summary.csv", rows)
    save_json(output_root / "batch_summary.json", rows)
    make_contact_sheet(contact_items, output_root / "batch_overlay_contact_sheet.jpg")
    return rows
