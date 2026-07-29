"""
X-ray 조각 결합(스티칭) 서비스

담당: 조각 결합 파트


[이 파일의 역할 - 어댑터]

결합 엔진은 원래 CLI 도구다. 폴더를 입력받아 subprocess로
돌고 산출물을 디스크에 쓴다.

반면 이 서비스의 규약은 무상태 멀티파트다.
업로드를 임시 저장하고, 처리하고, 반드시 지운다.

두 방식을 잇는 것이 이 파일이다.

    업로드 임시파일  →  원본 이름으로 작업 폴더에 배치
                     →  엔진 subprocess 실행
                     →  결합본 / 배치정보 회수
                     →  camelCase dict 반환
                     →  작업 폴더 통째로 삭제

엔진 자체는 수정하지 않는다.


[왜 assemble_xray.py 인가]

엔진에는 진입점이 둘 있다.

  batch_assemble.py  사전 등록된 데이터셋을 일괄 처리한다.
                     dataset_manifest.json 과 매핑 파일이 있어야 하고,
                     유물 ID가 양쪽에 등록되어 있어야 한다.
                     사용자가 임의로 올린 조각은 처리할 수 없다.

  assemble_xray.py   컬러 기준 이미지 1장과 조각 폴더만 받는다.
                     프론트에서 올라오는 것과 정확히 일치한다.

따라서 이 서비스는 assemble_xray.py 를 쓴다.
그 결과 artifactId 는 엔진에 전달되지 않고 응답 라벨로만 쓰인다.
매핑에 등록되지 않은 유물도 결합할 수 있다.


[상태를 갖지 않는 이유]

job 관리, 이력, 검수는 Spring 이 DB 로 처리한다.
컨테이너 임시파일에 남긴 상태는 재시작 시 사라져
공식 기록의 근거가 될 수 없다.
"""

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from app import config


# ------------------------------------------------------------
# 결합본 파일명 후보
#
# 엔진이 내보내는 파일명이 확정되면 맨 앞에 고정한다.
# 후보에 없으면 산출 폴더에서 가장 큰 이미지를 결합본으로 본다.
# ------------------------------------------------------------

ASSEMBLED_NAME_CANDIDATES = (
    "assembled.png",
    "assembled_xray.png",
    "composite.png",
    "composite.jpg",
    "result.png",
    "assembly.png",
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


class StitchError(RuntimeError):
    """결합 엔진 실행 실패. 라우터에서 502로 변환한다."""


# ------------------------------------------------------------
# 엔진 준비 상태 진단
# ------------------------------------------------------------

def check_engine() -> dict:
    """
    결합에 필요한 파일이 모두 갖춰졌는지 확인한다.

    결합은 수 분에서 수십 분이 걸린다. 설정 파일 하나가 없어서
    한참 뒤에 실패하면 시간 낭비가 크므로, 실행 전에 상태를
    확인할 수 있는 수단을 둔다.
    """
    try:
        script = _resolve_script()
        script_path = str(script)
        script_exists = True
    except StitchError:
        script_path = None
        script_exists = False

    try:
        package = _resolve_assembler_package()
        package_path = str(package)
        package_exists = True
    except StitchError:
        package_path = None
        package_exists = False

    try:
        config_path = str(
            _resolve_config(config.STITCH_DEFAULT_CONFIG_NAME)
        )
        config_exists = True
    except StitchError:
        config_path = None
        config_exists = False

    return {
        "stitchReady": script_exists
        and package_exists
        and config_exists,
        "engineDir": str(config.STITCH_ENGINE_DIR),
        "engineExists": config.STITCH_ENGINE_DIR.is_dir(),
        "scriptExists": script_exists,
        "scriptPath": script_path,
        "assemblerPackageExists": package_exists,
        "assemblerPackagePath": package_path,
        "configExists": config_exists,
        "configPath": config_path,
        "defaultConfigName": config.STITCH_DEFAULT_CONFIG_NAME,
        "timeoutSeconds": config.STITCH_TIMEOUT_SECONDS,
    }


# ------------------------------------------------------------
# 진입점
# ------------------------------------------------------------

def run(
    xray_paths: list[Path],
    color_paths: list[Path],
    artifact_id: str,
    config_name: str | None = None,
    xray_names: list[str] | None = None,
    color_names: list[str] | None = None,
) -> dict:
    """
    조각들을 결합하고 결과를 반환한다.

    Parameters
    ----------
    xray_paths
        업로드된 X-ray 조각 임시 경로 목록. 최소 1장.
    color_paths
        컬러 기준 이미지. 엔진은 이 완성본의 외곽 형태를 기준으로
        조각을 회전 이동하여 배치하므로 반드시 1장 필요하다.
        여러 장이 오면 첫 장을 쓴다.
    artifact_id
        응답 라벨로만 쓰인다. 엔진에는 전달되지 않는다.
    config_name
        결합 설정 이름. None이면 config 기본값.
    xray_names, color_names
        업로드 원본 파일명. save_upload는 충돌을 피하려고
        파일을 {uuid}.jpg 로 저장하므로, 그대로 넘기면
        배치 정보의 fileName이 uuid가 되어 조각과
        변환행렬을 이을 수 없다.

    Raises
    ------
    StitchError
        입력 부족, 엔진 실행 실패, 산출물 누락.
    """
    if not xray_paths:
        raise StitchError("X-ray 조각이 최소 1장 필요합니다.")

    if not color_paths:
        raise StitchError(
            "컬러 기준 이미지가 필요합니다. 결합 엔진은 컬러 "
            "완성본의 외곽 형태를 기준으로 조각을 배치하므로 "
            "기준 이미지 없이는 동작하지 않습니다."
        )

    script = _resolve_script()
    _resolve_assembler_package()

    config_path = _resolve_config(
        config_name or config.STITCH_DEFAULT_CONFIG_NAME
    )

    # 작업 폴더는 요청 단위로 만들고 finally에서 통째로 지운다.
    work_dir = Path(
        tempfile.mkdtemp(
            prefix=f"stitch_{uuid.uuid4().hex[:8]}_",
            dir=str(config.UPLOAD_DIR),
        )
    )

    try:
        fragments_dir = _stage_fragments(
            xray_paths, xray_names, work_dir / "fragments"
        )

        reference_path = _stage_reference(
            color_paths[0],
            color_names[0] if color_names else None,
            work_dir,
        )

        output_dir = work_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        command = [
            sys.executable,
            str(script),
            "--reference", str(reference_path),
            "--fragments", str(fragments_dir),
            "--output", str(output_dir),
            "--config", str(config_path),
        ]

        report = _execute(command, script.parent, work_dir)

        return _collect_result(
            output_dir=output_dir,
            artifact_id=artifact_id,
            report=report,
            fragment_count=len(xray_paths),
        )

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ------------------------------------------------------------
# 입력 배치
# ------------------------------------------------------------

def _stage_fragments(
    paths: list[Path],
    names: list[str] | None,
    target_dir: Path,
) -> Path:
    """
    조각들을 원본 이름으로 한 폴더에 모은다.

    엔진은 --fragments 로 폴더를 받아 그 안의 이미지를 모두
    조각으로 읽는다. 따라서 이 폴더에는 조각만 있어야 하며
    컬러 기준 이미지가 섞이면 안 된다.

    파일명은 원본을 유지한다. 배치 정보의 fileName이
    조각과 변환행렬을 잇는 키이기 때문이다.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    used: set[str] = set()

    for index, path in enumerate(paths):
        original = (
            names[index]
            if names and index < len(names) and names[index]
            else path.name
        )

        safe = Path(original).name

        if safe in used:
            safe = (
                f"{Path(safe).stem}_{index}{Path(safe).suffix}"
            )

        used.add(safe)

        shutil.copy2(path, target_dir / safe)

    return target_dir


def _stage_reference(
    path: Path,
    name: str | None,
    work_dir: Path,
) -> Path:
    """
    컬러 기준 이미지를 조각 폴더 바깥에 둔다.

    조각 폴더 안에 두면 엔진이 이것까지 조각으로 인식한다.
    """
    safe = Path(name or path.name).name
    target = work_dir / f"reference_{safe}"

    shutil.copy2(path, target)

    return target


# ------------------------------------------------------------
# 엔진 실행
# ------------------------------------------------------------

def _execute(
    command: list[str],
    cwd: Path,
    work_dir: Path,
) -> dict | None:
    """
    엔진을 실행하고 표준출력의 보고서를 파싱한다.

    엔진은 성공 시 보고서 JSON을 표준출력에 찍고,
    실패 시 [ERROR] 로 시작하는 줄을 표준오류에 남긴다.

    타임아웃을 두는 이유는, 엔진이 매칭에 실패해 무한정
    도는 경우 워커 스레드가 영구 점유되기 때문이다.
    """
    log_dir = work_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    stdout_path = log_dir / "stitch.stdout.log"
    stderr_path = log_dir / "stitch.stderr.log"

    print(f"[STITCH] 실행: {' '.join(command)}")

    try:
        with (
            stdout_path.open("w", encoding="utf-8") as out,
            stderr_path.open("w", encoding="utf-8") as err,
        ):
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                stdout=out,
                stderr=err,
                text=True,
                timeout=config.STITCH_TIMEOUT_SECONDS,
                check=False,
            )

    except subprocess.TimeoutExpired:
        raise StitchError(
            f"결합이 제한 시간({config.STITCH_TIMEOUT_SECONDS}초)을 "
            "초과했습니다. 조각 수를 줄이거나 "
            "XRAY_STITCH_TIMEOUT을 늘리십시오."
        )

    except OSError as error:
        raise StitchError(
            f"결합 엔진을 실행하지 못했습니다: {error}"
        )

    if completed.returncode != 0:
        raise StitchError(
            f"결합 엔진이 실패했습니다 "
            f"(returnCode={completed.returncode}). "
            f"{_read_tail(stderr_path)}"
        )

    print("[STITCH] 엔진 실행 완료")

    return _parse_stdout_report(stdout_path)


def _parse_stdout_report(stdout_path: Path) -> dict | None:
    """
    표준출력에 찍힌 보고서 JSON을 읽는다.

    엔진이 진행 로그를 함께 찍을 수 있으므로, 마지막 JSON
    객체만 골라낸다. 파싱에 실패해도 결합 자체는 성공으로 둔다.
    """
    if not stdout_path.is_file():
        return None

    text = stdout_path.read_text(
        encoding="utf-8", errors="replace"
    ).strip()

    if not text:
        return None

    start = text.find("{")

    if start < 0:
        return None

    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


# ------------------------------------------------------------
# 산출물 회수
# ------------------------------------------------------------

def _collect_result(
    output_dir: Path,
    artifact_id: str,
    report: dict | None,
    fragment_count: int,
) -> dict:
    """
    엔진 산출물을 응답 규약에 맞춰 정리한다.

    변환행렬 키는 transform이다(엔진은 affineMatrix 등으로 저장).
    """
    fragments = _collect_fragments(output_dir, report)

    assembled_path = _find_assembled(output_dir)

    if assembled_path is None:
        raise StitchError(
            "결합본 이미지를 찾지 못했습니다. "
            f"산출 폴더: {output_dir}"
        )

    encoded, width, height, scale = _encode_image(assembled_path)

    matched_count = sum(
        1 for f in fragments if f.get("matched")
    )

    return {
        "compositeImage": encoded,
        "compositeFormat": "png",
        "fragments": fragments,
        "summary": {
            "artifactId": artifact_id,
            "totalFragments": fragment_count,
            "matchedCount": matched_count,
            "canvasWidth": width,
            "canvasHeight": height,
            # 축소 반환 시 좌표 환산 계수.
            # 1.0이면 원본 해상도 그대로다.
            "previewScale": scale,
        },
    }


def _collect_fragments(
    output_dir: Path,
    report: dict | None,
) -> list[dict]:
    """
    조각별 배치 정보를 모은다.

    엔진 보고서(표준출력)와 산출 폴더의 JSON 파일 양쪽을 본다.
    스키마가 확정 전이므로 흔한 키 변형을 모두 흡수한다.
    """
    raw_items = _extract_list(report)

    if not raw_items:
        for name in ("layout.json", "report.json", "result.json"):
            candidate = output_dir / name

            if not candidate.is_file():
                continue

            try:
                data = json.loads(
                    candidate.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                continue

            raw_items = _extract_list(data)

            if raw_items:
                break

    fragments = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        transform = _pick(
            item,
            "affineMatrix", "affine_matrix",
            "transform", "matrix", "M",
        )

        fragment = {
            "fileName": _pick(
                item,
                "fileName", "file_name", "name",
                "image", "fragment",
            ),
            "transform": transform,
            "matched": transform is not None,
            "cropBBoxXYWH": _pick(
                item,
                "originalCropBBoxXYWH",
                "original_crop_bbox_xywh",
                "cropBBoxXYWH", "bbox",
            ),
            "subfragmentIndex": _pick(
                item,
                "subfragmentIndex", "subfragment_index",
                "index",
            ),
        }

        for key in ("rotationDeg", "rotation_deg", "angle"):
            if key in item and item[key] is not None:
                fragment["rotationDeg"] = item[key]
                break

        fragments.append(fragment)

    print(f"[STITCH] 배치 정보 {len(fragments)}건 수집")

    return fragments


def _find_assembled(output_dir: Path) -> Path | None:
    """결합본 이미지를 찾는다."""
    for name in ASSEMBLED_NAME_CANDIDATES:
        candidate = output_dir / name

        if candidate.is_file():
            return candidate

    images = [
        path
        for path in output_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in IMAGE_SUFFIXES
    ]

    if not images:
        return None

    # 결합본은 조각보다 항상 크므로 최대 크기를 고른다.
    return max(images, key=lambda p: p.stat().st_size)


def _encode_image(
    path: Path,
) -> tuple[str, int, int, float]:
    """
    결합본을 base64로 인코딩한다.

    이 서비스는 파일을 보관하지 않으므로 경로를 돌려줘도
    Spring이 읽을 수 없다. 따라서 본문에 실어 보낸다.

    결합본은 5000px 이상이라 base64가 수십 MB가 될 수 있다.
    XRAY_STITCH_RETURN_MAX_SIDE를 지정하면 긴 변을 그 값으로
    줄여 반환하고, previewScale로 환산 계수를 함께 준다.
    좌표는 항상 원본(canvasWidth/Height) 기준이다.
    """
    import cv2
    import numpy as np

    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)

    if image is None:
        raise StitchError(
            f"결합본을 읽을 수 없습니다: {path}"
        )

    height, width = image.shape[:2]
    max_side = config.STITCH_RETURN_MAX_SIDE
    scale = 1.0

    if max_side > 0 and max(height, width) > max_side:
        scale = max_side / float(max(height, width))

        image = cv2.resize(
            image,
            (int(width * scale), int(height * scale)),
            interpolation=cv2.INTER_AREA,
        )

    ok, buffer = cv2.imencode(".png", image)

    if not ok:
        raise StitchError("결합본 인코딩에 실패했습니다.")

    encoded = base64.b64encode(
        np.asarray(buffer).tobytes()
    ).decode("ascii")

    print(
        f"[STITCH] 결합본 {width}x{height} | "
        f"scale={scale:.3f} | base64 {len(encoded) // 1024}KB"
    )

    return encoded, width, height, scale


# ------------------------------------------------------------
# 엔진 리소스 해석
# ------------------------------------------------------------

def _resolve_script() -> Path:
    """
    엔진 실행 스크립트를 찾는다.

    엔진 저장소가 scripts/ 하위에 두는 구조일 수 있어
    두 배치를 모두 확인한다.
    """
    name = config.STITCH_ENGINE_SCRIPT

    candidates = [
        config.STITCH_ENGINE_DIR / name,
        config.STITCH_ENGINE_DIR / "scripts" / name,
        config.STITCH_ENGINE_DIR / "bin" / name,
    ]

    return _first_file(candidates, f"엔진 스크립트({name})")


def _resolve_assembler_package() -> Path:
    """
    xray_assembler 패키지를 찾는다.

    엔진은 스크립트 단독으로 돌지 않는다. 이 패키지가 없으면
    subprocess가 ImportError로 죽는데, 그 오류는 로그 파일에만
    남아 원인 파악이 오래 걸리므로 실행 전에 미리 잡는다.

    스크립트가 scripts/ 하위에 있으면 패키지는 그 상위에 있다.
    """
    candidates = [
        config.STITCH_ENGINE_DIR / "xray_assembler",
        config.STITCH_ENGINE_DIR / "scripts" / "xray_assembler",
    ]

    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()

    checked = ", ".join(str(p) for p in candidates)

    raise StitchError(
        f"엔진 패키지(xray_assembler)를 찾을 수 없습니다. "
        f"확인 경로: {checked}"
    )


def _resolve_config(config_name: str) -> Path:
    """
    결합 설정 JSON을 찾는다.

    실제 설정 파일명은
    config.batch_fast.color_slot_voronoi_all_fragments_v13_conservative.json
    처럼 길다. 짧은 별칭(v13_conservative)으로도 찾을 수 있도록
    정확한 이름을 먼저 보고, 없으면 이름을 포함하는 파일을 찾는다.
    """
    name = Path(config_name).name

    search_dirs = [
        config.STITCH_CONFIG_DIR,
        config.STITCH_ENGINE_DIR / "configs",
        config.STITCH_ENGINE_DIR,
    ]

    # 1) 정확한 이름
    exact = []

    for base in search_dirs:
        exact.extend([
            base / name,
            base / f"{name}.json",
            base / f"config.{name}.json",
        ])

    for candidate in exact:
        if candidate.is_file():
            return candidate.resolve()

    # 2) 이름을 포함하는 파일
    stem = name.removesuffix(".json")

    for base in search_dirs:
        if not base.is_dir():
            continue

        matches = sorted(
            path
            for path in base.glob("*.json")
            if stem in path.name
        )

        if matches:
            return matches[0].resolve()

    checked = ", ".join(str(p) for p in search_dirs)

    raise StitchError(
        f"결합 설정({config_name})을 찾을 수 없습니다. "
        f"확인 폴더: {checked}"
    )


def _first_file(candidates: list[Path], label: str) -> Path:
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    checked = ", ".join(str(p) for p in candidates)

    raise StitchError(
        f"{label}을 찾을 수 없습니다. 확인 경로: {checked}"
    )


# ------------------------------------------------------------
# 소소한 유틸
# ------------------------------------------------------------

def _extract_list(raw) -> list:
    if isinstance(raw, list):
        return raw

    if isinstance(raw, dict):
        for key in (
            "fragments", "placements", "items",
            "pieces", "layout", "results",
        ):
            value = raw.get(key)

            if isinstance(value, list):
                return value

    return []


def _pick(item: dict, *keys):
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]

    return None


def _read_tail(path: Path, limit: int = 2000) -> str:
    if not path.exists():
        return ""

    return path.read_text(
        encoding="utf-8", errors="replace"
    )[-limit:]
