"""report_json(assemble_node 출력)을 편집 가능한 .docx 파일로 변환한다.

섹션은 두 가지 형태만 온다 (assemble.py 참고):
  - {"title", "fields": dict, "key"}  -> header 섹션, 표로 렌더링
  - {"title", "body": str, "key"}     -> LLM이 작성한 섹션, 문단으로 렌더링
    (body는 "\n\n"으로 문단이 구분된 정식 보고서체 텍스트)

사진 배치 방식은 실제 보존처리 보고서(예: 국립박물관 보존과학 논문,
문화재청 보존처리 보고서) 여러 건을 직접 확인해서 정했다 - 공통적으로
사진을 문서 끝에 모아 붙이지 않고, "그림 N. 캡션" 형태로 해당 단계
서술이 끝나는 지점에 바로 이어 붙인다 (예: "세척" 문단 뒤에 세척 전/후
사진, 그 다음에 "안정화처리" 문단). 그래서 photos는 섹션 title이 아니라
assemble.py가 심어둔 안정적인 "key"(header/pre_investigation/disassembly/
cleaning/reinforcement/bonding/restoration/conclusion)로 매칭해서, 각
섹션 본문 바로 뒤에 삽입한다.
"""

from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Any

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from PIL import Image

REPORT_TYPE_LABELS = {
    "ceramic_treatment_report": "도자기 처리보고서",
}

BASE_FONT = "맑은 고딕"


def _set_base_font(doc: Document) -> None:
    style = doc.styles["Normal"]
    style.font.name = BASE_FONT
    style.font.size = Pt(11)
    # 한글 폰트는 eastAsia 요소를 따로 지정해야 워드에서 실제로 적용된다.
    style.element.rPr.rFonts.set(qn("w:eastAsia"), BASE_FONT)


def _add_title_block(doc: Document, report_json: dict[str, Any]) -> None:
    report_type = REPORT_TYPE_LABELS.get(
        report_json.get("report_type"), report_json.get("report_type") or "보존처리 보고서"
    )
    title = doc.add_heading(report_type, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta_run = meta.add_run(
        f"유물 관리번호: {report_json.get('artifact_id') or '-'}    "
        f"작성일: {date.today().isoformat()}"
    )
    meta_run.font.size = Pt(10)


def _add_fields_table(doc: Document, fields: dict[str, Any]) -> None:
    table = doc.add_table(rows=0, cols=2)
    table.style = "Light Grid Accent 1"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for key, value in fields.items():
        row = table.add_row().cells
        row[0].text = str(key)
        row[0].paragraphs[0].runs[0].bold = True
        row[1].text = "" if value in (None, "") else str(value)


def _add_body_paragraphs(doc: Document, body: str) -> None:
    for para in (body or "").split("\n\n"):
        para = para.strip()
        if para:
            doc.add_paragraph(para)


def _normalize_image_bytes(raw: bytes) -> bytes:
    """python-docx의 이미지 헤더 파서는 CMYK JPEG(PDF에서 추출한 이미지에
    흔함) 등 일부 변형을 인식하지 못해 UnrecognizedImageError를 던진다.
    Pillow로 한 번 열어 RGB PNG로 다시 저장하면 항상 안전하게 삽입된다.
    """
    with Image.open(BytesIO(raw)) as img:
        rgb = img.convert("RGB")
        out = BytesIO()
        rgb.save(out, format="PNG")
        return out.getvalue()


def _add_photo_block(doc: Document, photos: list[dict[str, Any]]) -> None:
    """사진 N장을 "그림 N. 캡션" 형태로 나란히 삽입한다.

    참고 보고서들의 관행대로 한 줄에 최대 2장까지만 배치한다 - 3장 이상
    한 줄에 넣으면 사진이 작아져 세부(균열, 색 변화 등)가 잘 안 보인다.
    """
    if not photos:
        return

    cols = min(2, len(photos))
    rows = (len(photos) + cols - 1) // cols
    table = doc.add_table(rows=rows, cols=cols)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    for idx, photo in enumerate(photos):
        r, c = divmod(idx, cols)
        cell = table.cell(r, c)

        pic_paragraph = cell.paragraphs[0]
        pic_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        normalized = _normalize_image_bytes(photo["image"])
        pic_paragraph.add_run().add_picture(BytesIO(normalized), width=Inches(2.6))

        caption_paragraph = cell.add_paragraph(photo.get("caption", ""))
        caption_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if caption_paragraph.runs:
            caption_run = caption_paragraph.runs[0]
            caption_run.font.size = Pt(9)
            caption_run.italic = True

    # 사진 블록과 다음 섹션 제목 사이에 여백 한 줄.
    doc.add_paragraph()


def render_report_docx(
    report_json: dict[str, Any],
    photos: dict[str, list[dict[str, Any]]] | None = None,
) -> bytes:
    """report_json -> .docx 바이트. Spring/프론트가 그대로 파일로 내려주면 된다.

    photos: {section_key: [{"caption": str, "image": bytes}, ...]}.
    section_key는 assemble.py의 SECTION_ORDER 값(header/pre_investigation/
    disassembly/cleaning/reinforcement/bonding/restoration/conclusion)과
    맞춰서 넘긴다 - 해당 섹션의 본문 바로 뒤에 사진이 삽입된다. 안 넘기면
    (None/빈 dict) 기존과 동일하게 텍스트만 생성된다 - 하위 호환 유지.
    """
    photos = photos or {}

    doc = Document()
    _set_base_font(doc)
    _add_title_block(doc, report_json)

    for section in report_json.get("sections") or []:
        title = section.get("title") or ""
        doc.add_heading(title, level=1)

        if "fields" in section:
            _add_fields_table(doc, section["fields"] or {})
        else:
            _add_body_paragraphs(doc, section.get("body") or "")

        section_photos = photos.get(section.get("key"))
        _add_photo_block(doc, section_photos)

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
