package com.aivle.conservation_backend.xray_api.controller;

import com.aivle.conservation_backend.xray_api.dto.XrayJobResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayJobStatusResponse;
import com.aivle.conservation_backend.xray_api.service.XrayStitchService;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

@RestController
@RequestMapping("/api/xray/stitch/jobs")
public class XrayStitchController {

    private final XrayStitchService xrayStitchService;

    public XrayStitchController(
            XrayStitchService xrayStitchService
    ) {
        this.xrayStitchService = xrayStitchService;
    }

    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<XrayJobResponse> createJob(
            @RequestParam("artifactId") String artifactId,
            @RequestParam("colorFiles") List<MultipartFile> colorFiles,
            @RequestParam("xrayFiles") List<MultipartFile> xrayFiles
    ) {
        XrayJobResponse response = xrayStitchService.createJob(
                artifactId,
                colorFiles,
                xrayFiles
        );

        return ResponseEntity
                .status(HttpStatus.ACCEPTED)
                .body(response);
    }

    @GetMapping("/{jobId}")
    public ResponseEntity<XrayJobStatusResponse> getJobStatus(
            @PathVariable String jobId
    ) {
        XrayJobStatusResponse response =
                xrayStitchService.getLocalJobStatus(jobId);

        return ResponseEntity.ok(response);
    }
}