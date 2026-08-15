"""
전문가용 1차 상태조사 문안 생성

Colab 셀 13의 create_llm_report를 서버용으로 이관했다.

핵심 설계
- 탐지 좌표만으로는 GPT가 영상을 볼 수 없으므로
  박스를 그린 이미지를 메모리에서 생성해 함께 전달한다
- 전체 영역을 상세 서술하면 출력 토큰을 초과하므로
  신뢰도 상위 영역만 개별 서술하고 나머지는 통계로 요약한다
"""

import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image

from app import config


# ------------------------------------------------------------
# 시스템 프롬프트
# ------------------------------------------------------------

EXPERT_INSTRUCTIONS = """
당신은 문화유산 보존과학자의 X-ray 및 표면 상태조사를
보조하는 분석 문서 작성 AI이다.

당신의 역할은 결함을 확정 진단하거나 보존처리 방법을
결정하는 것이 아니다.

제공된 X-ray 영상, 컬러 2D 영상, AI 탐지 데이터를
근거로 문화유산 보존처리 전문가가 검토하고 수정할 수 있는
1차 상태조사 초안을 작성해야 한다.

[AI 탐지 결과의 성격]

본 시스템의 AI는 균열, 부식, 결손 등을 판정하지 않는다.
주변과 밝기 또는 형태가 다른 영역을 탐지하여
전문가가 검토할 후보로 제시할 뿐이다.

따라서 탐지된 영역은 다음 중 어느 것이든 될 수 있다.

- 실제 균열이나 손상
- 유물 고유의 제작 흔적
- 촬영 또는 스캔 과정의 영상 특성
- 조각 경계나 결합 흔적

판정은 반드시 전문가가 수행해야 한다.

[현재 데이터 구조]

현재 업무 API가 전달하는 영역은 원본 조각 탐지와 결합본 탐지를
그대로 나열한 결과가 아니라, layout.final.json으로 좌표를 변환해
최종 결합본 좌표계에서 중복을 통합한 전문가 검수 결과이다.

- 모든 bbox는 최종 결합본 좌표계에 있다.
- review_decision이 DAMAGE인, 즉 전문가가 정상으로 제외하지 않은
  영역만 문안 생성 입력에 포함된다.
- origin_type은 탐지·매핑 근거를 나타낸다.
  - MATCHED: 원본 조각과 최종 결합본에서 대응이 확인된 후보
  - ASSEMBLED_ONLY: 최종 결합본 탐지만으로 남은 후보
  - SOURCE_ONLY: 원본 조각 탐지를 최종 좌표계로 투영했으나
    결합본 탐지와 대응되지 않은 후보
- origin_type이 없는 구버전 입력에만 analysis_target 구분을 사용한다.

[분석 자료의 구분]

1. 최종 결합 X-ray
- 최종 좌표계와 전체 형상을 확인하는 주 대상 영상이다.

2. X-ray 원본 조각
- 결합 전 영상 특징을 대조하기 위한 보조 자료이다.
- 현재 문안 입력에는 원본 조각별 bbox가 별도로 제공되지 않을 수 있다.

3. 컬러 2D 이미지
- 표면에서 직접 관찰되는 특징만 확인하기 위한 참고 영상이다.

[분석 원칙]

- 탐지 결과를 확정된 결함이나 진단으로 표현하지 않는다.
- '이상 영역', '검토 필요 영역', '관찰됨',
  '전문가 확인 필요' 등의 표현을 사용한다.
- 결합 완료 X-ray의 조각 경계, 실제 파손 간격,
  중첩 영역, 회전·리사이즈 보간 흔적을 결함으로 단정하지 않는다.
- X-ray 명암 차이만으로 균열, 부식, 충전재,
  이물질 또는 제작기법을 확정하지 않는다.
- 원본 조각과 결합본의 방향 및 배율이 같다고 가정하지 않는다.
- origin_type이 MATCHED인 경우에만 원본·결합본 탐지의 대응 근거가
  있다고 기록한다. 이것도 손상 종류나 원인을 확정하는 근거는 아니다.
- ASSEMBLED_ONLY와 SOURCE_ONLY를 서로 동일한 영역으로 연결하지 않는다.
- X-ray와 컬러 2D 이미지의 촬영 방향이 같다고 가정하지 않는다.
- 위치 대응이 확인되지 않은 X-ray 이상 영역과
  컬러 표면 손상을 동일한 손상으로 연결하지 않는다.
- 사진만으로 재질, 제작기법, 손상 원인과
  발생 시기를 확정하지 않는다.
- 제공된 영상에 없는 정보를 일반적인 지식으로 보충하지 않는다.
- 판단 근거가 부족하면 '판단 불가',
  '추가 촬영 필요', '전문가 대조 확인 필요'라고 작성한다.
- 보존처리 방법은 확정적으로 지시하지 않는다.

[분량 원칙]

- 문서는 전문가가 실제로 읽고 활용할 분량이어야 한다.
- 개별 영역 서술은 제공된 상세 서술 대상 목록에 한정한다.
- 목록에 없는 영역은 통계와 분포 경향으로만 언급한다.
- 동일한 문장을 영역마다 반복하지 않는다.

[중요 구분]

현재 통합 결과에서는 결합본·원본 탐지 건수를 다시 합산하지 말고
origin_type별 분포를 근거로 작성해야 한다.

ASSEMBLED_ONLY 영역은 다음 가능성을 함께 검토한다.

- 결합 경계
- 실제 파손 간격
- 영상 중첩
- 밝기 차이
- 리사이즈 또는 회전 과정의 보간 흔적
- 실제 손상 후보

SOURCE_ONLY 영역은 원본에서 탐지되어 최종 좌표계로 투영되었지만
결합본 탐지와 대응되지 않았다는 점만 기록한다. 결합본에서 실제로
보이는 손상이라고 단정하지 않는다.

최종 결과는 문화유산 보존처리 전문가가 수정하는
1차 상태조사 초안이어야 한다.
"""


# ------------------------------------------------------------
# 이미지 유틸
# ------------------------------------------------------------

def _read_bgr(path: Path) -> Optional[np.ndarray]:
    try:
        binary = np.fromfile(str(path), dtype=np.uint8)
        return cv2.imdecode(binary, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _to_data_url(image_bgr: np.ndarray) -> str:
    """
    OpenCV 이미지를 JPEG base64 data URL로 변환한다.
    API 요청 크기를 줄이기 위해 긴 변을 1800px로 제한한다.
    """
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)

    if max(image.size) > config.LLM_IMAGE_MAX_SIDE:
        image.thumbnail(
            (
                config.LLM_IMAGE_MAX_SIDE,
                config.LLM_IMAGE_MAX_SIDE,
            ),
            Image.Resampling.LANCZOS,
        )

    buffer = BytesIO()
    image.save(
        buffer, format="JPEG", quality=88, optimize=True
    )

    encoded = base64.b64encode(
        buffer.getvalue()
    ).decode("utf-8")

    return f"data:image/jpeg;base64,{encoded}"


def _region_color(confidence: float):
    """낮음 노랑, 중간 주황, 높음 빨강."""
    if confidence < 0.20:
        return (0, 255, 255)
    if confidence < 0.40:
        return (0, 165, 255)
    return (0, 0, 255)


def render_annotated(
    image_bgr: np.ndarray,
    regions: list,
) -> np.ndarray:
    """
    탐지 박스를 그린 이미지를 만든다.

    이 이미지는 응답에 포함하지 않고 LLM 입력으로만 쓴다.
    GPT가 좌표만으로는 영상을 볼 수 없기 때문이다.
    """
    annotated = image_bgr.copy()
    height, width = annotated.shape[:2]

    scale = max(width, height) / 1000.0
    thickness = max(2, int(round(2 * scale)))
    font_scale = max(0.5, 0.55 * scale)
    font_thickness = max(1, int(round(1.5 * scale)))

    for r in regions:
        bbox = r.get("bbox") or {}

        try:
            x1 = int(round(float(bbox["x1"])))
            y1 = int(round(float(bbox["y1"])))
            x2 = int(round(float(bbox["x2"])))
            y2 = int(round(float(bbox["y2"])))
        except (KeyError, TypeError, ValueError):
            continue

        confidence = r.get("confidence")
        conf = float(confidence) if confidence is not None else None
        color = _region_color(conf) if conf is not None else (0, 165, 255)

        cv2.rectangle(
            annotated, (x1, y1), (x2, y2), color, thickness
        )

        label = str(r.get("regionId", ""))

        if conf is not None:
            label = f"{label} {conf:.2f}"

        (tw, th), baseline = cv2.getTextSize(
            label,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            font_thickness,
        )

        lx = max(0, min(x1, width - tw - 10))
        ly = max(th + 10, y1)

        cv2.rectangle(
            annotated,
            (lx, ly - th - 8),
            (lx + tw + 8, ly + baseline + 2),
            (0, 0, 0),
            -1,
        )

        cv2.putText(
            annotated,
            label,
            (lx + 4, ly - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (255, 255, 255),
            font_thickness,
            cv2.LINE_AA,
        )

    return annotated


# ------------------------------------------------------------
# LLM 입력 구성
# ------------------------------------------------------------

def build_payload(
    regions: list,
    top_assembled: int,
    top_fragment: int,
):
    """
    전체 영역을 상세 서술하면 출력 토큰을 초과하므로
    대상별 신뢰도 상위 영역만 상세 서술 대상으로 전달하고
    나머지는 통계로 요약한다.

    프롬프트의 "최대 N개 작성" 지시와 여기서 보내는 건수를
    일치시켜야 결과가 흔들리지 않는다.
    """
    unified = [r for r in regions if r.get("originType")]
    assembled = sorted(
        [
            r for r in regions
            if r.get("analysisTarget") == "결합 완료본"
        ],
        key=lambda r: -float(r.get("confidence") or 0),
    )

    fragment = sorted(
        [
            r for r in regions
            if r.get("analysisTarget") != "결합 완료본"
        ],
        key=lambda r: -float(r.get("confidence") or 0),
    )

    def simplify(items):
        simplified = []

        for r in items:
            bbox = r.get("bbox") or {}
            bbox_final = {
                key: bbox.get(key)
                for key in ("x1", "y1", "x2", "y2")
                if bbox.get(key) is not None
            }
            item = {
                "region_id": r.get("regionId"),
                "analysis_target": r.get("analysisTarget"),
                "xray_file": r.get("fileName"),
                "origin_type": r.get("originType"),
                "review_decision": r.get("reviewDecision"),
                "bbox_final": bbox_final or None,
                "confidence": r.get("confidence"),
                "position": r.get("position"),
                "area_ratio_percent": r.get(
                    "areaRatioPercent"
                ),
                "user_note": r.get("userNote"),
                "source_observations": (
                    r.get("sourceObservations")
                    or bbox.get("sourceObservations")
                ),
                "mapping_status": (
                    r.get("mappingStatus")
                    or bbox.get("mappingStatus")
                ),
                "seam_coverage": bbox.get("seamCoverage"),
                "overlap_coverage": bbox.get("overlapCoverage"),
            }

            simplified.append({
                key: value for key, value in item.items()
                if value is not None and value != ""
            })

        return simplified

    if unified:
        # 현재 workflow에서는 모든 영역이 최종 결합본 좌표계로 통합된다.
        # 과거의 결합본/원본 상세 건수 설정은 전체 상세 건수 상한으로만 쓴다.
        detail_limit = top_assembled + top_fragment
        detail = simplify(sorted(
            unified,
            key=lambda r: -float(r.get("confidence") or 0),
        )[:detail_limit])
    else:
        detail = (
            simplify(assembled[:top_assembled])
            + simplify(fragment[:top_fragment])
        )

    def conf_range(items):
        if not items:
            return None

        values = [
            float(r["confidence"])
            for r in items
            if r.get("confidence") is not None
        ]

        if not values:
            return None

        return {
            "min": round(min(values), 3),
            "max": round(max(values), 3),
        }

    position_counts = {}

    for r in assembled:
        key = r.get("position", "미상")
        position_counts[key] = (
            position_counts.get(key, 0) + 1
        )

    file_counts = {}

    for r in fragment:
        key = r.get("fileName", "미상")
        file_counts[key] = file_counts.get(key, 0) + 1

    origin_counts = {}

    for r in regions:
        key = r.get("originType") or "구버전_미분류"
        origin_counts[key] = origin_counts.get(key, 0) + 1

    stats = {
        "통합_검수결과": {
            "전문가_포함_영역수": len(regions),
            "상세서술_대상": len(detail),
            "매핑_근거별_분포": origin_counts,
            "좌표계": "최종 결합본",
        },
    }

    if not unified:
        stats.update({
            "결합본": {
                "총_영역수": len(assembled),
                "상세서술_대상": min(
                    top_assembled, len(assembled)
                ),
                "신뢰도_범위": conf_range(assembled),
                "위치별_분포": position_counts,
            },
            "원본_조각": {
                "총_영역수": len(fragment),
                "상세서술_대상": min(
                    top_fragment, len(fragment)
                ),
                "신뢰도_범위": conf_range(fragment),
                "파일별_탐지건수": file_counts,
            },
        })

    return detail, stats


# ------------------------------------------------------------
# 프롬프트
#
# 실제 서비스에서는 하나의 실무자용 요약 문안만 사용한다.
# 상세본 분기는 제거하고, 최종 결합 X-ray/원본 조각/전문가 검수 결과를
# 한 번에 근거로 삼아 최종보고서에도 재사용하기 쉬운 문안을 만든다.
# ------------------------------------------------------------


def _common_header(artifact_type, material, detail, stats):
    return f"""
[유물 기본정보]
- 유물 유형: {artifact_type or "정보 없음"}
- 재질: {material or "정보 없음"}

[전문가 검수 결과 통계]
{json.dumps(stats, ensure_ascii=False, indent=2)}

[주요 검토 영역]
아래 목록은 AI가 탐지한 후보 중 전문가 검수에서 DAMAGE로 남긴 영역을
최종 결합본 좌표계로 통합한 결과이다. user_note가 있으면 전문가 소견이므로
우선적으로 반영한다. origin_type, mapping_status, seam_coverage,
overlap_coverage, source_observations는 결합/매핑 근거를 판단하는 보조 정보다.

{json.dumps(detail, ensure_ascii=False, indent=2)}
"""


def build_summary_prompt(artifact_type, material, detail, stats):
    """보존처리 실무자가 바로 검토·수정할 수 있는 X-ray 상태조사 요약문."""

    return f"""
다음 자료를 바탕으로 문화유산 보존처리 실무자가 실제 기록과 후속 검토에
사용할 수 있는 X-ray 상태조사 초안을 작성하라. 이 문안은 최종 통합보고서의
X-ray 조사 근거로 그대로 재사용될 수 있으므로, 일반적인 설명보다 실제
결합 결과와 전문가 검수 결과를 우선한다.
{_common_header(artifact_type, material, detail, stats)}

[핵심 작성 원칙]
1. 모든 서술은 제공된 최종 결합 X-ray, 원본 조각, 컬러 참고영상, 전문가 검수
   결과에서 확인 가능한 사실만 사용한다. 자료에 없는 결함명·원인·처리법을
   만들어내지 않는다.
2. 현재 입력 영역은 전문가가 DAMAGE로 남긴 검토 대상이다. 이를 '확정 결함'으로
   표현하지 말고 '전문가 검수에서 이상으로 포함한 영역', '추가 확인 대상'으로
   표현한다.
3. bbox는 모두 최종 결합본 좌표계다. 위치(position), bbox_final, user_note가
   제공되면 실제 영역 설명에 반영한다.
4. MATCHED는 원본 조각과 최종 결합본의 대응 근거가 있는 영역이다.
   ASSEMBLED_ONLY는 결합본에서만 남은 영역, SOURCE_ONLY는 원본 탐지를
   최종 좌표계로 투영했지만 결합본 탐지와 대응되지 않은 영역이다.
   origin_type을 손상 종류·심각도·확률로 해석하지 않는다.
5. seam_coverage 또는 overlap_coverage가 높거나 결합 경계와 가까운 영역은
   실제 손상과 결합/중첩/보간 영향의 구분이 필요하다고 명시한다. 수치가 없으면
   임의로 만들지 않는다.
6. source_observations가 있으면 원본 조각에서 동일 특징이 존재했는지와
   최종 결합본 매핑 근거를 함께 기술한다. 없으면 원본 대응을 추정하지 않는다.
7. 컬러 이미지는 표면에서 직접 보이는 특징만 참고한다. X-ray 위치와 대응이
   확인되지 않으면 동일 손상으로 연결하지 않는다.
8. 단순히 탐지 건수를 나열하지 말고, 실무자가 '어디를 다시 봐야 하는지',
   '결합 영향 가능성이 있는지', '원본 조각 대조가 필요한지'를 빠르게 판단할 수
   있게 작성한다.
9. 전체 분량은 공백 포함 약 1,200~1,800자. 동일 표현을 반복하지 않는다.

[출력 형식]

[AI 기반 X-ray 상태조사 초안]

1. 조사·결합 결과 요약
- 최종 결합 X-ray를 기준으로 전체 형상/명암/조각 경계의 관찰사항을 2~3문장
- 전문가 검수에서 포함한 영역 수와 origin_type 분포를 1~2문장

2. 주요 이상영역 검토
- 주요 검토 영역을 실제 입력값에 근거해 최대 8건까지 작성
- 형식: '영역 ID — 위치: 관찰/검수 소견 · 결합/매핑 근거 · 확인 필요사항'
- user_note가 있으면 반드시 반영
- seam/overlap/source_observations가 있으면 실무적 의미를 함께 제시

3. 원본 조각·컬러영상 대조
- MATCHED/SOURCE_ONLY 영역 중 원본 대조가 의미 있는 사항
- 컬러영상에서 직접 확인되는 표면 특징이 있을 때만 기록
- 대응이 불확실한 항목은 명확히 '대응 확인 필요'로 표시

4. 실무자 확인 우선순위
- 재확인이 필요한 영역을 우선순위 형태로 최대 3개 제시
- 예: 원본 조각 재대조, 결합 경계 영향 확인, 추가 촬영/육안 확인
- 보존처리 방법을 확정적으로 지시하지 않는다.

5. 최종보고서 인계 요약
- 최종 통합보고서에 바로 넣을 수 있도록 핵심 결과를 3~4문장으로 정리
- 실제 확인된 결과와 불확실성을 함께 남긴다.
- 마지막 문장: '본 결과는 AI 탐지와 전문가 검수 결과를 바탕으로 한 상태조사
  보조자료이며, 최종 손상 진단은 추가 확인을 거쳐야 한다.'
"""


# ------------------------------------------------------------
# 문안 생성
# ------------------------------------------------------------

def generate_report(
    regions: list,
    assembled_path: Optional[Path] = None,
    fragment_paths: Optional[list] = None,
    rgb_paths: Optional[list] = None,
    artifact_type: str = "",
    material: str = "",
) -> dict:
    """
    OpenAI 멀티모달 API로 상태조사 문안을 생성한다.

    Returns
    -------
    dict
        report : 생성된 문안
        style : 사용한 스타일
        detailCount : 개별 서술 대상 영역 수
        stats : 통계 요약
    """
    if not config.OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않았습니다. "
            "환경변수를 확인하세요."
        )

    # 지연 임포트: 키가 없는 환경에서도 서버가 뜨도록
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)

    # 실서비스는 실무자용 요약 문안 하나만 사용한다.
    top_assembled = config.LLM_SUMMARY_TOP_ASSEMBLED
    top_fragment = config.LLM_SUMMARY_TOP_FRAGMENT
    max_tokens = config.LLM_SUMMARY_MAX_TOKENS
    build = build_summary_prompt

    detail, stats = build_payload(
        regions,
        top_assembled=top_assembled,
        top_fragment=top_fragment,
    )

    content = [
        {
            "type": "input_text",
            "text": build(
                artifact_type, material, detail, stats
            ),
        }
    ]

    # --------------------------------------------------------
    # 결합 완료본 원본 + 박스 표시본
    # --------------------------------------------------------

    if assembled_path is not None:
        image_bgr = _read_bgr(assembled_path)

        if image_bgr is not None:
            content.append({
                "type": "input_text",
                "text": (
                    "다음 이미지는 결합·수정이 완료된 "
                    "X-ray 완성본 원본이다."
                ),
            })

            content.append({
                "type": "input_image",
                "image_url": _to_data_url(image_bgr),
                "detail": "high",
            })

            assembled_regions = [
                r for r in regions
                if r.get("analysisTarget") == "결합 완료본"
            ]

            annotated = render_annotated(
                image_bgr, assembled_regions
            )

            content.append({
                "type": "input_text",
                "text": (
                    "다음 이미지는 AI 이상영역이 표시된 "
                    "결합 완료 X-ray이다."
                ),
            })

            content.append({
                "type": "input_image",
                "image_url": _to_data_url(annotated),
                "detail": "high",
            })

    # --------------------------------------------------------
    # 원본 조각 (요청 크기 제한으로 최대 6장)
    # --------------------------------------------------------

    for index, path in enumerate(
        (fragment_paths or [])[: config.LLM_MAX_IMAGES],
        start=1,
    ):
        image_bgr = _read_bgr(path)

        if image_bgr is None:
            continue

        content.append({
            "type": "input_text",
            "text": (
                f"X-ray 원본 조각 {index}: {path.name}"
            ),
        })

        content.append({
            "type": "input_image",
            "image_url": _to_data_url(image_bgr),
            "detail": "high",
        })

    # --------------------------------------------------------
    # 컬러 2D
    # --------------------------------------------------------

    for index, path in enumerate(
        (rgb_paths or [])[: config.LLM_MAX_IMAGES],
        start=1,
    ):
        image_bgr = _read_bgr(path)

        if image_bgr is None:
            continue

        content.append({
            "type": "input_text",
            "text": (
                f"실제 컬러 2D 이미지 {index}: {path.name}"
            ),
        })

        content.append({
            "type": "input_image",
            "image_url": _to_data_url(image_bgr),
            "detail": "high",
        })

    response = client.responses.create(
        model=config.OPENAI_MODEL,
        instructions=EXPERT_INSTRUCTIONS,
        input=[{"role": "user", "content": content}],
        max_output_tokens=max_tokens,
    )

    text = response.output_text

    return {
        "report": text,
        "style": "summary",
        "charCount": len(text),
        "detailCount": len(detail),
        "totalRegionCount": len(regions),
        "stats": stats,
        "model": config.OPENAI_MODEL,
    }