from __future__ import annotations

from pathlib import Path
from typing import Iterable
import re

import cv2
import numpy as np


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


def list_images(folder: str | Path) -> list[Path]:
    root = Path(folder)
    if not root.is_dir():
        raise FileNotFoundError(f"파편 폴더를 찾을 수 없습니다: {root}")
    def natural_key(path: Path) -> list[object]:
        return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", path.name)]

    paths = sorted(
        (p for p in root.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS),
        key=natural_key,
    )
    if not paths:
        raise FileNotFoundError(f"지원 이미지가 없습니다: {root}")
    return paths


def read_image(path: str | Path, flags: int = cv2.IMREAD_UNCHANGED) -> np.ndarray:
    """Windows 한글/유니코드 경로를 포함해 OpenCV 이미지 파일을 읽습니다."""
    p = Path(path)
    try:
        encoded = np.fromfile(str(p), dtype=np.uint8)
    except OSError as exc:
        raise ValueError(f"이미지 파일을 읽을 수 없습니다: {p}") from exc
    image = cv2.imdecode(encoded, flags) if encoded.size else None
    if image is None:
        raise ValueError(f"이미지를 디코딩할 수 없습니다: {p}")
    return image


def write_image(path: str | Path, image: np.ndarray) -> None:
    """Windows 한글/유니코드 경로를 포함해 OpenCV 이미지를 저장합니다."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ext = p.suffix.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"지원하지 않는 이미지 확장자입니다: {p.suffix}")
    ok, encoded = cv2.imencode(ext, image)
    if not ok:
        raise IOError(f"이미지 인코딩 실패: {p}")
    try:
        encoded.tofile(str(p))
    except OSError as exc:
        raise IOError(f"이미지 저장 실패: {p}") from exc


def to_gray_for_analysis(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image.copy()
    if image.ndim == 3 and image.shape[2] == 4:
        return cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    if image.ndim == 3 and image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    raise ValueError(f"지원하지 않는 이미지 shape: {image.shape}")


def to_uint8_for_analysis(image: np.ndarray) -> np.ndarray:
    if image.dtype == np.uint8:
        return image.copy()
    finite = image[np.isfinite(image)] if np.issubdtype(image.dtype, np.floating) else image.reshape(-1)
    if finite.size == 0:
        return np.zeros(image.shape, dtype=np.uint8)
    lo, hi = np.percentile(finite.astype(np.float64), [0.5, 99.5])
    if hi <= lo:
        return np.zeros(image.shape, dtype=np.uint8)
    scaled = np.clip((image.astype(np.float64) - lo) * 255.0 / (hi - lo), 0, 255)
    return scaled.astype(np.uint8)


def largest_components(mask: np.ndarray, min_area_ratio: float = 0.002) -> np.ndarray:
    binary = (mask > 0).astype(np.uint8)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if num <= 1:
        return binary * 255
    areas = stats[1:, cv2.CC_STAT_AREA]
    largest = int(areas.max())
    min_area = max(4, int(largest * min_area_ratio))
    out = np.zeros_like(binary)
    for label, area in enumerate(areas, start=1):
        if int(area) >= min_area:
            out[labels == label] = 1
    return out * 255


def fill_holes(mask: np.ndarray) -> np.ndarray:
    binary = (mask > 0).astype(np.uint8) * 255
    h, w = binary.shape
    flood = binary.copy()
    cv2.floodFill(flood, np.zeros((h + 2, w + 2), np.uint8), (0, 0), 255)
    holes = cv2.bitwise_not(flood)
    return cv2.bitwise_or(binary, holes)


def clean_mask(mask: np.ndarray, kernel_size: int = 3, min_area_ratio: float = 0.002) -> np.ndarray:
    binary = (mask > 0).astype(np.uint8) * 255
    k = max(1, int(kernel_size))
    if k % 2 == 0:
        k += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    binary = largest_components(binary, min_area_ratio=min_area_ratio)
    return fill_holes(binary)


def crop_to_mask(image: np.ndarray, mask: np.ndarray, padding: int = 2) -> tuple[np.ndarray, np.ndarray, tuple[int, int, int, int]]:
    ys, xs = np.where(mask > 0)
    if xs.size == 0:
        raise ValueError("마스크가 비어 있습니다.")
    x0 = max(0, int(xs.min()) - padding)
    y0 = max(0, int(ys.min()) - padding)
    x1 = min(mask.shape[1], int(xs.max()) + 1 + padding)
    y1 = min(mask.shape[0], int(ys.max()) + 1 + padding)
    return image[y0:y1, x0:x1].copy(), mask[y0:y1, x0:x1].copy(), (x0, y0, x1 - x0, y1 - y0)


def principal_angle_deg(mask: np.ndarray) -> float:
    ys, xs = np.where(mask > 0)
    if xs.size < 3:
        return 0.0
    pts = np.column_stack([xs, ys]).astype(np.float64)
    pts -= pts.mean(axis=0, keepdims=True)
    cov = np.cov(pts, rowvar=False)
    values, vectors = np.linalg.eigh(cov)
    v = vectors[:, int(np.argmax(values))]
    return float(np.degrees(np.arctan2(v[1], v[0])))


def rotate_bound(array: np.ndarray, angle_deg: float, interpolation: int, border_value: int | tuple[int, ...] = 0) -> np.ndarray:
    h, w = array.shape[:2]
    center = ((w - 1) / 2.0, (h - 1) / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    cos = abs(matrix[0, 0])
    sin = abs(matrix[0, 1])
    new_w = max(1, int(np.ceil(h * sin + w * cos)))
    new_h = max(1, int(np.ceil(h * cos + w * sin)))
    matrix[0, 2] += (new_w - 1) / 2.0 - center[0]
    matrix[1, 2] += (new_h - 1) / 2.0 - center[1]
    return cv2.warpAffine(
        array,
        matrix,
        (new_w, new_h),
        flags=interpolation,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_value,
    )


def paste_center(canvas: np.ndarray, patch: np.ndarray, center_x: float, center_y: float, mode: str = "overwrite") -> tuple[slice, slice, slice, slice] | None:
    ph, pw = patch.shape[:2]
    x0 = int(round(center_x - (pw - 1) / 2.0))
    y0 = int(round(center_y - (ph - 1) / 2.0))
    x1 = x0 + pw
    y1 = y0 + ph

    cx0 = max(0, x0)
    cy0 = max(0, y0)
    cx1 = min(canvas.shape[1], x1)
    cy1 = min(canvas.shape[0], y1)
    if cx0 >= cx1 or cy0 >= cy1:
        return None

    px0 = cx0 - x0
    py0 = cy0 - y0
    px1 = px0 + (cx1 - cx0)
    py1 = py0 + (cy1 - cy0)
    if mode == "overwrite":
        canvas[cy0:cy1, cx0:cx1] = patch[py0:py1, px0:px1]
    elif mode == "add":
        canvas[cy0:cy1, cx0:cx1] += patch[py0:py1, px0:px1]
    else:
        raise ValueError(f"지원하지 않는 paste mode: {mode}")
    return slice(cy0, cy1), slice(cx0, cx1), slice(py0, py1), slice(px0, px1)


def normalize_preview(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        gray = to_uint8_for_analysis(image)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if image.ndim == 3 and image.shape[2] == 4:
        image = image[:, :, :3]
    if image.dtype == np.uint8:
        return image.copy()
    channels = [to_uint8_for_analysis(image[:, :, c]) for c in range(image.shape[2])]
    return np.stack(channels, axis=2)


def ensure_output_channels(image: np.ndarray, channels: int, dtype: np.dtype) -> np.ndarray:
    if image.ndim == 3 and image.shape[2] == 4:
        image = image[:, :, :3]
    if channels == 1:
        if image.ndim == 2:
            out = image
        else:
            out = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        if image.ndim == 2:
            out = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        else:
            out = image
    return out.astype(dtype, copy=False)


def save_json(path: str | Path, data: object) -> None:
    import json

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def iter_border_pixels(gray: np.ndarray, width: int) -> np.ndarray:
    h, w = gray.shape
    bw = max(1, min(width, h // 3, w // 3))
    parts: Iterable[np.ndarray] = (
        gray[:bw, :].reshape(-1),
        gray[-bw:, :].reshape(-1),
        gray[:, :bw].reshape(-1),
        gray[:, -bw:].reshape(-1),
    )
    return np.concatenate(list(parts))
