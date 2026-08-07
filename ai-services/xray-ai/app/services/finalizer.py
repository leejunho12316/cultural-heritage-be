from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app import config


class FinalizationError(RuntimeError):
    """Konva 최종 배치를 실제 최종 X-ray 산출물로 렌더링하지 못한 경우."""


class Finalizer:
    """layout.final.json을 기준으로 최종 결합본과 provenance map을 만든다."""

    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
    SEAM_RADIUS_PX = 8

    def __init__(self, jobs_root: Path | None = None) -> None:
        self.jobs_root = (jobs_root or config.STITCH_JOBS_ROOT).resolve()

    def finalize(self, job_id: str) -> dict[str, Any]:
        job_dir = (self.jobs_root / job_id).resolve()
        if job_dir.parent != self.jobs_root or not job_dir.is_dir():
            raise FinalizationError(f"X-ray job directory was not found: {job_id}")

        job_path = job_dir / "job.json"
        job = self._read_json(job_path, "job.json")
        if str(job.get("status", "")).upper() != "COMPLETED":
            raise FinalizationError(
                f"X-ray stitching is not completed: {job.get('status')}"
            )

        artifact_id = str(job.get("artifactId", "")).strip()
        if not artifact_id:
            raise FinalizationError("artifactId is missing from job.json")

        artifact_dir = self._artifact_dir(job_dir, job, artifact_id)
        final_layout_path = artifact_dir / "layout.final.json"
        layout = self._read_json(final_layout_path, "layout.final.json")

        canvas = layout.get("canvas") or {}
        width = int(canvas.get("width") or 0)
        height = int(canvas.get("height") or 0)
        if width <= 0 or height <= 0:
            raise FinalizationError("Final layout canvas width/height is invalid")

        fragments = layout.get("fragments")
        if not isinstance(fragments, list) or not fragments:
            raise FinalizationError("Final layout fragments are missing")
        fragments = sorted(fragments, key=lambda item: int(item.get("index", -1)))

        source_files = self._list_images(job_dir / "inputs" / "xray")
        if not source_files:
            raise FinalizationError("Original X-ray source images were not found")

        mask_files = self._fragment_masks(artifact_dir, fragments)

        first_source = cv2.imread(str(source_files[0]), cv2.IMREAD_UNCHANGED)
        if first_source is None:
            raise FinalizationError(f"Failed to read X-ray source: {source_files[0]}")

        if first_source.ndim == 2:
            output_shape = (height, width)
        else:
            output_shape = (height, width, first_source.shape[2])

        final_image = np.zeros(output_shape, dtype=first_source.dtype)
        fragment_owner = np.zeros((height, width), dtype=np.uint16)
        source_owner = np.zeros((height, width), dtype=np.uint16)
        best_weight = np.full((height, width), -1.0, dtype=np.float32)
        overlap_count = np.zeros((height, width), dtype=np.uint16)

        for fragment in fragments:
            fragment_index = self._required_int(fragment, "index")
            source_index = self._required_int(fragment, "originalSourceIndex")
            if source_index < 0 or source_index >= len(source_files):
                raise FinalizationError(
                    f"originalSourceIndex is out of range: {source_index}"
                )

            crop_box = fragment.get("originalCropBBoxXYWH")
            if not isinstance(crop_box, list) or len(crop_box) < 4:
                raise FinalizationError(
                    f"originalCropBBoxXYWH is missing: fragment={fragment_index}"
                )
            x, y, crop_w, crop_h = [int(round(float(v))) for v in crop_box[:4]]
            if crop_w <= 0 or crop_h <= 0:
                raise FinalizationError(
                    f"Invalid crop size: fragment={fragment_index}, crop={crop_box}"
                )

            source = cv2.imread(str(source_files[source_index]), cv2.IMREAD_UNCHANGED)
            if source is None:
                raise FinalizationError(
                    f"Failed to read X-ray source: {source_files[source_index]}"
                )
            source_h, source_w = source.shape[:2]
            if x < 0 or y < 0 or x + crop_w > source_w or y + crop_h > source_h:
                raise FinalizationError(
                    f"Crop is outside source image: fragment={fragment_index}, crop={crop_box}"
                )

            crop = source[y : y + crop_h, x : x + crop_w]
            mask = cv2.imread(str(mask_files[fragment_index]), cv2.IMREAD_GRAYSCALE)
            if mask is None:
                raise FinalizationError(
                    f"Failed to read fragment mask: {mask_files[fragment_index]}"
                )
            if mask.shape != (crop_h, crop_w):
                raise FinalizationError(
                    "Fragment mask/crop size mismatch: "
                    f"fragment={fragment_index}, mask={mask.shape[::-1]}, crop={(crop_w, crop_h)}"
                )

            matrix = np.asarray(fragment.get("affineMatrix"), dtype=np.float32)
            if matrix.shape == (3, 3):
                matrix = matrix[:2]
            if matrix.shape != (2, 3):
                raise FinalizationError(
                    f"Invalid affineMatrix: fragment={fragment_index}"
                )

            binary_mask = (mask > 0).astype(np.uint8)
            distance = cv2.distanceTransform(binary_mask, cv2.DIST_L2, 3)

            warped_mask = cv2.warpAffine(
                binary_mask,
                matrix,
                (width, height),
                flags=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            ) > 0
            warped_image = cv2.warpAffine(
                crop,
                matrix,
                (width, height),
                flags=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            )
            warped_weight = cv2.warpAffine(
                distance.astype(np.float32),
                matrix,
                (width, height),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            )

            overlap_count[warped_mask] += 1
            write = warped_mask & (warped_weight > best_weight)
            if final_image.ndim == 2:
                final_image[write] = warped_image[write]
            else:
                final_image[write, :] = warped_image[write, :]

            # PNG에서는 0을 배경으로 쓰기 위해 실제 index에 +1 해서 기록한다.
            fragment_owner[write] = fragment_index + 1
            source_owner[write] = source_index + 1
            best_weight[write] = warped_weight[write]

        overlap_mask = (overlap_count > 1).astype(np.uint8) * 255
        seam_zone = self._build_seam_zone(source_owner)

        final_image_path = artifact_dir / "assembled_xray.final.png"
        source_owner_path = artifact_dir / "source_owner.final.png"
        fragment_owner_path = artifact_dir / "fragment_owner.final.png"
        seam_zone_path = artifact_dir / "seam_zone.final.png"
        overlap_mask_path = artifact_dir / "overlap_mask.final.png"
        manifest_path = artifact_dir / "provenance.final.json"

        self._write_image(final_image_path, final_image)
        self._write_image(source_owner_path, source_owner)
        self._write_image(fragment_owner_path, fragment_owner)
        self._write_image(seam_zone_path, seam_zone)
        self._write_image(overlap_mask_path, overlap_mask)

        finalized_at = datetime.now(timezone.utc).isoformat()
        manifest = {
            "schemaVersion": 1,
            "jobId": job_id,
            "artifactId": artifact_id,
            "layoutFile": "layout.final.json",
            "assembledImage": final_image_path.name,
            "sourceOwner": {
                "file": source_owner_path.name,
                "encoding": "uint16_png",
                "backgroundValue": 0,
                "sourceIndexValue": "originalSourceIndex + 1",
            },
            "fragmentOwner": {
                "file": fragment_owner_path.name,
                "encoding": "uint16_png",
                "backgroundValue": 0,
                "fragmentIndexValue": "layout fragment index + 1",
            },
            "seamZone": {
                "file": seam_zone_path.name,
                "encoding": "binary_8bit_png",
                "radiusPx": self.SEAM_RADIUS_PX,
                "meaning": "boundary neighborhood where adjacent visible pixels come from different originalSourceIndex values",
            },
            "overlapMask": {
                "file": overlap_mask_path.name,
                "encoding": "binary_8bit_png",
                "meaning": "pixels covered by masks from two or more layout fragments before winner selection",
            },
            "finalizedAt": finalized_at,
        }
        self._write_json(manifest_path, manifest)

        job.update(
            {
                "finalLayoutPath": str(final_layout_path),
                "finalAssembledImagePath": str(final_image_path),
                "sourceOwnerPath": str(source_owner_path),
                "fragmentOwnerPath": str(fragment_owner_path),
                "seamZonePath": str(seam_zone_path),
                "overlapMaskPath": str(overlap_mask_path),
                "provenancePath": str(manifest_path),
                "finalizedAt": finalized_at,
            }
        )
        self._write_json(job_path, job)

        return manifest

    def _artifact_dir(
        self,
        job_dir: Path,
        job: dict[str, Any],
        artifact_id: str,
    ) -> Path:
        layout_path_value = job.get("layoutPath")
        if layout_path_value:
            layout_path = self._resolve_runtime_path(str(layout_path_value))
            if layout_path.is_file():
                return layout_path.parent

        fallback = job_dir / "outputs" / "assembly" / "artifacts" / artifact_id
        if not fallback.is_dir():
            raise FinalizationError(f"Artifact output directory was not found: {fallback}")
        return fallback.resolve()

    def _resolve_runtime_path(self, value: str) -> Path:
        # Legacy absolute paths are not authoritative in the S3-native flow.
        # A bundled job falls back to its process-local artifact directory.
        return Path(value).resolve()

    def _fragment_masks(
        self,
        artifact_dir: Path,
        fragments: list[dict[str, Any]],
    ) -> dict[int, Path]:
        # 신규 job은 실제 layout fragment(분할 subfragment 포함)의 마스크를
        # index로 직접 저장하므로 이 경로가 정본이다.
        layout_mask_root = artifact_dir / "debug" / "layout_fragment_masks"
        if layout_mask_root.is_dir():
            exact: dict[int, Path] = {}
            for fragment in fragments:
                index = self._required_int(fragment, "index")
                path = layout_mask_root / f"fragment_{index:04d}.png"
                if not path.is_file():
                    raise FinalizationError(
                        f"Layout fragment mask is missing: fragment={index}, path={path}"
                    )
                exact[index] = path
            return exact

        # 기존 job 호환: split 이전 원본 fragment mask만 존재할 수 있다.
        # 이 fallback은 파일명/크기로 최선 대응하며, 신규 job에서는 사용하지 않는다.
        mask_root = artifact_dir / "debug" / "fragment_masks"
        if not mask_root.is_dir():
            raise FinalizationError(f"Fragment mask directory was not found: {mask_root}")

        available = self._list_images(mask_root)
        if len(available) < len(fragments):
            raise FinalizationError(
                "Not enough fragment masks: "
                f"expected={len(fragments)}, actual={len(available)}"
            )

        by_name = {path.name: path for path in available}
        result: dict[int, Path] = {}
        unused = set(available)

        # 정상 런타임에서는 layout.file과 debug mask 이름이 정확히 대응한다.
        for fragment in fragments:
            index = self._required_int(fragment, "index")
            file_name = str(fragment.get("file") or "")
            candidate_name = f"{Path(file_name).stem}_mask.png" if file_name else ""
            candidate = by_name.get(candidate_name)
            if candidate is not None:
                result[index] = candidate
                unused.discard(candidate)

        # ZIP 인코딩 등으로 파일명이 달라져도 crop 크기와 index 순서로 복구한다.
        remaining_fragments = [
            fragment
            for fragment in fragments
            if self._required_int(fragment, "index") not in result
        ]
        remaining_masks = sorted(unused, key=lambda path: self._natural_key(path.name))
        for fragment in remaining_fragments:
            index = self._required_int(fragment, "index")
            crop_box = fragment.get("originalCropBBoxXYWH") or []
            expected = None
            if isinstance(crop_box, list) and len(crop_box) >= 4:
                expected = (
                    int(round(float(crop_box[2]))),
                    int(round(float(crop_box[3]))),
                )

            selected = None
            if expected is not None:
                for path in remaining_masks:
                    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                    if image is not None and image.shape[::-1] == expected:
                        selected = path
                        break
            if selected is None and remaining_masks:
                selected = remaining_masks[0]
            if selected is None:
                raise FinalizationError(f"Fragment mask was not found: index={index}")

            result[index] = selected
            remaining_masks.remove(selected)

        return result

    def _build_seam_zone(self, source_owner: np.ndarray) -> np.ndarray:
        seam = np.zeros(source_owner.shape, dtype=np.uint8)

        left = source_owner[:, :-1]
        right = source_owner[:, 1:]
        horizontal = (left > 0) & (right > 0) & (left != right)
        seam[:, :-1][horizontal] = 255
        seam[:, 1:][horizontal] = 255

        top = source_owner[:-1, :]
        bottom = source_owner[1:, :]
        vertical = (top > 0) & (bottom > 0) & (top != bottom)
        seam[:-1, :][vertical] = 255
        seam[1:, :][vertical] = 255

        radius = self.SEAM_RADIUS_PX
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (radius * 2 + 1, radius * 2 + 1),
        )
        return cv2.dilate(seam, kernel)

    @classmethod
    def _list_images(cls, directory: Path) -> list[Path]:
        if not directory.is_dir():
            return []
        return sorted(
            (
                path.resolve()
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in cls.IMAGE_EXTENSIONS
            ),
            key=lambda path: cls._natural_key(path.name),
        )

    @staticmethod
    def _natural_key(value: str) -> list[tuple[int, Any]]:
        parts = re.findall(r"\d+|\D+", value)
        return [
            (0, int(part)) if part.isdigit() else (1, part.casefold())
            for part in parts
        ]

    @staticmethod
    def _required_int(obj: dict[str, Any], key: str) -> int:
        value = obj.get(key)
        if value is None:
            raise FinalizationError(f"Required integer is missing: {key}")
        return int(value)

    @staticmethod
    def _read_json(path: Path, label: str) -> dict[str, Any]:
        if not path.is_file():
            raise FinalizationError(f"{label} was not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FinalizationError(f"Failed to read {label}: {path}") from exc
        if not isinstance(data, dict):
            raise FinalizationError(f"{label} must contain a JSON object")
        return data

    @staticmethod
    def _write_image(path: Path, image: np.ndarray) -> None:
        if not cv2.imwrite(str(path), image):
            raise FinalizationError(f"Failed to write image: {path}")

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
