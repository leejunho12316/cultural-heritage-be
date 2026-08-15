package com.aivle.conservation_backend.xray_api.controller;

import com.aivle.conservation_backend.artifact.service.ArtifactAccessService;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.CompleteResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.DefectListResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.DefectReviewRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.DetectRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.ReportGenerateRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.ReportTextRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.ReportTextResponse;
import com.aivle.conservation_backend.xray_api.service.XrayWorkflowService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/xray/jobs")
public class XrayWorkflowController {

    private final XrayWorkflowService workflowService;
    private final ArtifactAccessService artifactAccessService;

    public XrayWorkflowController(XrayWorkflowService workflowService, ArtifactAccessService artifactAccessService) {
        this.workflowService = workflowService;
        this.artifactAccessService = artifactAccessService;
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

    @PostMapping("/{jobId}/report-text/generate")
    public ResponseEntity<ReportTextResponse> generateReportText(
            @PathVariable String jobId,
            @RequestBody(required = false) ReportGenerateRequest request
    ) {
        artifactAccessService.requireXrayJob(jobId);
        return ResponseEntity.ok(workflowService.generateReportText(jobId, request));
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
