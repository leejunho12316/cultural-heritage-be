package com.aivle.conservation_backend.xray_api.controller;

import com.aivle.conservation_backend.artifact.service.ArtifactAccessService;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.CompleteResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.DefectListResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.DefectReviewRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.DetectRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.ReportGenerateRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.ReportTextRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.ReportTextResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.StageResponse;
import com.aivle.conservation_backend.xray_api.service.XrayWorkflowService;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClientResponseException;

import java.util.Map;

@RestController
@RequestMapping("/api/xray/jobs")
public class XrayWorkflowController {

    private final XrayWorkflowService workflowService;
    private final ArtifactAccessService artifactAccessService;
    private final ObjectMapper objectMapper;

    public XrayWorkflowController(
            XrayWorkflowService workflowService,
            ArtifactAccessService artifactAccessService,
            ObjectMapper objectMapper
    ) {
        this.workflowService = workflowService;
        this.artifactAccessService = artifactAccessService;
        this.objectMapper = objectMapper;
    }

    @PostMapping("/{jobId}/detect")
    public ResponseEntity<DefectListResponse> detect(
            @PathVariable String jobId,
            @RequestBody(required = false) DetectRequest request
    ) {
        artifactAccessService.requireXrayJob(jobId);
        return ResponseEntity.accepted().body(
                workflowService.detect(jobId, request == null ? null : request.confidence())
        );
    }

    @GetMapping("/{jobId}/defects")
    public ResponseEntity<DefectListResponse> getDefects(@PathVariable String jobId) {
        artifactAccessService.requireXrayJob(jobId);
        return ResponseEntity.ok(workflowService.getDefects(jobId));
    }

    @PutMapping("/{jobId}/defects")
    public ResponseEntity<DefectListResponse> updateDefects(
            @PathVariable String jobId,
            @RequestBody DefectReviewRequest request
    ) {
        artifactAccessService.requireXrayJob(jobId);
        return ResponseEntity.ok(workflowService.updateDefects(jobId, request));
    }

    @PostMapping("/{jobId}/report-ready")
    public ResponseEntity<StageResponse> markReportReady(@PathVariable String jobId) {
        artifactAccessService.requireXrayJob(jobId);
        return ResponseEntity.ok(workflowService.markReportReady(jobId));
    }

    @PostMapping("/{jobId}/report-text/generate")
    public ResponseEntity<?> generateReportText(
            @PathVariable String jobId,
            @RequestBody(required = false) ReportGenerateRequest request
    ) {
        artifactAccessService.requireXrayJob(jobId);
        try {
            return ResponseEntity.ok(workflowService.generateReportText(jobId, request));
        } catch (RestClientResponseException error) {
            // xray-ai가 전달한 OpenAI 오류 상태/상세를 그대로 FE까지 전달한다.
            // 예: 429 insufficient_quota, 401 invalid_api_key.
            return ResponseEntity
                    .status(error.getStatusCode())
                    .body(Map.of("detail", upstreamDetail(error)));
        }
    }

    private String upstreamDetail(RestClientResponseException error) {
        String body = error.getResponseBodyAsString();
        if (body == null || body.isBlank()) {
            return "X-ray AI 문안 생성 요청이 실패했습니다.";
        }

        try {
            JsonNode json = objectMapper.readTree(body);
            JsonNode detail = json.get("detail");
            if (detail != null && !detail.isNull()) {
                return detail.isTextual() ? detail.asText() : detail.toString();
            }
        } catch (Exception ignored) {
            // JSON이 아니면 원문을 그대로 사용한다.
        }

        return body;
    }

    @GetMapping("/{jobId}/report-text")
    public ResponseEntity<ReportTextResponse> getReportText(@PathVariable String jobId) {
        artifactAccessService.requireXrayJob(jobId);
        return ResponseEntity.ok(workflowService.getReportText(jobId));
    }

    @PutMapping("/{jobId}/report-text")
    public ResponseEntity<ReportTextResponse> saveReportText(
            @PathVariable String jobId,
            @RequestBody ReportTextRequest request
    ) {
        artifactAccessService.requireXrayJob(jobId);
        return ResponseEntity.ok(workflowService.saveReportText(
                jobId,
                request == null ? null : request.reportText()
        ));
    }

    @PostMapping("/{jobId}/complete")
    public ResponseEntity<CompleteResponse> complete(@PathVariable String jobId) {
        artifactAccessService.requireXrayJob(jobId);
        return ResponseEntity.ok(workflowService.complete(jobId));
    }
}
