"""
X-ray AI 서비스 통합 테스트

결합 파트를 합친 뒤 기존 기능이 깨지지 않았는지,
결합이 동작하는지 순서대로 확인한다.

Windows 셸에서 curl로 멀티파트와 한글 폼값을 보내면
인코딩이 깨지기 쉬워 스크립트로 만들었다.


사용법

    pip install requests
    python test_integration.py

    # 조각 폴더와 결합본을 지정하려면
    python test_integration.py --fragments samples --composite samples/composite.jpg


확인 순서

    1. 서비스 살아있는지
    2. 결함 탐지 회귀      ← 가장 중요. 결합본 6건이 기준
    3. 결합 엔진 준비 상태
    4. 결합 실행
    5. 결합본을 다시 탐지에 투입
"""

import argparse
import base64
import json
import sys
from pathlib import Path

import requests


BASE_URL = "http://localhost:8001"

# 회귀 기준값
#
# composite.jpg 를 conf=0.08 / imgsz=2048 / NMS IoU=0.5 로
# 분석했을 때 나오던 건수. 병합 후에도 같아야 한다.
EXPECTED_REGIONS = 6

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


def section(title):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def ok(message):
    print(f"  [통과] {message}")


def fail(message):
    print(f"  [실패] {message}")


def warn(message):
    print(f"  [주의] {message}")


# ------------------------------------------------------------
# 1. 서비스 상태
# ------------------------------------------------------------

def test_health(base_url):
    section("1. 서비스 상태")

    try:
        response = requests.get(f"{base_url}/health", timeout=10)
    except requests.exceptions.ConnectionError:
        fail(f"{base_url} 에 연결할 수 없습니다.")
        print("       docker compose ps 로 컨테이너 상태를 확인하십시오.")
        return False

    if response.status_code != 200:
        fail(f"HTTP {response.status_code}")
        return False

    data = response.json()
    print(f"       {json.dumps(data, ensure_ascii=False)}")

    if not data.get("modelLoaded"):
        fail("모델이 로드되지 않았습니다. 결함 탐지를 할 수 없습니다.")
        print("       docker compose logs xray-ai 로 확인하십시오.")
        return False

    ok(f"모델 로드됨 (device={data.get('device')})")

    if not data.get("llmEnabled"):
        warn("OPENAI_API_KEY 미설정. /report 만 사용할 수 없습니다.")

    return True


# ------------------------------------------------------------
# 2. 결함 탐지 회귀 (가장 중요)
# ------------------------------------------------------------

def test_anomaly_regression(base_url, composite_path):
    section("2. 결함 탐지 회귀 — 병합이 기존 기능을 깨뜨렸는지")

    if composite_path is None or not composite_path.is_file():
        warn("결합본 이미지가 없어 건너뜁니다.")
        print("       --composite 로 지정하면 회귀를 확인할 수 있습니다.")
        return None

    print(f"       대상: {composite_path.name}")

    with composite_path.open("rb") as image_file:
        response = requests.post(
            f"{base_url}/detect",
            files={"file": (composite_path.name, image_file)},
            # 한글 폼값은 requests가 UTF-8로 올바르게 인코딩한다
            data={"analysis_target": "결합 완료본"},
            timeout=300,
        )

    if response.status_code != 200:
        fail(f"HTTP {response.status_code}: {response.text[:300]}")
        return False

    result = response.json()
    count = len(result["regions"])
    summary = result["summary"]

    print(
        f"       해상도 {summary.get('imageWidth')}x"
        f"{summary.get('imageHeight')} | "
        f"추론 imgsz {summary.get('inferenceImgsz')} | "
        f"conf {summary.get('confidenceThreshold')}"
    )

    if count == EXPECTED_REGIONS:
        ok(f"탐지 {count}건 — 기준값과 일치. 회귀 없음")
        return True

    fail(f"탐지 {count}건 — 기준값 {EXPECTED_REGIONS}건과 다름")
    print("       결합 파트 병합이 탐지에 영향을 줬을 수 있습니다.")
    print("       config.py의 DEFAULT_CONF / NMS_IOU / IMGSZ_ASSEMBLED 를")
    print("       확인하십시오. 이 값들은 병합 시 건드리지 않았어야 합니다.")
    return False


# ------------------------------------------------------------
# 3. 결합 엔진 준비 상태
# ------------------------------------------------------------

def test_stitch_health(base_url):
    section("3. 결합 엔진 준비 상태")

    response = requests.get(f"{base_url}/stitch/health", timeout=10)

    if response.status_code != 200:
        fail(f"HTTP {response.status_code}: {response.text[:300]}")
        print("       /stitch/health 가 없으면 라우터가 반영되지 않은 것입니다.")
        return False

    data = response.json()

    for key in (
            "engineExists",
            "scriptExists",
            "assemblerPackageExists",
            "configExists",
    ):
        mark = "O" if data.get(key) else "X"
        print(f"       [{mark}] {key}")

    if data.get("stitchReady"):
        ok("엔진 준비 완료")
        print(f"       스크립트: {data.get('scriptPath')}")
        print(f"       설정    : {data.get('configPath')}")
        print(f"       제한시간: {data.get('timeoutSeconds')}초")
        return True

    fail("엔진이 준비되지 않았습니다.")
    print()
    print("       engine/ configs/ mappings/ 를 동료에게 받아")
    print("       ai-services/xray-ai/ 아래에 두고 다시 빌드하십시오.")
    print()
    print("       ai-services/xray-ai/")
    print("       ├── engine/     batch_assemble.py + xray_assembler/")
    print("       ├── configs/    config.v13_conservative.json")
    print("       └── mappings/   mapping.color_front.json")
    return False


# ------------------------------------------------------------
# 4. 결합 실행
# ------------------------------------------------------------

def test_stitch_run(
        base_url, fragment_paths, color_path, artifact_id,
        output_dir, fragments_dir=None,
):
    section("4. 결합 실행")

    if not fragment_paths:
        warn("조각 이미지가 없어 건너뜁니다.")
        print(f"       폴더: {fragments_dir}")

        if fragments_dir is None or not fragments_dir.is_dir():
            print("       → 폴더가 존재하지 않습니다.")
        else:
            names = [p.name for p in fragments_dir.iterdir()]
            if not names:
                print("       → 폴더가 비어 있습니다.")
            else:
                print(f"       → 폴더 내용: {names[:10]}")
                print("       → 이미지 확장자가 아니거나 이름에")
                print("          composite/color/reference 가")
                print("          들어가 제외되었습니다.")

        return None

    if color_path is None or not color_path.is_file():
        warn("컬러 기준 이미지가 없어 건너뜁니다.")
        print("       엔진은 컬러 완성본의 외곽 형태를 기준으로")
        print("       조각을 배치하므로 기준 이미지가 필요합니다.")
        print("       --color 로 지정하십시오.")
        return None

    print(f"       유물 ID: {artifact_id}")
    print(f"       조각 {len(fragment_paths)}장")
    print(f"       컬러 기준: {color_path.name}")
    print("       수 분이 걸릴 수 있습니다. 기다리십시오...")

    files = []
    handles = []

    try:
        for path in fragment_paths:
            handle = path.open("rb")
            handles.append(handle)
            files.append(("files", (path.name, handle)))

        color_handle = color_path.open("rb")
        handles.append(color_handle)
        files.append(
            ("color_files", (color_path.name, color_handle))
        )

        response = requests.post(
            f"{base_url}/stitch/run",
            files=files,
            data={"artifact_id": artifact_id},
            timeout=1800,
        )

    finally:
        for handle in handles:
            handle.close()

    if response.status_code != 200:
        fail(f"HTTP {response.status_code}")
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        print(f"       {str(detail)[:600]}")

        if response.status_code == 502:
            print()
            print("       502는 엔진 실행 실패입니다. 위 메시지의")
            print("       returnCode 와 stderr 내용을 확인하십시오.")
            print("       매핑 파일에 없는 artifact_id 인 경우가 많습니다.")
        return False

    result = response.json()
    summary = result["summary"]

    print(
        f"       캔버스 {summary['canvasWidth']}x"
        f"{summary['canvasHeight']}"
    )
    print(
        f"       조각 {summary['totalFragments']}장 중 "
        f"{summary['matchedCount']}장 배치됨"
    )

    # 변환행렬 확인
    matched = [
        f for f in result["fragments"] if f.get("transform")
    ]

    if matched:
        sample = matched[0]
        ok(f"변환행렬 수신 ({len(matched)}건)")
        print(f"       예: {sample['fileName']}")
        print(f"           {sample['transform']}")
    else:
        warn("변환행렬이 비어 있습니다.")
        print("       layout.json 키 이름이 다를 수 있습니다.")
        print("       stitcher.py의 _parse_layout 후보 키를 확인하십시오.")

    # 결합본 저장
    output_dir.mkdir(parents=True, exist_ok=True)
    composite_path = output_dir / f"{artifact_id}_composite.png"

    composite_path.write_bytes(
        base64.b64decode(result["compositeImage"])
    )

    size_mb = composite_path.stat().st_size / 1024 / 1024
    ok(f"결합본 저장: {composite_path} ({size_mb:.1f}MB)")

    if summary.get("previewScale", 1.0) < 1.0:
        warn(
            f"축소 반환됨 (previewScale="
            f"{summary['previewScale']:.3f}). "
            "좌표는 원본 기준입니다."
        )

    return composite_path


# ------------------------------------------------------------
# 5. 결합본을 다시 탐지에 투입
# ------------------------------------------------------------

def test_chain(base_url, composite_path):
    section("5. 연결 흐름 — 결합본을 탐지에 투입")

    if composite_path is None or not composite_path.is_file():
        warn("결합본이 없어 건너뜁니다.")
        return None

    with composite_path.open("rb") as image_file:
        response = requests.post(
            f"{base_url}/detect",
            files={"file": (composite_path.name, image_file)},
            data={"analysis_target": "결합 완료본"},
            timeout=300,
        )

    if response.status_code != 200:
        fail(f"HTTP {response.status_code}: {response.text[:300]}")
        return False

    result = response.json()
    ok(f"결합본에서 이상영역 {len(result['regions'])}건 탐지")
    print("       결합 → 탐지 전체 흐름이 이어집니다.")

    for region in result["regions"][:3]:
        print(
            f"       - {region.get('regionId')} "
            f"conf={region.get('confidence'):.3f} "
            f"{region.get('position')}"
        )

    return True


# ------------------------------------------------------------

def collect_fragments(directory, limit):
    if directory is None or not directory.is_dir():
        return []

    paths = sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.suffix.lower() in IMAGE_SUFFIXES
        and "composite" not in path.name.lower()
        and "color" not in path.name.lower()
        and "reference" not in path.name.lower()
    )

    return paths[:limit]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument(
        "--fragments",
        type=Path,
        default=Path("samples"),
        help="조각 이미지 폴더",
    )
    parser.add_argument(
        "--composite",
        type=Path,
        default=None,
        help="회귀 테스트용 기존 결합본",
    )
    parser.add_argument(
        "--color",
        type=Path,
        default=None,
        help="컬러 기준 이미지(유물 완성본 전면 사진)",
    )
    parser.add_argument(
        "--artifact-id",
        default="artifact_003",
        help="응답 라벨. 엔진에는 전달되지 않는다",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="첫 테스트에서 사용할 조각 수",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("test_output"),
    )
    args = parser.parse_args()

    composite = args.composite

    if composite is None:
        for guess in (
                args.fragments / "composite.jpg",
                args.fragments.parent / "composite.jpg",
                Path("samples") / "composite.jpg",
        ):
            if guess.is_file():
                composite = guess
                break

    color = args.color

    if color is None:
        for name in (
                "color.jpg", "color.png",
                "reference.jpg", "reference.png",
        ):
            guess = args.fragments / name
            if guess.is_file():
                color = guess
                break

    fragments = collect_fragments(args.fragments, args.limit)

    print(f"대상 서비스: {args.base_url}")
    print(f"조각 폴더  : {args.fragments}")
    print(f"회귀 결합본: {composite}")
    print(f"컬러 기준  : {color}")

    results = {}

    if not test_health(args.base_url):
        print("\n서비스가 준비되지 않아 중단합니다.")
        sys.exit(1)

    results["회귀"] = test_anomaly_regression(
        args.base_url, composite
    )

    engine_ready = test_stitch_health(args.base_url)
    results["엔진준비"] = engine_ready

    stitched = None

    if engine_ready:
        stitched = test_stitch_run(
            args.base_url,
            fragments,
            color,
            args.artifact_id,
            args.output,
            args.fragments,
        )
        results["결합"] = bool(stitched)

        if stitched:
            results["연결"] = test_chain(args.base_url, stitched)
    else:
        print()
        warn("엔진 미준비로 결합 테스트를 건너뜁니다.")
        print("       결함 탐지는 정상 동작하므로 회귀 결과만 유효합니다.")

    section("결과 요약")

    for name, value in results.items():
        if value is None:
            mark = "건너뜀"
        elif value:
            mark = "통과"
        else:
            mark = "실패"
        print(f"  {name:8s} {mark}")

    failed = [
        name
        for name, value in results.items()
        if value is False
    ]

    if failed:
        print(f"\n실패 항목: {', '.join(failed)}")
        sys.exit(1)

    print("\n모두 통과했습니다.")


if __name__ == "__main__":
    main()