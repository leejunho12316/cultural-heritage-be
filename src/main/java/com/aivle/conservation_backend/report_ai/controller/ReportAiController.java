package com.aivle.conservation_backend.report_ai.controller;

import com.aivle.conservation_backend.report_ai.client.ReportAiClient;
import com.aivle.conservation_backend.report_ai.dto.DocxRequestDto;
import com.aivle.conservation_backend.report_ai.dto.GenerateReportRequestDto;
import com.aivle.conservation_backend.report_ai.service.PotterySourceAdapter;
import com.aivle.conservation_backend.report_ai.service.XraySourceAdapter;

import lombok.RequiredArgsConstructor;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * 보고서 자동생성 연동 확인용 컨트롤러.
 *
 * X-ray/육안조사 컨트롤러와 마찬가지로 아직 결과를 DB(ASSESSMENT_REPORT)에
 * 저장하지 않고 그대로 반환한다 - 저장 로직은 보존가이드/X-ray/육안조사 각
 * 파트의 실제 DB 연동이 끝난 뒤, 그 결과를 모아 넘기는 형태로 붙일 예정이다.
 */
@RequiredArgsConstructor
@RestController
@RequestMapping("/api/reports")
public class ReportAiController {

    private static final MediaType DOCX_MEDIA_TYPE = MediaType.parseMediaType(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    );

    private final ReportAiClient reportAiClient;
    private final XraySourceAdapter xraySourceAdapter;
    private final PotterySourceAdapter potterySourceAdapter;

    /**
     * X-ray가 RDS에 저장해둔 결과(XrayJob/XrayDefect)를 report-ai 입력
     * 형태(xray_report_text/xray_regions)로 변환해서 보여준다.
     *
     * generate()의 동작을 바꾸지 않는 별도 조회용 엔드포인트다 - 아직
     * 육안조사/보존가이드는 DB 연동이 없어서 자동으로 전부 합쳐주는
     * 조율 엔드포인트는 만들 수 없고, X-ray 몫만 우선 보여준다. 이
     * 응답을 그대로 GenerateReportRequestDto.xrayReportText /
     * xrayRegions에 넣으면 된다.
     */
    @GetMapping("/{artifactId}/xray-source")
    public ResponseEntity<Map<String, Object>> xraySource(@PathVariable String artifactId) {
        return xraySourceAdapter.resolve(artifactId)
                .map(source -> ResponseEntity.ok(Map.<String, Object>of(
                        "xray_report_text", source.reportText() == null ? "" : source.reportText(),
                        "xray_regions", source.regions()
                )))
                .orElseGet(() -> ResponseEntity.ok(Map.of(
                        "xray_report_text", "",
                        "xray_regions", List.of()
                )));
    }

    /**
     * 육안조사가 RDS에 저장해둔 결과(AssessmentRun -&gt; InspectionResultPottery)를
     * report-ai 입력 형태(pottery_inspection)로 변환해서 보여준다.
     *
     * xraySource()와 동일하게 generate()의 동작은 바꾸지 않는 별도
     * 조회용 엔드포인트다. 이 응답을 그대로
     * GenerateReportRequestDto.potteryInspection에 넣으면 된다.
     */
    @GetMapping("/{artifactId}/pottery-source")
    public ResponseEntity<Map<String, Object>> potterySource(@PathVariable String artifactId) {
        return potterySourceAdapter.resolve(artifactId)
                .map(source -> ResponseEntity.ok(source.asMap()))
                .orElseGet(() -> ResponseEntity.ok(Map.of(
                        "inspection_text", "",
                        "human_review_recommended", false,
                        "detail", Map.of()
                )));
    }

    /** report_json만 생성한다. */
    @PostMapping("/generate")
    public ResponseEntity<Map<String, Object>> generate(@RequestBody GenerateReportRequestDto request) {
        return ResponseEntity.ok(reportAiClient.generateReport(request));
    }

    /** report_json 생성 + .docx 변환을 한 번에 한다 (데모/직접 테스트용). */
    @PostMapping("/generate/docx")
    public ResponseEntity<ByteArrayResource> generateDocx(@RequestBody GenerateReportRequestDto request) {
        byte[] docx = reportAiClient.generateReportDocx(request);
        return docxResponse(request.artifactId(), docx);
    }

    /** 이미 생성/저장된 report_json을 .docx로 변환만 한다 (LLM 재호출 없음). */
    @PostMapping("/docx")
    public ResponseEntity<ByteArrayResource> toDocx(@RequestBody DocxRequestDto request) {
        byte[] docx = reportAiClient.reportToDocx(request);
        return docxResponse(request.artifactId(), docx);
    }

    private ResponseEntity<ByteArrayResource> docxResponse(String artifactId, byte[] docx) {
        String filename = "report_" + artifactId + ".docx";

        return ResponseEntity.ok()
                .contentType(DOCX_MEDIA_TYPE)
                .header(
                        HttpHeaders.CONTENT_DISPOSITION,
                        ContentDisposition.attachment().filename(filename).build().toString()
                )
                .body(new ByteArrayResource(docx));
    }
}
