"""보고서 생성 그래프의 State.

conservation-guide-ai의 State/merge_results 패턴을 그대로 따른다: 노드마다
자기 섹션 키만 채워 넣고, merge_sections가 이를 누적한다.
"""

from typing import Annotated, Any

from typing_extensions import TypedDict


def merge_sections(current: dict[str, Any] | None, update: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(current or {})
    merged.update(update or {})
    return merged


class State(TypedDict, total=False):
    # ── 요청 시 전달받는 원본 데이터 (각 파트 API를 호출해 모은 결과) ──
    artifact_id: str
    relic_info: dict[str, Any]

    guide_result: dict[str, Any]  # GUIDE_TASK.result 그대로 (results.<stage>...)
    xray_report_text: str | None  # XRAY_JOB.report_text
    xray_regions: list[dict[str, Any]]  # XRAY_REGION 행 목록
    pottery_inspection: dict[str, Any] | None  # INSPECTION_RESULT_POTTERY 등가
    vca_assessment: dict[str, Any] | None  # ASSESSMENT_REPORT(VCA)

    # ── 노드별 산출물 (섹션 키: header/pre_investigation/disassembly/...) ──
    sections: Annotated[dict[str, Any], merge_sections]

    # ── 최종 조립 결과 ──
    report_json: dict[str, Any]
