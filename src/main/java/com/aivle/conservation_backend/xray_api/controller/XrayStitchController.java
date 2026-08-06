package com.aivle.conservation_backend.xray_api.controller;

import com.aivle.conservation_backend.xray_api.dto.XrayFinalLayoutRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayJobResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayJobStatusResponse;
import com.aivle.conservation_backend.xray_api.service.XrayStitchService;
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
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
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

    @GetMapping(
            value = "/{jobId}/result",
            produces = MediaType.IMAGE_PNG_VALUE
    )
    public ResponseEntity<Resource> getJobResult(
            @PathVariable String jobId
    ) {
        try {
            Resource result = xrayStitchService.getResult(jobId);
            ContentDisposition disposition = ContentDisposition
                    .inline()
                    .filename("assembled-xray.png", StandardCharsets.UTF_8)
                    .build();

            return ResponseEntity.ok()
                    .contentType(MediaType.IMAGE_PNG)
                    .header(
                            HttpHeaders.CONTENT_DISPOSITION,
                            disposition.toString()
                    )
                    .body(result);
        } catch (IllegalStateException e) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    e.getMessage(),
                    e
            );
        }
    }
    @GetMapping(
            value = "/{jobId}/result/final",
            produces = MediaType.IMAGE_PNG_VALUE
    )
    public ResponseEntity<Resource> getFinalJobResult(
            @PathVariable String jobId
    ) {
        try {
            Resource result = xrayStitchService.getFinalResult(jobId);
            ContentDisposition disposition = ContentDisposition
                    .inline()
                    .filename("assembled-xray-final.png", StandardCharsets.UTF_8)
                    .build();

            return ResponseEntity.ok()
                    .contentType(MediaType.IMAGE_PNG)
                    .header(HttpHeaders.CONTENT_DISPOSITION, disposition.toString())
                    .body(result);
        } catch (IllegalStateException e) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    e.getMessage(),
                    e
            );
        }
    }

    /**
     * Konva에서 최종 확정한 조각 위치/회전을 저장한다.
     *
     * 자동 결합 결과인 layout.json은 보존하고, 최종 보정 결과는
     * 같은 결과 디렉터리의 layout.final.json으로 별도 저장한다.
     */
    @PutMapping(
            value = "/{jobId}/layout/final",
            consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE
    )
    public ResponseEntity<String> saveFinalJobLayout(
            @PathVariable String jobId,
            @RequestBody XrayFinalLayoutRequest request
    ) {
        return ResponseEntity.ok(
                xrayStitchService.saveFinalLayout(jobId, request)
        );
    }

    /**
     * Konva 보정까지 반영해 저장한 최종 layout을 조회한다.
     */
    @GetMapping(
            value = "/{jobId}/layout/final",
            produces = MediaType.APPLICATION_JSON_VALUE
    )
    public ResponseEntity<String> getFinalJobLayout(
            @PathVariable String jobId
    ) {
        return ResponseEntity.ok(xrayStitchService.getFinalLayout(jobId));
    }

    /**
     * 조각별 자동 배치 정보를 조회한다.
     *
     * 수동 보정 화면이 이 값으로 조각을 개별 배치한다.
     * 결합 결과 이미지만으로는 조각을 따로 움직일 수 없다.
     */
    @GetMapping(
            value = "/{jobId}/layout",
            produces = MediaType.APPLICATION_JSON_VALUE
    )
    public ResponseEntity<String> getJobLayout(
            @PathVariable String jobId
    ) {
        return ResponseEntity.ok(xrayStitchService.getLayout(jobId));
    }
}
