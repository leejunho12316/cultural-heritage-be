package com.aivle.conservation_backend.vca.controller;

import com.aivle.conservation_backend.vca.dto.ArtifactCollectionResponse;
import com.aivle.conservation_backend.vca.dto.ArtifactDetailResponse;
import com.aivle.conservation_backend.vca.dto.CompleteImageRequest;
import com.aivle.conservation_backend.vca.dto.ImageResponse;
import com.aivle.conservation_backend.vca.dto.IntermediateResultsResponse;
import com.aivle.conservation_backend.vca.dto.PdfJobResponse;
import com.aivle.conservation_backend.vca.dto.PresignImageRequest;
import com.aivle.conservation_backend.vca.dto.PresignImageResponse;
import com.aivle.conservation_backend.vca.dto.ReportResponse;
import com.aivle.conservation_backend.vca.dto.RunResponse;
import com.aivle.conservation_backend.vca.exception.VcaApiException;
import com.aivle.conservation_backend.vca.service.VcaService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.net.URI;

@RestController
@RequestMapping("/api/vca")
public class VcaController {

    private final VcaService vcaService;

    public VcaController(VcaService vcaService) {
        this.vcaService = vcaService;
    }

    @GetMapping
    public ArtifactCollectionResponse getArtifacts() {
        return vcaService.getArtifacts();
    }

    @GetMapping("/{artifactId}")
    public ArtifactDetailResponse getArtifact(@PathVariable String artifactId) {
        return vcaService.getArtifact(artifactId);
    }

    @GetMapping("/{artifactId}/runs/{assessmentRunId}/report")
    public ReportResponse getReport(
            @PathVariable String artifactId,
            @PathVariable String assessmentRunId
    ) {
        return vcaService.getReport(artifactId, assessmentRunId);
    }

    @GetMapping("/{artifactId}/runs/{assessmentRunId}/intermediate-results")
    public IntermediateResultsResponse getIntermediateResults(
            @PathVariable String artifactId,
            @PathVariable String assessmentRunId
    ) {
        return vcaService.getIntermediateResults(artifactId, assessmentRunId);
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
    public ResponseEntity<PresignImageResponse> presignImage(
            @PathVariable String artifactId,
            @Valid @RequestBody PresignImageRequest request
    ) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(vcaService.presignImage(artifactId, request));
    }

    @PostMapping(
            path = "/{artifactId}/images",
            consumes = MediaType.MULTIPART_FORM_DATA_VALUE
    )
    public ResponseEntity<ImageResponse> uploadImage(
            @PathVariable String artifactId,
            @RequestParam("file") MultipartFile file
    ) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(vcaService.uploadImage(artifactId, file));
    }

    @PostMapping("/{artifactId}/images/{imageId}/complete")
    public ImageResponse completeImage(
            @PathVariable String artifactId,
            @PathVariable String imageId,
            @Valid @RequestBody CompleteImageRequest request
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
    public ResponseEntity<RunResponse> createRun(@PathVariable String artifactId) {
        return ResponseEntity.accepted().body(vcaService.createRun(artifactId));
    }

    @PostMapping("/{artifactId}/runs/{assessmentRunId}/report/pdf")
    public ResponseEntity<PdfJobResponse> createPdfJob(
            @PathVariable String artifactId,
            @PathVariable String assessmentRunId
    ) {
        return ResponseEntity.accepted()
                .body(vcaService.createPdfJob(artifactId, assessmentRunId));
    }

    @GetMapping("/{artifactId}/report-pdf-jobs/{jobId}")
    public PdfJobResponse getPdfJob(
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
