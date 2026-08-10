package com.aivle.conservation_backend.xray_api.controller;

import com.aivle.conservation_backend.xray_api.dto.XrayFinalLayoutRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayJobResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayJobStatusResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchCallbackRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchDtos.PrepareRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchDtos.PrepareResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchDtos.ReconcileResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchDtos.SourceResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchDtos.SourceTarget;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchDtos.StartRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchDtos.UrlResponse;
import com.aivle.conservation_backend.xray_api.service.XrayStitchService;
import com.aivle.conservation_backend.xray_api.storage.XrayS3Keys;
import org.springframework.core.io.Resource;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.stream.IntStream;

@RestController
@RequestMapping("/api/xray/stitch")
public class XrayStitchController {

    private final XrayStitchService stitchService;

    public XrayStitchController(XrayStitchService stitchService) {
        this.stitchService = stitchService;
    }

    /** New FE flow: obtain presigned PUT URLs, then upload directly to S3. */
    @PostMapping(
            value = "/jobs/prepare",
            consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE
    )
    public ResponseEntity<PrepareResponse> prepare(@RequestBody PrepareRequest request) {
        return ResponseEntity.status(HttpStatus.CREATED).body(stitchService.prepare(request));
    }

    /**
     * Compatibility flow for the current main FE. Files still end up in S3;
     * no shared/EFS directory is used.
     */
    @PostMapping(
            value = "/jobs",
            consumes = MediaType.MULTIPART_FORM_DATA_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE
    )
    public ResponseEntity<XrayJobResponse> createMultipartJob(
            @RequestParam("artifactId") String artifactId,
            @RequestParam("colorFiles") List<MultipartFile> colorFiles,
            @RequestParam("xrayFiles") List<MultipartFile> xrayFiles
    ) {
        return ResponseEntity.accepted().body(
                stitchService.createJob(artifactId, colorFiles, xrayFiles)
        );
    }

    @PostMapping(
            value = "/jobs/{jobId}/start",
            consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE
    )
    public ResponseEntity<XrayJobStatusResponse> start(
            @PathVariable String jobId,
            @RequestBody StartRequest request
    ) {
        return ResponseEntity.accepted().body(
                stitchService.start(jobId, request.colorFileName(), request.xrayFileNames())
        );
    }

    @PostMapping(
            value = "/callback",
            consumes = MediaType.APPLICATION_JSON_VALUE
    )
    public ResponseEntity<Void> callback(
            @RequestBody XrayStitchCallbackRequest request,
            @RequestHeader(value = "X-Xray-Callback-Token", required = false) String token
    ) {
        stitchService.handleCallback(request, token);
        return ResponseEntity.ok().build();
    }

    @GetMapping(value = "/jobs/{jobId}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<XrayJobStatusResponse> getStatus(@PathVariable String jobId) {
        return ResponseEntity.ok(stitchService.getLocalJobStatus(jobId));
    }

    @PostMapping(value = "/jobs/{jobId}/reconcile", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<ReconcileResponse> reconcile(@PathVariable String jobId) {
        return ResponseEntity.ok(stitchService.reconcile(jobId));
    }

    // Presigned URL endpoints used by the S3-native FE.
    @GetMapping(value = "/jobs/{jobId}/result-url", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<UrlResponse> getResultUrl(@PathVariable String jobId) {
        XrayJobStatusResponse job = stitchService.getLocalJobStatus(jobId);
        return ResponseEntity.ok(new UrlResponse(
                stitchService.getAssembledUrl(jobId),
                XrayS3Keys.assembled(job.artifactId())
        ));
    }

    @GetMapping(value = "/jobs/{jobId}/layout-url", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<UrlResponse> getLayoutUrl(@PathVariable String jobId) {
        XrayJobStatusResponse job = stitchService.getLocalJobStatus(jobId);
        return ResponseEntity.ok(new UrlResponse(
                stitchService.getLayoutUrl(jobId),
                XrayS3Keys.layout(job.artifactId())
        ));
    }

    @GetMapping(value = "/jobs/{jobId}/sources", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<SourceResponse> getSourceUrls(@PathVariable String jobId) {
        List<String> fileNames = stitchService.getOrderedXraySourceFileNames(jobId);
        List<String> urls = stitchService.getOrderedXraySourceUrls(jobId);
        List<SourceTarget> sources = IntStream.range(0, fileNames.size())
                .mapToObj(index -> new SourceTarget(index, fileNames.get(index), urls.get(index)))
                .toList();
        return ResponseEntity.ok(new SourceResponse(sources));
    }

    @GetMapping(value = "/jobs/{jobId}/report", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<UrlResponse> getReportUrl(@PathVariable String jobId) {
        XrayJobStatusResponse job = stitchService.getLocalJobStatus(jobId);
        return ResponseEntity.ok(new UrlResponse(
                stitchService.getReportUrl(jobId),
                XrayS3Keys.report(job.artifactId())
        ));
    }

    @GetMapping(value = "/jobs/{jobId}/result/final-url", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<UrlResponse> getFinalResultUrl(@PathVariable String jobId) {
        XrayJobStatusResponse job = stitchService.requireFinalizedJob(jobId);
        return ResponseEntity.ok(new UrlResponse(
                stitchService.getFinalAssembledUrl(jobId),
                XrayS3Keys.finalAssembled(job.artifactId())
        ));
    }

    // Current-main compatibility: actual layout JSON and image bytes.
    @GetMapping(value = "/jobs/{jobId}/layout", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> getLayout(@PathVariable String jobId) {
        return ResponseEntity.ok(stitchService.getLayout(jobId));
    }

    @GetMapping(value = "/jobs/{jobId}/result", produces = MediaType.IMAGE_PNG_VALUE)
    public ResponseEntity<Resource> getResultImage(@PathVariable String jobId) {
        return imageResponse(stitchService.getResult(jobId), "assembled-xray.png");
    }

    @GetMapping(value = "/jobs/{jobId}/result/final", produces = MediaType.IMAGE_PNG_VALUE)
    public ResponseEntity<Resource> getFinalResultImage(@PathVariable String jobId) {
        return imageResponse(stitchService.getFinalResult(jobId), "assembled-xray-final.png");
    }

    @PutMapping(
            value = "/jobs/{jobId}/layout/final",
            consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE
    )
    public ResponseEntity<String> saveFinalLayout(
            @PathVariable String jobId,
            @RequestBody XrayFinalLayoutRequest request
    ) {
        return ResponseEntity.ok(stitchService.saveFinalLayout(jobId, request));
    }

    @GetMapping(value = "/jobs/{jobId}/layout/final", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> getFinalLayout(@PathVariable String jobId) {
        return ResponseEntity.ok(stitchService.getFinalLayout(jobId));
    }

    private ResponseEntity<Resource> imageResponse(Resource resource, String fileName) {
        ContentDisposition disposition = ContentDisposition.inline()
                .filename(fileName, StandardCharsets.UTF_8)
                .build();
        return ResponseEntity.ok()
                .contentType(MediaType.IMAGE_PNG)
                .header(HttpHeaders.CONTENT_DISPOSITION, disposition.toString())
                .body(resource);
    }
}