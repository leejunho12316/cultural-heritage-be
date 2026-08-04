"""Gradio 도자기 육안조사 어시스턴트.

개선 사항 (코드 리뷰 반영)
- n_calls=1로 실행하면 합의 검증 없이 단일 VLM 결과만 쓰인다는 점을
  요약 문장에서 알려준다 (pottery_analyzer.build_report_sentence에서 처리).
- 범례/목록 외 명칭 표시가 무엇을 뜻하는지 하단 설명에 추가했다.
- 기본 조사(위치 마커만) / 상세 검토(근사 영역 포함) 두 가지 시각화
  모드를 라디오 버튼으로 선택할 수 있다.

개선 사항 (v9, 클로즈업 육안상태조사)
- 정밀 박스/마스크를 화면에 그리는 대신, 확정된 문양마다 원본에서 넉넉하게
  크롭한 클로즈업 이미지 + VLM이 평가한 보존 상태(마모/박락/변색/균열/오염)를
  갤러리로 보여준다. pottery_analyzer.analyze_pottery가 반환하는
  pattern_bundle["condition_gallery"]를 그대로 사용한다.
"""

from __future__ import annotations

import gradio as gr

from pottery_analyzer import (
    analyze_pottery,
    build_inspection_text,
    build_report_sentence,
    expand_names_to_keys,
    needs_human_review,
    render_selected_patterns,
)
from pottery_pattern_vlm_locator import MODEL_NAME


def build_condition_gallery_items(pattern_bundle: dict | None) -> list[tuple]:
    """pattern_bundle["condition_gallery"]를 Gradio Gallery가 받는
    (이미지, 캡션) 튜플 목록으로 변환한다."""
    if not pattern_bundle:
        return []

    items = []
    for entry in pattern_bundle.get("condition_gallery", []):
        condition = entry.get("condition", {})
        status = condition.get("condition_status", "평가 없음")
        description = condition.get("condition_description") or condition.get("error", "")
        issues = condition.get("issues") or []
        if issues:
            issue_lines = []
            for issue in issues:
                alt = issue.get("alternative_explanation")
                alt_text = f", 대안설명: {alt}" if alt else ""
                issue_lines.append(
                    f"  · {issue.get('issue_type', '?')} "
                    f"(확신도 {issue.get('confidence', '?')}{alt_text})"
                )
            issue_text = "\n" + "\n".join(issue_lines)
        else:
            issue_text = ""
        review_flag = " [사람 재검토 권장]" if condition.get("human_review_required") else ""
        caption = (
            f"[{entry.get('badge', '?')}] {entry.get('pattern_name', '?')} - "
            f"{status}{review_flag}\n{description}{issue_text}"
        )
        items.append((entry["crop"], caption))
    return items


def _review_badge_text(result: dict) -> str:
    """전문가 재검토 권장 여부를 발표/시연 화면에서 한눈에 보이도록 배지 문구로
    바꾼다. AI가 최종 승인/반려를 내리는 게 아니라 우선순위 힌트일 뿐이라는
    점을 문구에서도 분명히 한다."""
    if result.get("status") != "성공":
        return "[분석 실패] 결과를 생성하지 못했습니다."
    if needs_human_review(result):
        return "[전문가 재검토 권장] 이상 후보 또는 명칭 미확정 문양이 있어 확인이 필요합니다."
    return "[특이사항 없음] 현재까지 확인된 범위에서는 우선 검토가 필요한 소견이 없습니다."


def run_analysis(image_path: str, use_vlm_pattern: bool, n_calls: int):
    if image_path is None:
        return (
            "이미지를 업로드해주세요.",
            "",
            "",
            {},
            None,
            None,
            gr.update(choices=[], value=[]),
            [],
        )

    result, result_image, pattern_bundle = analyze_pottery(
        image_path,
        use_vlm_pattern=use_vlm_pattern,
        n_calls=int(n_calls),
    )
    summary = build_report_sentence(result)
    inspection_text = build_inspection_text(result)
    review_badge = _review_badge_text(result)

    if pattern_bundle is None:
        choices: list[str] = []
        selected: list[str] = []
    else:
        choices = sorted(pattern_bundle["by_name_all"].keys())
        selected = pattern_bundle.get("default_names", [])

    return (
        summary,
        review_badge,
        inspection_text,
        result,
        result_image,
        pattern_bundle,
        gr.update(choices=choices, value=selected),
        build_condition_gallery_items(pattern_bundle),
    )


def filter_pattern_view(
    pattern_bundle: dict | None,
    selected_names: list[str],
    include_low_confidence: bool,
    visualization_mode: str,
):
    if pattern_bundle is None:
        return None
    if not selected_names:
        return pattern_bundle["base_image"].convert("RGB")
    selected_keys = expand_names_to_keys(
        pattern_bundle,
        selected_names,
        include_low_confidence=include_low_confidence,
    )
    mode = "detail" if visualization_mode == "상세 검토(근사 영역 포함)" else "markers"
    return render_selected_patterns(
        pattern_bundle,
        selected_keys,
        visualization_mode=mode,
    )


def build_demo() -> gr.Blocks:
    with gr.Blocks(title="도자기 육안조사 어시스턴트") as demo:
        gr.Markdown("# 도자기 육안조사 어시스턴트 (MVP)")
        gr.Markdown(
            "완전/파편과 표면 광택은 로컬 RF 모델로 추정하고, 시대는 로컬 CNN(멀티태스크 "
            "모델의 era 헤드)으로 추정하며, "
            f"문양 종류·위치는 VLM({MODEL_NAME}) 반복 분석으로 보조합니다. "
            "기본 오버레이에는 여러 호출 중 다수가 위치를 일치시킨 문양만 표시합니다 "
            "(반복 호출 횟수를 1로 설정하면 합의 검증 없이 단일 결과만 사용됩니다). "
            "확정된 문양마다 클로즈업 크롭을 추가로 만들어 보존 상태(마모/박락/변색/균열/오염)를 "
            "평가합니다 - 문양 개수만큼 VLM 호출이 추가로 발생하니 비용에 참고하세요."
        )

        pattern_bundle_state = gr.State(None)

        with gr.Row():
            with gr.Column():
                image_input = gr.Image(type="filepath", label="도자기 사진 업로드")
                vlm_checkbox = gr.Checkbox(
                    value=True,
                    label="문양 분석 포함 (VLM 호출·비용 발생)",
                )
                n_calls_slider = gr.Slider(
                    minimum=1,
                    maximum=5,
                    value=3,
                    step=1,
                    label="문양 분석 반복 호출 횟수 (1이면 합의 검증 없음)",
                )
                run_button = gr.Button("분석 실행", variant="primary")
                pattern_select = gr.CheckboxGroup(
                    choices=[],
                    label="표시할 문양 종류 선택 (분석 재호출 없음)",
                )
                show_low_confidence = gr.Checkbox(
                    value=False,
                    label="낮은 합의 후보도 함께 표시",
                )
                visualization_mode = gr.Radio(
                    choices=["기본 조사(위치 마커만)", "상세 검토(근사 영역 포함)"],
                    value="기본 조사(위치 마커만)",
                    label="문양 시각화 방식",
                )

            with gr.Column():
                review_badge_output = gr.Textbox(
                    label="검토 우선순위", lines=1, show_label=True
                )
                report_output = gr.Textbox(
                    label="조사보고서(육안 Text) - 유물 외형/주요 문양/상태 이상 후보/종합 의견",
                    lines=12,
                )
                summary_output = gr.Textbox(label="한 줄 요약(개발자용)", lines=3)
                image_output = gr.Image(
                    label="문양 위치 표시 (기본은 마커만, 상세 검토에서 근사 영역 표시)"
                )
                condition_gallery = gr.Gallery(
                    label="문양별 클로즈업 육안상태조사 (확정된 문양만, 자세히 보려면 클릭)",
                    columns=3,
                    height="auto",
                )
                detail_output = gr.JSON(label="상세 결과")

        run_button.click(
            fn=run_analysis,
            inputs=[image_input, vlm_checkbox, n_calls_slider],
            outputs=[
                summary_output,
                review_badge_output,
                report_output,
                detail_output,
                image_output,
                pattern_bundle_state,
                pattern_select,
                condition_gallery,
            ],
        )

        pattern_select.change(
            fn=filter_pattern_view,
            inputs=[
                pattern_bundle_state,
                pattern_select,
                show_low_confidence,
                visualization_mode,
            ],
            outputs=[image_output],
        )

        show_low_confidence.change(
            fn=filter_pattern_view,
            inputs=[
                pattern_bundle_state,
                pattern_select,
                show_low_confidence,
                visualization_mode,
            ],
            outputs=[image_output],
        )

        visualization_mode.change(
            fn=filter_pattern_view,
            inputs=[
                pattern_bundle_state,
                pattern_select,
                show_low_confidence,
                visualization_mode,
            ],
            outputs=[image_output],
        )

        gr.Markdown(
            "---\n"
            "- **영역 탐지 방식**: Grounding DINO와 SAM 2는 유물 전체 실루엣을 추출하며, "
            "개별 문양 영역(대형 연속 문양의 사각형, 띠 문양의 반투명 영역)은 그 실루엣 "
            "내부에서 색상 대비와 위치 정보를 이용해 추정합니다 - SAM 2가 문양 하나하나를 "
            "픽셀 단위로 분할한 결과가 아닙니다.\n"
            "- **형태 결과**는 단일 사진에서 보이는 외형 기준 추정이며 뒷면·내부는 판단하지 않습니다.\n"
            "- **표면 광택 수준**은 사진 속 반사 특성만 추정하며, 실제 유약 유무를 확정하지 않습니다.\n"
            "- **시대 후보**는 로컬 CNN(멀티태스크 모델의 era 헤드) 결과이며 참고용입니다. "
            "모델 점수는 실제 정답 확률이 아니라 분류기의 softmax 출력값이며, 모델의 "
            "교차검증 정확도는 88%입니다.\n"
            "- **문양 위치 합의**와 **문양 이름 합의**는 서로 다른 지표입니다.\n"
            "- `mask_status=approximate`는 정밀 마스크가 아니라 VLM bbox/위치의 근사 표시입니다.\n"
            "- 이미지 우하단 **범례**는 배지 약어와 실제 문양명을 연결해 보여줍니다. "
            "`[목록 외 명칭]`이 붙은 항목은 사전에 정의한 참고 문양 목록에 없는 이름으로, "
            "여러 호출이 같은 답을 냈다는 것이지 정확하다는 보장은 아니므로 우선적으로 검토하세요.\n"
            "- 문양 합의는 재현성(같은 답이 반복됐는지) 지표이며 정확성(실제로 맞는지)을 "
            "보장하지 않습니다. 문양 위치·명칭은 별도 정량 평가 전이므로 조사자 검토가 필요합니다.\n"
            "- **decision(확정/추정/판정보류)**은 문양별 판별 기준(필수 특징 목록)을 VLM이 "
            "얼마나 직접 확인했는지를 나타내는 별도 지표입니다. 기본 화면에는 판정보류 "
            "후보는 표시하지 않고 상세 JSON에만 남깁니다.\n"
            "- **기본 조사** 모드는 위치 마커(점+배지)만 표시합니다. bbox는 실제 탐지 경계가 "
            "아니라 근사 위치이므로, 상세 검토 모드에서만 점선으로 함께 표시합니다.\n"
            "- **클로즈업 육안상태조사**는 확정된 문양마다 원본에서 넉넉하게 자른 확대 이미지를 "
            "VLM에게 다시 보여줘 마모·박락·변색·균열·오염 '의심' 여부를 평가한 결과입니다. "
            "사진 한 장만으로는 실제 손상과 촬영 화질 저하(블러/압축/조명 등)를 확실히 구분할 "
            "수 없어, 상태는 항상 \"의심/후보\" 수준으로만 표기하며 \"양호/훼손 확정\"처럼 "
            "단정하지 않습니다. 각 이상 후보에는 손상이 아닐 수도 있는 대안 설명을 함께 "
            "제시하며, 앙상블(반복 호출) 없이 문양당 1회만 평가하므로 이 결과는 AI의 최종 "
            "판정이 아니라 사람이 재확인해야 할 후보 목록으로 활용하세요."
        )

    return demo


if __name__ == "__main__":
    build_demo().launch()