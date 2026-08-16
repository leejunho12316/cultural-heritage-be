package com.aivle.conservation_backend.report_ai.controller;

import com.aivle.conservation_backend.artifact.service.ArtifactAccessService;
import com.aivle.conservation_backend.report_ai.client.ReportAiClient;
import com.aivle.conservation_backend.report_ai.dto.DocxRequestDto;
import com.aivle.conservation_backend.report_ai.dto.GenerateReportRequestDto;
import com.aivle.conservation_backend.report_ai.dto.ReportDocumentResponseDto;
import com.aivle.conservation_backend.report_ai.dto.SaveReportRequestDto;
import com.aivle.conservation_backend.report_ai.service.ArtifactSourceAdapter;
import com.aivle.conservation_backend.report_ai.service.ConservationGuideSourceAdapter;
import com.aivle.conservation_backend.report_ai.service.PotterySourceAdapter;
import com.aivle.conservation_backend.report_ai.service.ReportDocumentService;
import com.aivle.conservation_backend.report_ai.service.XraySourceAdapter;

import lombok.RequiredArgsConstructor;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
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
import java.util.UUID;

/**
 * 보고서 자동생성 연동 컨트롤러.
 *
 * `save()`/`latest()`만 DB(report_document)에 실제로 저장/조회하고,
 * 나머지(`generate`/`generate/docx`/`docx`, `*-source`)는 report-ai를
 * 그대로 감싸거나 각 파트 RDS 결과를 변환해서 보여주기만 한다 - 유물
 * 기본정보/X-ray는 실제 테이블에서 조회되지만, 보존가이드/육안조사는
 * 아직 해당 파트 DB 연동이 끝나지 않아 호출자가 직접 채워야 한다
 * (README 참고).
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
    private final ArtifactSourceAdapter artifactSourceAdapter;
    private final ConservationGuideSourceAdapter conservationGuideSourceAdapter;
    private final ReportDocumentService reportDocumentService;
    private final ArtifactAccessService artifactAccessService;

    /**
     * 생성 직후 report_json만 먼저 저장한다.
     * DOCX 생성/사진 수집은 사용자가 실제로 저장하기를 눌렀을 때 수행하므로
     * 페이지 재진입 시 기존 미리보기를 빠르게 복원할 수 있다.
     */
    @PostMapping("/{artifactId}/save-json")
    public ResponseEntity<ReportDocumentResponseDto> saveJson(
            @PathVariable UUID artifactId,
            @RequestBody SaveReportRequestDto request
    ) {
        artifactAccessService.requireArtifact(artifactId);
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(reportDocumentService.saveJson(artifactId, request));
    }

    /**
     * 미리보기까지 끝난 report_json을 .docx로 변환해 S3에 영구 저장하고,
     * 그 결과를 DB(report_document)에 기록한다 (LLM 재호출 없음).
     *
     * assessment_report(vca 파이프라인 산출물)와는 별개 테이블이다 -
     * 육안조사 실행 여부와 무관하게 유물(artifact_id) 기준으로 저장한다.
     */
    @PostMapping("/{artifactId}/save")
    public ResponseEntity<ReportDocumentResponseDto> save(
            @PathVariable UUID artifactId,
            @RequestBody SaveReportRequestDto request
    ) {
        artifactAccessService.requireArtifact(artifactId);
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(reportDocumentService.save(artifactId, request));
    }

    /** 이 유물의 가장 최근 저장 보고서를 조회한다 (게시판 등에서 재조회용). */
    @GetMapping("/{artifactId}")
    public ResponseEntity<ReportDocumentResponseDto> latest(@PathVariable UUID artifactId) {
        artifactAccessService.requireArtifact(artifactId);
        return ResponseEntity.ok(reportDocumentService.findLatest(artifactId));
    }

    /**
     * 유물 기본정보(`artifacts` 테이블)를 report-ai 입력 형태
     * (relic_info)로 변환해서 보여준다.
     *
     * 다른 source 엔드포인트와 동일하게 generate()의 동작은 바꾸지
     * 않는 별도 조회용 엔드포인트다. 이 응답을 그대로
     * GenerateReportRequestDto.relicInfo에 넣으면 된다.
     */
    @GetMapping("/{artifactId}/relic-info-source")
    public ResponseEntity<Map<String, Object>> relicInfoSource(@PathVariable String artifactId) {
        artifactAccessService.requireArtifact(artifactId);
        return artifactSourceAdapter.resolve(artifactId)
                .map(ResponseEntity::ok)
                .orElseGet(() -> ResponseEntity.ok(Map.of()));
    }

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
        artifactAccessService.requireArtifact(artifactId);
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
        artifactAccessService.requireArtifact(artifactId);
        return potterySourceAdapter.resolve(artifactId)
                .map(source -> ResponseEntity.ok(source.asMap()))
                .orElseGet(() -> ResponseEntity.ok(Map.of(
                        "inspection_text", "",
                        "human_review_recommended", false,
                        "detail", Map.of()
                )));
    }

    /**
     * 보존가이드가 RDS에 저장해둔 결과(tasks.results)를 report-ai 입력
     * 형태(guide_result)로 변환해서 보여준다.
     *
     * 다른 source 엔드포인트와 동일하게 generate()의 동작을 바꾸지 않는
     * 별도 조회용 엔드포인트다. 이 응답을 그대로
     * GenerateReportRequestDto.guideResult에 넣으면 된다. 아직 어떤
     * 단계도 완료되지 않았으면(진행 중이거나 시작 전) 빈 객체를 반환한다.
     */
    @GetMapping("/{artifactId}/conservation-guide-source")
    public ResponseEntity<Map<String, Object>> conservationGuideSource(@PathVariable String artifactId) {
        artifactAccessService.requireArtifact(artifactId);
        return conservationGuideSourceAdapter.resolve(artifactId)
                .map(ResponseEntity::ok)
                .orElseGet(() -> ResponseEntity.ok(Map.of()));
    }

    /** report_json만 생성한다. */
    @PostMapping("/generate")
    public ResponseEntity<Map<String, Object>> generate(@RequestBody GenerateReportRequestDto request) {
        artifactAccessService.requireArtifact(request.artifactId());
        return ResponseEntity.ok(reportAiClient.generateReport(request));
    }

    /** report_json 생성 + .docx 변환을 한 번에 한다 (데모/직접 테스트용). */
    @PostMapping("/generate/docx")
    public ResponseEntity<ByteArrayResource> generateDocx(@RequestBody GenerateReportRequestDto request) {
        artifactAccessService.requireArtifact(request.artifactId());
        byte[] docx = reportAiClient.generateReportDocx(request);
        return docxResponse(request.artifactId(), docx);
    }

    /** 이미 생성/저장된 report_json을 .docx로 변환만 한다 (LLM 재호출 없음). */
    @PostMapping("/docx")
    public ResponseEntity<ByteArrayResource> toDocx(@RequestBody DocxRequestDto request) {
        artifactAccessService.requireArtifact(request.artifactId());
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
