package com.aivle.conservation_backend.report_ai.controller;

import com.aivle.conservation_backend.report_ai.client.ReportAiClient;
import com.aivle.conservation_backend.report_ai.dto.DocxRequestDto;
import com.aivle.conservation_backend.report_ai.dto.GenerateReportRequestDto;

import lombok.RequiredArgsConstructor;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

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
