package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.vca.dto.ReportResponse;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDPage;
import org.apache.pdfbox.pdmodel.PDPageContentStream;
import org.apache.pdfbox.pdmodel.common.PDRectangle;
import org.apache.pdfbox.pdmodel.font.PDFont;
import org.apache.pdfbox.pdmodel.font.PDType0Font;
import org.apache.pdfbox.pdmodel.graphics.image.PDImageXObject;

import java.awt.Color;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.text.Normalizer;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

// VCA 조사 결과를 PDF로 렌더링한다(VcaService.createPdfJob에서 호출). 1페이지는
// 유물 정보+대표 사진, 2페이지(부터)는 사진별 특이점 전체 오버레이 + 통계/요약,
// 그 다음부터는 특이점 하나당 한 페이지씩이다. 한글 렌더링을 위해 Noto Sans KR을
// (OFL 라이선스, resources/fonts에 동봉) 매 문서마다 새로 embed한다 - PDFBox의
// 기본 14개 폰트는 한글 글리프가 없다. 의존성이 없는 순수 렌더러라 Spring 빈으로
// 등록하지 않고 VcaService가 필요할 때 직접 생성한다.
class VcaReportPdfRenderer {

    private static final PDRectangle PAGE_SIZE = PDRectangle.A4;
    private static final float MARGIN = 50f;
    private static final float PAGE_WIDTH = PAGE_SIZE.getWidth() - 2 * MARGIN;
    private static final float TITLE_SIZE = 18f;
    private static final float HEADING_SIZE = 13f;
    private static final float BODY_SIZE = 10.5f;
    private static final float SMALL_SIZE = 9f;
    private static final float LINE_GAP = 4f;
    private static final Color BRONZE = new Color(0x8B, 0x5E, 0x34);
    private static final Color INK = new Color(0x22, 0x22, 0x22);
    private static final Color MUTED = new Color(0x77, 0x77, 0x77);
    private static final Color[] MARKER_COLORS = {
            new Color(0xE0, 0x57, 0x57), new Color(0x3B, 0x82, 0xC4), new Color(0x4C, 0xAF, 0x50),
            new Color(0xE0, 0x9F, 0x3E), new Color(0x9C, 0x27, 0xB0), new Color(0x00, 0x96, 0x88),
    };
    private static final DateTimeFormatter DATE_FORMAT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm").withZone(ZoneId.of("Asia/Seoul"));

    private static final Map<String, String> SEVERITY_LABELS = Map.of(
            "INFO", "정보", "LOW", "낮음", "MEDIUM", "보통", "HIGH", "높음", "CRITICAL", "심각"
    );
    private static final List<String> SEVERITY_ORDER = List.of("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO");
    private static final Map<String, String> CONCEPT_FAMILY_LABELS = Map.ofEntries(
            Map.entry("crack", "균열"), Map.entry("deposit", "침전물"), Map.entry("corrosion", "부식"),
            Map.entry("biological_growth", "생물학적 오염"), Map.entry("surface_loss", "결손"),
            Map.entry("flaking", "박리"), Map.entry("stain_discoloration", "변색"),
            Map.entry("hole_pit", "구멍·천공"), Map.entry("deformation", "변형"),
            Map.entry("adhesive_residue", "접착제 잔류물"), Map.entry("unknown_visual_anomaly", "시각적 이상"),
            Map.entry("unknown", "미분류")
    );

    record PdfImage(String imageId, String fileName, byte[] bytes) {
    }

    record PdfRenderInput(
            String artifactTitle,
            String artifactCode,
            String material,
            int imageCount,
            Instant completedAt,
            List<PdfImage> images,
            ReportResponse.Summary summary,
            List<ReportResponse.Finding> findings,
            ReportResponse.PotteryInspection potteryInspection
    ) {
    }

    byte[] render(PdfRenderInput input) {
        try (PDDocument document = new PDDocument()) {
            PDType0Font regular = loadFont(document, "/fonts/NotoSansKR-Regular.ttf");
            PDType0Font bold = loadFont(document, "/fonts/NotoSansKR-Bold.ttf");
            Map<String, PDImageXObject> embeddedImages = embedImages(document, input.images());
            Map<String, Integer> findingNumbers = findingNumbers(input.findings());

            renderCoverPage(document, regular, bold, input, embeddedImages);
            renderOverviewPages(document, regular, bold, input, embeddedImages, findingNumbers);
            if (input.potteryInspection() != null) {
                renderPotteryInspectionPage(document, regular, bold, input.potteryInspection());
            }
            for (ReportResponse.Finding finding : input.findings()) {
                renderFindingPage(document, regular, bold, input, finding, embeddedImages, findingNumbers);
            }

            ByteArrayOutputStream output = new ByteArrayOutputStream();
            document.save(output);
            return output.toByteArray();
        } catch (IOException exception) {
            throw new UncheckedIOException("Failed to render VCA report PDF.", exception);
        }
    }

    private PDType0Font loadFont(PDDocument document, String classpathResource) throws IOException {
        try (InputStream stream = getClass().getResourceAsStream(classpathResource)) {
            if (stream == null) {
                throw new IOException("Missing bundled font resource: " + classpathResource);
            }
            return PDType0Font.load(document, stream);
        }
    }

    private Map<String, PDImageXObject> embedImages(PDDocument document, List<PdfImage> images) throws IOException {
        Map<String, PDImageXObject> embedded = new LinkedHashMap<>();
        for (PdfImage image : images) {
            embedded.put(image.imageId(), PDImageXObject.createFromByteArray(document, image.bytes(), image.fileName()));
        }
        return embedded;
    }

    // findings 배열 순서(index+1)로 번호를 매긴다 - 웹 리포트(VisualCandidateOverlay,
    // ReportFindingsBrief였던 것, VisualFindingDetailPage)와 정확히 같은 규칙이라야
    // PDF와 웹 화면의 번호가 서로 어긋나지 않는다.
    private Map<String, Integer> findingNumbers(List<ReportResponse.Finding> findings) {
        Map<String, Integer> numbers = new LinkedHashMap<>();
        for (int index = 0; index < findings.size(); index++) {
            String findingId = findings.get(index).findingId();
            if (findingId != null) {
                numbers.put(findingId, index + 1);
            }
        }
        return numbers;
    }

    // 1페이지: 유물 정보 표 + 대표 사진(첫 업로드 이미지).
    private void renderCoverPage(
            PDDocument document, PDFont regular, PDFont bold,
            PdfRenderInput input, Map<String, PDImageXObject> embeddedImages
    ) throws IOException {
        PDPage page = new PDPage(PAGE_SIZE);
        document.addPage(page);
        try (PDPageContentStream stream = new PDPageContentStream(document, page)) {
            TextCursor cursor = new TextCursor(PAGE_SIZE.getHeight() - MARGIN);
            cursor.y -= drawText(stream, bold, TITLE_SIZE, INK, MARGIN, cursor.y, "VCA 육안 조사 보고서") + 20;

            List<String[]> rows = new ArrayList<>();
            rows.add(new String[]{"유물명", nullToDash(input.artifactTitle())});
            rows.add(new String[]{"관리번호", nullToDash(input.artifactCode())});
            rows.add(new String[]{"재질", nullToDash(input.material())});
            rows.add(new String[]{"조사 이미지 수", input.imageCount() + "장"});
            rows.add(new String[]{"조사 완료일", input.completedAt() != null ? DATE_FORMAT.format(input.completedAt()) : "-"});
            cursor.y -= drawInfoTable(stream, regular, bold, MARGIN, cursor.y, rows) + 24;

            if (!input.images().isEmpty()) {
                PdfImage representative = input.images().get(0);
                PDImageXObject embedded = embeddedImages.get(representative.imageId());
                cursor.y -= drawText(stream, bold, HEADING_SIZE, BRONZE, MARGIN, cursor.y, "대표 사진") + 10;
                if (embedded != null) {
                    float maxHeight = cursor.y - MARGIN;
                    drawScaledImage(stream, embedded, MARGIN, cursor.y, PAGE_WIDTH, maxHeight, true);
                }
            }
        }
    }

    // 2페이지(부터): 사진마다 그 사진에 속한 모든 특이점을 번호 배지와 함께
    // 오버레이해서 한 장씩 보여주고, 마지막에 통계 표 + 요약 문장을 담은 페이지를
    // 붙인다 - 웹 리포트의 "특이점 위치" + "육안 조사 결과" 상단부와 같은 내용이다.
    private void renderOverviewPages(
            PDDocument document, PDFont regular, PDFont bold,
            PdfRenderInput input, Map<String, PDImageXObject> embeddedImages, Map<String, Integer> findingNumbers
    ) throws IOException {
        for (PdfImage image : input.images()) {
            List<ReportResponse.Finding> imageFindings = input.findings().stream()
                    .filter(finding -> image.imageId().equals(finding.imageId()) && finding.bbox() != null)
                    .toList();
            if (imageFindings.isEmpty()) {
                continue;
            }
            PDPage page = new PDPage(PAGE_SIZE);
            document.addPage(page);
            try (PDPageContentStream stream = new PDPageContentStream(document, page)) {
                TextCursor cursor = new TextCursor(PAGE_SIZE.getHeight() - MARGIN);
                cursor.y -= drawText(stream, bold, HEADING_SIZE, INK, MARGIN, cursor.y,
                        "특이점 위치 · " + nullToDash(image.fileName())) + 14;
                PDImageXObject embedded = embeddedImages.get(image.imageId());
                if (embedded != null) {
                    ImagePlacement placement = drawScaledImage(
                            stream, embedded, MARGIN, cursor.y, PAGE_WIDTH, cursor.y - MARGIN, true
                    );
                    for (ReportResponse.Finding finding : imageFindings) {
                        int number = findingNumbers.getOrDefault(finding.findingId(), 0);
                        drawOverlayMarker(stream, bold, placement, finding, number, true);
                    }
                }
            }
        }
        renderStatsPage(document, regular, bold, input);
    }

    // 통계 표(심각도/유형별 건수) + 규칙 기반 요약 문장. VisualReport.jsx의
    // FindingsStatsTable/summarizeFindings와 같은 집계 로직을 그대로 옮겼다.
    private void renderStatsPage(PDDocument document, PDFont regular, PDFont bold, PdfRenderInput input) throws IOException {
        PDPage page = new PDPage(PAGE_SIZE);
        document.addPage(page);
        try (PDPageContentStream stream = new PDPageContentStream(document, page)) {
            TextCursor cursor = new TextCursor(PAGE_SIZE.getHeight() - MARGIN);
            cursor.y -= drawText(stream, bold, HEADING_SIZE, INK, MARGIN, cursor.y, "육안 조사 결과 요약") + 14;

            List<ReportResponse.Finding> findings = input.findings();
            List<String[]> statsColumns = statsColumns(findings);
            if (!statsColumns.isEmpty()) {
                cursor.y -= drawStatsTable(stream, regular, bold, MARGIN, cursor.y, statsColumns) + 18;
            }

            String description = input.summary() != null && input.summary().description() != null
                    ? input.summary().description() : "설명 정보가 없습니다.";
            cursor.y -= drawWrappedText(stream, regular, BODY_SIZE, INK, MARGIN, cursor.y, PAGE_WIDTH, description) + 10;

            String narrative = findings.isEmpty() ? "등록된 특이점이 없습니다." : summarizeFindings(findings);
            drawWrappedText(stream, bold, BODY_SIZE, INK, MARGIN, cursor.y, PAGE_WIDTH, narrative);
        }
    }

    // 도자기 재질 유물에 한해 붙는 보조 검사 결과 페이지. 웹 리포트의
    // ReportPotteryInspection(VisualReport.jsx)과 같은 내용(모듈 버전/전문가
    // 검토 권장/요약/검사 기록/세부 정보)을 담는다. potteryInspection이
    // null이면(비도자기 재질이거나 검사 미실행) 호출 자체가 안 된다.
    private void renderPotteryInspectionPage(
            PDDocument document, PDFont regular, PDFont bold, ReportResponse.PotteryInspection potteryInspection
    ) throws IOException {
        PDPage page = new PDPage(PAGE_SIZE);
        document.addPage(page);
        try (PDPageContentStream stream = new PDPageContentStream(document, page)) {
            TextCursor cursor = new TextCursor(PAGE_SIZE.getHeight() - MARGIN);
            cursor.y -= drawText(stream, bold, HEADING_SIZE, INK, MARGIN, cursor.y, "도자기 검사") + 14;

            List<String[]> infoRows = new ArrayList<>();
            infoRows.add(new String[]{"모듈 버전", formatPotteryValue(potteryInspection.moduleVersion())});
            infoRows.add(new String[]{"전문가 검토 권장", formatPotteryValue(potteryInspection.humanReviewRecommended())});
            cursor.y -= drawInfoTable(stream, regular, bold, MARGIN, cursor.y, infoRows) + 16;

            cursor.y -= drawText(stream, bold, BODY_SIZE, BRONZE, MARGIN, cursor.y, "요약") + 6;
            cursor.y -= drawWrappedText(
                    stream, regular, BODY_SIZE, INK, MARGIN, cursor.y, PAGE_WIDTH,
                    formatPotteryValue(potteryInspection.summary())
            ) + 12;

            cursor.y -= drawText(stream, bold, BODY_SIZE, BRONZE, MARGIN, cursor.y, "검사 기록") + 6;
            cursor.y -= drawWrappedText(
                    stream, regular, BODY_SIZE, INK, MARGIN, cursor.y, PAGE_WIDTH,
                    formatPotteryValue(potteryInspection.inspectionText())
            ) + 12;

            Map<String, Object> detail = potteryInspection.detail();
            if (detail != null && !detail.isEmpty()) {
                cursor.y -= drawText(stream, bold, BODY_SIZE, BRONZE, MARGIN, cursor.y, "대상 세부 정보") + 6;
                for (Map.Entry<String, Object> entry : detail.entrySet()) {
                    String line = formatPotteryLabel(entry.getKey()) + ": " + formatPotteryValue(entry.getValue());
                    cursor.y -= drawWrappedText(stream, regular, SMALL_SIZE, MUTED, MARGIN, cursor.y, PAGE_WIDTH, line) + 4;
                }
            }
        }
    }

    // camelCase/snake_case 키를 "camel Case"/"snake case"처럼 사람이 읽기 쉬운
    // 형태로 바꾼다. FE formatPotteryInspectionLabel과 동일 규칙.
    private String formatPotteryLabel(String label) {
        return label.replaceAll("([a-z])([A-Z])", "$1 $2").replaceAll("[_-]", " ");
    }

    // 불리언/배열/맵/원시값 중 무엇이 와도 표시 가능한 문자열로 재귀 변환한다.
    // FE formatPotteryInspectionValue와 동일 규칙.
    @SuppressWarnings("unchecked")
    private String formatPotteryValue(Object value) {
        if (value == null) {
            return "정보 없음";
        }
        if (value instanceof Boolean bool) {
            return bool ? "예" : "아니오";
        }
        if (value instanceof List<?> list) {
            return list.isEmpty() ? "정보 없음"
                    : list.stream().map(this::formatPotteryValue).collect(java.util.stream.Collectors.joining(", "));
        }
        if (value instanceof Map<?, ?> map) {
            return map.isEmpty() ? "정보 없음"
                    : ((Map<String, Object>) map).entrySet().stream()
                            .map(entry -> formatPotteryLabel(entry.getKey()) + ": " + formatPotteryValue(entry.getValue()))
                            .collect(java.util.stream.Collectors.joining(" · "));
        }
        return String.valueOf(value);
    }

    // 특이점 하나의 상세 페이지: 소속 사진 위에 이 특이점만 강조해서 표시하고,
    // 아래에 심각도/유형/설명/근거 문헌을 적는다. 웹의 VisualFindingDetailPage와
    // 같은 정보를 담는다.
    private void renderFindingPage(
            PDDocument document, PDFont regular, PDFont bold, PdfRenderInput input,
            ReportResponse.Finding finding, Map<String, PDImageXObject> embeddedImages,
            Map<String, Integer> findingNumbers
    ) throws IOException {
        PDPage page = new PDPage(PAGE_SIZE);
        document.addPage(page);
        try (PDPageContentStream stream = new PDPageContentStream(document, page)) {
            TextCursor cursor = new TextCursor(PAGE_SIZE.getHeight() - MARGIN);
            int number = findingNumbers.getOrDefault(finding.findingId(), 0);
            String heading = (number > 0 ? number + ". " : "") + conceptFamilyLabel(finding.conceptFamily());
            cursor.y -= drawText(stream, bold, HEADING_SIZE, INK, MARGIN, cursor.y, heading) + 12;

            PDImageXObject embedded = finding.imageId() != null ? embeddedImages.get(finding.imageId()) : null;
            float imageAreaHeight = PAGE_SIZE.getHeight() * 0.5f;
            if (embedded != null && finding.bbox() != null) {
                ImagePlacement placement = drawScaledImage(stream, embedded, MARGIN, cursor.y, PAGE_WIDTH, imageAreaHeight, true);
                drawOverlayMarker(stream, bold, placement, finding, number, false);
                cursor.y -= placement.drawHeight + 16;
            }

            List<String[]> rows = new ArrayList<>();
            rows.add(new String[]{"심각도", severityLabel(finding.severity())});
            rows.add(new String[]{"관찰 유형", conceptFamilyLabel(finding.conceptFamily())});
            cursor.y -= drawInfoTable(stream, regular, bold, MARGIN, cursor.y, rows) + 14;

            List<ReportResponse.Citation> citations = finding.citations() != null ? finding.citations() : List.of();
            String description = findingDescription(finding, citations);
            cursor.y -= drawWrappedText(stream, regular, BODY_SIZE, INK, MARGIN, cursor.y, PAGE_WIDTH, description) + 14;

            if (!citations.isEmpty()) {
                cursor.y -= drawText(stream, bold, BODY_SIZE, BRONZE, MARGIN, cursor.y, "근거 문헌") + 8;
                for (ReportResponse.Citation citation : citations) {
                    String line = "- " + nullToDash(citation.sourceCitation())
                            + (citation.pageNumber() != null ? " (p." + citation.pageNumber() + ")" : "");
                    cursor.y -= drawWrappedText(stream, regular, SMALL_SIZE, MUTED, MARGIN, cursor.y, PAGE_WIDTH, line) + 4;
                }
            }
        }
    }

    // 심각도별 -> 유형별 순서로 이어붙인 (라벨, 건수) 컬럼 목록.
    // VisualReport.jsx의 severityCounts/conceptFamilyCounts와 같은 순서.
    private List<String[]> statsColumns(List<ReportResponse.Finding> findings) {
        Map<String, Integer> severityCounts = new LinkedHashMap<>();
        for (String severity : SEVERITY_ORDER) {
            long count = findings.stream().filter(f -> severity.equals(f.severity())).count();
            if (count > 0) {
                severityCounts.put(severity, (int) count);
            }
        }
        Map<String, Integer> familyCounts = new LinkedHashMap<>();
        for (ReportResponse.Finding finding : findings) {
            String key = finding.conceptFamily() != null ? finding.conceptFamily() : "unknown";
            familyCounts.merge(key, 1, Integer::sum);
        }
        List<String[]> columns = new ArrayList<>();
        severityCounts.forEach((severity, count) -> columns.add(new String[]{severityLabel(severity), String.valueOf(count)}));
        familyCounts.forEach((family, count) -> columns.add(new String[]{conceptFamilyLabel(family), String.valueOf(count)}));
        return columns;
    }

    // VisualReport.jsx의 summarizeFindings를 그대로 옮긴 규칙 기반 요약 문장 생성.
    private String summarizeFindings(List<ReportResponse.Finding> findings) {
        Map<String, Integer> counts = new LinkedHashMap<>();
        Map<String, Integer> highSeverityCounts = new LinkedHashMap<>();
        for (ReportResponse.Finding finding : findings) {
            String key = finding.conceptFamily() != null ? finding.conceptFamily() : "unknown";
            counts.merge(key, 1, Integer::sum);
            if ("HIGH".equals(finding.severity()) || "CRITICAL".equals(finding.severity())) {
                highSeverityCounts.merge(key, 1, Integer::sum);
            }
        }
        List<Map.Entry<String, Integer>> sorted = new ArrayList<>(counts.entrySet());
        sorted.sort((a, b) -> b.getValue() - a.getValue());
        List<String> parts = new ArrayList<>();
        for (Map.Entry<String, Integer> entry : sorted) {
            parts.add(conceptFamilyLabel(entry.getKey()) + " " + entry.getValue() + "건");
        }
        int highSeverityTotal = highSeverityCounts.values().stream().mapToInt(Integer::intValue).sum();
        String severityNote = highSeverityTotal > 0
                ? " 이 중 " + highSeverityTotal + "건은 심각도가 '높음' 이상으로 평가되어 주의가 필요합니다."
                : "";
        return "총 " + findings.size() + "건의 특이점이 확인되었으며, " + String.join(", ", parts) + "입니다." + severityNote;
    }

    // 웹 리포트(visualVcaLabels.js의 findingDescription)와 동일한 폴백: 개념
    // 분류가 안 됐고 인용 근거도 없는 후보는 원문 description에 "unknown"
    // 같은 내부 값이 그대로 담겨 있을 수 있어, 사용자에게는 원문 대신 안내
    // 문구를 보여준다.
    private String findingDescription(ReportResponse.Finding finding, List<ReportResponse.Citation> citations) {
        if ("unknown".equals(finding.conceptFamily()) && citations.isEmpty()) {
            return "AI가 이상 부위로 탐지했지만, 참고 문헌에서 일치하는 근거를 찾지 못했습니다.";
        }
        return finding.description() != null && !finding.description().isBlank()
                ? finding.description() : "세부 설명이 없습니다.";
    }

    private String severityLabel(String severity) {
        return SEVERITY_LABELS.getOrDefault(severity, nullToDash(severity));
    }

    private String conceptFamilyLabel(String conceptFamily) {
        return CONCEPT_FAMILY_LABELS.getOrDefault(conceptFamily, nullToDash(conceptFamily));
    }

    private String nullToDash(String value) {
        return value == null || value.isBlank() ? "-" : value;
    }

    // maxWidth/maxHeight 안에 원본 비율을 유지하며 맞추고, 요청하면 가로 중앙 정렬한다.
    // 이후 오버레이 좌표 변환(원본 픽셀 -> PDF 좌표)에 필요한 배치 정보를 돌려준다.
    private ImagePlacement drawScaledImage(
            PDPageContentStream stream, PDImageXObject image,
            float x, float topY, float maxWidth, float maxHeight, boolean center
    ) throws IOException {
        float nativeWidth = image.getWidth();
        float nativeHeight = image.getHeight();
        float scale = Math.min(maxWidth / nativeWidth, maxHeight / nativeHeight);
        float drawWidth = nativeWidth * scale;
        float drawHeight = nativeHeight * scale;
        float drawX = center ? x + (maxWidth - drawWidth) / 2 : x;
        float drawY = topY - drawHeight;
        stream.drawImage(image, drawX, drawY, drawWidth, drawHeight);
        return new ImagePlacement(drawX, drawY, drawWidth, drawHeight, nativeWidth, nativeHeight);
    }

    // bbox(사각형)와 폴리곤(있으면) 윤곽선 + 번호 배지를 이미지 배치 위에 그린다.
    // 원본 이미지 픽셀 좌표(top-left 원점, y 아래로 증가)를 PDF 좌표(bottom-left
    // 원점, y 위로 증가)로 변환해야 한다.
    private void drawOverlayMarker(
            PDPageContentStream stream, PDFont bold, ImagePlacement placement,
            ReportResponse.Finding finding, int number, boolean bboxOnly
    ) throws IOException {
        Color color = MARKER_COLORS[Math.max(0, number - 1) % MARKER_COLORS.length];
        stream.setStrokingColor(color);
        stream.setLineWidth(2f);

        // 흩어진 손상(예: 점무늬 부식)은 폴리곤 조각이 수백~수천 개까지 나올 수
        // 있다 - 여러 특이점을 한 장에 겹쳐 그리는 개요 페이지에서 전부 그리면
        // 알아볼 수 없는 얼룩이 되므로, 개요 페이지는 bbox만, 특이점 개별
        // 페이지는 전체 폴리곤을 그린다.
        List<List<ReportResponse.Point>> polygons = bboxOnly || finding.polygons() == null
                ? List.of() : finding.polygons();
        List<List<ReportResponse.Point>> validPolygons = polygons.stream().filter(p -> p.size() >= 3).toList();
        if (!validPolygons.isEmpty()) {
            for (List<ReportResponse.Point> polygon : validPolygons) {
                float[] first = toPdfPoint(placement, polygon.get(0));
                stream.moveTo(first[0], first[1]);
                for (int index = 1; index < polygon.size(); index++) {
                    float[] point = toPdfPoint(placement, polygon.get(index));
                    stream.lineTo(point[0], point[1]);
                }
                stream.closePath();
                stream.stroke();
            }
        } else if (finding.bbox() != null) {
            ReportResponse.Bbox bbox = finding.bbox();
            float[] topLeft = toPdfPoint(placement, bbox.xMin(), bbox.yMin());
            float[] bottomRight = toPdfPoint(placement, bbox.xMax(), bbox.yMax());
            stream.addRect(topLeft[0], bottomRight[1], bottomRight[0] - topLeft[0], topLeft[1] - bottomRight[1]);
            stream.stroke();
        }

        if (finding.bbox() != null && number > 0) {
            float[] badge = toPdfPoint(placement, finding.bbox().xMin(), finding.bbox().yMin());
            float radius = 9f;
            stream.setNonStrokingColor(color);
            drawFilledCircle(stream, badge[0], badge[1], radius);
            String label = String.valueOf(number);
            float textWidth = bold.getStringWidth(label) / 1000f * SMALL_SIZE;
            stream.beginText();
            stream.setFont(bold, SMALL_SIZE);
            stream.setNonStrokingColor(Color.WHITE);
            stream.newLineAtOffset(badge[0] - textWidth / 2, badge[1] - SMALL_SIZE / 3);
            stream.showText(label);
            stream.endText();
        }
    }

    private void drawFilledCircle(PDPageContentStream stream, float centerX, float centerY, float radius) throws IOException {
        float k = radius * 0.5523f;
        stream.moveTo(centerX - radius, centerY);
        stream.curveTo(centerX - radius, centerY + k, centerX - k, centerY + radius, centerX, centerY + radius);
        stream.curveTo(centerX + k, centerY + radius, centerX + radius, centerY + k, centerX + radius, centerY);
        stream.curveTo(centerX + radius, centerY - k, centerX + k, centerY - radius, centerX, centerY - radius);
        stream.curveTo(centerX - k, centerY - radius, centerX - radius, centerY - k, centerX - radius, centerY);
        stream.closePath();
        stream.fill();
    }

    private float[] toPdfPoint(ImagePlacement placement, ReportResponse.Point point) {
        return toPdfPoint(placement, point.x(), point.y());
    }

    private float[] toPdfPoint(ImagePlacement placement, double pixelX, double pixelY) {
        float x = placement.drawX + (float) (pixelX / placement.nativeWidth) * placement.drawWidth;
        float y = placement.drawY + placement.drawHeight - (float) (pixelY / placement.nativeHeight) * placement.drawHeight;
        return new float[]{x, y};
    }

    private float drawText(PDPageContentStream stream, PDFont font, float size, Color color, float x, float y, String text) throws IOException {
        stream.beginText();
        stream.setFont(font, size);
        stream.setNonStrokingColor(color);
        stream.newLineAtOffset(x, y - size);
        stream.showText(normalize(text));
        stream.endText();
        return size + LINE_GAP;
    }

    // macOS 파일시스템은 한글 파일명을 NFD(자모 분해)로 돌려주는 경우가 흔하다
    // (예: 업로드 원본 파일명). PDFBox의 showText()는 문자 shaping을 하지 않고
    // 코드포인트 하나당 글리프 하나를 그대로 그리므로, NFD 문자열을 그대로 그리면
    // 자모가 낱개로 흩어져 보인다 - NFC(완성형)로 정규화해 실제 음절 글리프로
    // 그려지게 한다.
    private String normalize(String text) {
        return text == null ? "" : Normalizer.normalize(text, Normalizer.Form.NFC);
    }

    // 공백 기준 단순 줄바꿈(한국어 문장도 어절 사이 공백은 있으므로 충분하다).
    // 그려진 총 높이를 돌려준다.
    private float drawWrappedText(
            PDPageContentStream stream, PDFont font, float size, Color color,
            float x, float y, float maxWidth, String text
    ) throws IOException {
        List<String> lines = wrapText(text, font, size, maxWidth);
        float cursorY = y;
        for (String line : lines) {
            cursorY -= drawText(stream, font, size, color, x, cursorY, line);
        }
        return y - cursorY;
    }

    private List<String> wrapText(String text, PDFont font, float size, float maxWidth) throws IOException {
        List<String> lines = new ArrayList<>();
        for (String paragraph : text.split("\n", -1)) {
            StringBuilder current = new StringBuilder();
            for (String word : paragraph.split(" ")) {
                String candidate = current.isEmpty() ? word : current + " " + word;
                if (font.getStringWidth(candidate) / 1000f * size > maxWidth && !current.isEmpty()) {
                    lines.add(current.toString());
                    current = new StringBuilder(word);
                } else {
                    current = new StringBuilder(candidate);
                }
            }
            lines.add(current.toString());
        }
        return lines;
    }

    // 라벨(bold) | 값(regular) 두 칸짜리 표. 유물 정보/특이점 요약에 쓴다.
    private float drawInfoTable(
            PDPageContentStream stream, PDFont regular, PDFont bold,
            float x, float topY, List<String[]> rows
    ) throws IOException {
        float labelWidth = 110f;
        float rowHeight = BODY_SIZE + LINE_GAP + 6f;
        float totalHeight = rowHeight * rows.size();
        float cursorY = topY;
        for (String[] row : rows) {
            stream.setStrokingColor(new Color(0xE3, 0xDD, 0xD2));
            stream.setLineWidth(0.5f);
            stream.moveTo(x, cursorY - rowHeight);
            stream.lineTo(x + PAGE_WIDTH, cursorY - rowHeight);
            stream.stroke();
            drawText(stream, bold, BODY_SIZE, BRONZE, x, cursorY - 5, row[0]);
            drawText(stream, regular, BODY_SIZE, INK, x + labelWidth, cursorY - 5, row[1]);
            cursorY -= rowHeight;
        }
        return totalHeight;
    }

    // 가로형 통계 표: 첫 행은 라벨(굵게), 둘째 행은 건수. columns 개수가 많아 페이지
    // 폭을 넘으면 다음 줄로 넘긴다.
    private float drawStatsTable(
            PDPageContentStream stream, PDFont regular, PDFont bold,
            float x, float topY, List<String[]> columns
    ) throws IOException {
        float columnWidth = 90f;
        float rowHeight = 20f;
        int columnsPerRow = Math.max(1, (int) (PAGE_WIDTH / columnWidth));
        float cursorY = topY;
        for (int start = 0; start < columns.size(); start += columnsPerRow) {
            List<String[]> chunk = columns.subList(start, Math.min(columns.size(), start + columnsPerRow));
            float cellX = x;
            for (String[] column : chunk) {
                stream.setStrokingColor(new Color(0xE3, 0xDD, 0xD2));
                stream.setLineWidth(0.5f);
                stream.addRect(cellX, cursorY - rowHeight * 2, columnWidth - 6, rowHeight * 2);
                stream.stroke();
                drawText(stream, bold, SMALL_SIZE, INK, cellX + 6, cursorY - 4, column[0]);
                drawText(stream, regular, BODY_SIZE + 2, BRONZE, cellX + 6, cursorY - rowHeight - 4, column[1]);
                cellX += columnWidth;
            }
            cursorY -= rowHeight * 2 + 6;
        }
        return topY - cursorY;
    }

    private record ImagePlacement(float drawX, float drawY, float drawWidth, float drawHeight, float nativeWidth, float nativeHeight) {
    }

    private static final class TextCursor {
        private float y;

        private TextCursor(float y) {
            this.y = y;
        }
    }
}
