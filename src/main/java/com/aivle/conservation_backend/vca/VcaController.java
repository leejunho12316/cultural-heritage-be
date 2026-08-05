package com.aivle.conservation_backend.vca;

import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.net.URI;

@RestController
@RequestMapping("/api/vca")
public class VcaController {

    private final VcaService vcaService;

    public VcaController(VcaService vcaService) {
        this.vcaService = vcaService;
    }

    @GetMapping
    public VcaResponses.ArtifactCollection getArtifacts() {
        return vcaService.getArtifacts();
    }

    @GetMapping("/{artifactId}")
    public VcaResponses.ArtifactDetail getArtifact(@PathVariable String artifactId) {
        return vcaService.getArtifact(artifactId);
    }

    @GetMapping("/{artifactId}/runs/{assessmentRunId}/report")
    public VcaResponses.Report getReport(
            @PathVariable String artifactId,
            @PathVariable String assessmentRunId
    ) {
        return vcaService.getReport(artifactId, assessmentRunId);
    }

    @GetMapping("/{artifactId}/files/sha256/{sha256}")
    public ResponseEntity<Void> getFile(
            @PathVariable String artifactId,
            @PathVariable String sha256
    ) {
        return ResponseEntity.status(HttpStatus.SEE_OTHER)
                .location(vcaService.getFileDownloadLocation(artifactId, sha256))
                .build();
    }

    @PostMapping("/{artifactId}/images/presign")
    public ResponseEntity<VcaResponses.PresignImage> presignImage(
            @PathVariable String artifactId,
            @Valid @RequestBody VcaRequests.PresignImageRequest request
    ) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(vcaService.presignImage(artifactId, request));
    }

    @PostMapping("/{artifactId}/images/{imageId}/complete")
    public VcaResponses.Image completeImage(
            @PathVariable String artifactId,
            @PathVariable String imageId,
            @Valid @RequestBody VcaRequests.CompleteImageRequest request
    ) {
        return vcaService.completeImage(artifactId, imageId, request);
    }

    @DeleteMapping("/{artifactId}/images/{imageId}")
    public ResponseEntity<Void> deleteImage(
            @PathVariable String artifactId,
            @PathVariable String imageId
    ) {
        vcaService.deleteImage(artifactId, imageId);
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/{artifactId}/runs")
    public ResponseEntity<VcaResponses.Run> createRun(@PathVariable String artifactId) {
        return ResponseEntity.accepted().body(vcaService.createRun(artifactId));
    }

    @PostMapping("/{artifactId}/runs/{assessmentRunId}/report/pdf")
    public ResponseEntity<VcaResponses.PdfJob> createPdfJob(
            @PathVariable String artifactId,
            @PathVariable String assessmentRunId
    ) {
        return ResponseEntity.accepted()
                .body(vcaService.createPdfJob(artifactId, assessmentRunId));
    }

    @GetMapping("/{artifactId}/report-pdf-jobs/{jobId}")
    public VcaResponses.PdfJob getPdfJob(
            @PathVariable String artifactId,
            @PathVariable String jobId
    ) {
        return vcaService.getPdfJob(artifactId, jobId);
    }

    @GetMapping("/{artifactId}/report-pdf-jobs/{jobId}/download")
    public ResponseEntity<Void> downloadPdf(
            @PathVariable String artifactId,
            @PathVariable String jobId
    ) {
        URI location = vcaService.getPdfDownloadLocation(artifactId, jobId);
        return ResponseEntity.status(HttpStatus.SEE_OTHER).location(location).build();
    }

    @RequestMapping("/**")
    public void handleUnknownVcaEndpoint() {
        throw new VcaApiException(
                HttpStatus.NOT_FOUND,
                "VCA_ENDPOINT_NOT_FOUND",
                "The requested VCA endpoint was not found."
        );
    }
}
