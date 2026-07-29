package com.aivle.conservation_backend.xray_api.controller;

import com.aivle.conservation_backend.xray_api.dto.XrayJobResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayJobStatusResponse;
import com.aivle.conservation_backend.xray_api.service.XrayStitchService;

import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * X-ray 조각 결합 API.
 *
 * 이상영역 탐지는 XrayInspectionController 가 담당한다.
 * 파일을 나눈 이유는 두 파트가 동시에 작업할 때
 * git 충돌이 나는 것을 막기 위함이다.
 *
 * 비동기 작업 방식을 쓰는 이유
 *
 *   결합은 조각 수에 따라 수 분에서 수십 분이 걸린다.
 *   동기 호출로 두면 브라우저나 프록시가 먼저 연결을 끊어
 *   결과를 받을 수 없다. 그래서 작업을 접수하고 jobId 를
 *   먼저 돌려준 뒤, 프론트가 상태를 폴링하도록 한다.
 *
 * 결과를 base64 가 아니라 바이너리로 내려주는 이유
 *
 *   프론트는 결합본을 File 객체로 받아 미리보기에 쓰고,
 *   이상영역 탐지와 문안 생성에 다시 업로드한다. JSON 에
 *   base64 로 실어 보내면 프론트가 매번 되돌려야 하고
 *   전송량도 약 33퍼센트 늘어난다.
 */
@RestController
@RequestMapping("/api/xray/stitch")
public class XrayStitchController {

    private final XrayStitchService xrayStitchService;

    public XrayStitchController(
            XrayStitchService xrayStitchService
    ) {
        this.xrayStitchService = xrayStitchService;
    }

    // ------------------------------------------------------------
    // 작업 생성
    // ------------------------------------------------------------

    /**
     * 조각을 업로드해 결합 작업을 만든다.
     *
     * 결합을 기다리지 않고 즉시 jobId 를 반환한다.
     */
    @PostMapping("/jobs")
    public ResponseEntity<XrayJobResponse> createJob(
            @RequestParam("artifactId") String artifactId,

            @RequestParam("xrayFiles") List<MultipartFile> xrayFiles,

            @RequestParam(value = "colorFiles", required = false)
            List<MultipartFile> colorFiles,

            @RequestParam(value = "configName", required = false)
            String configName
    ) {
        XrayJobResponse response = xrayStitchService.createJob(
                artifactId, xrayFiles, colorFiles, configName
        );

        return ResponseEntity
                .status(HttpStatus.ACCEPTED)
                .body(response);
    }

    // ------------------------------------------------------------
    // 상태 폴링
    // ------------------------------------------------------------

    /**
     * 작업 상태를 조회한다.
     *
     * status 는 PENDING, RUNNING, COMPLETED, FAILED 중 하나다.
     * FAILED 이면 errorMessage 에 원인이 들어 있다.
     */
    @GetMapping("/jobs/{jobId}")
    public ResponseEntity<XrayJobStatusResponse> getJobStatus(
            @PathVariable String jobId
    ) {
        return ResponseEntity.ok(
                xrayStitchService.getJobStatus(jobId)
        );
    }

    // ------------------------------------------------------------
    // 결과 내려받기
    // ------------------------------------------------------------

    /**
     * 완료된 결합본 이미지를 내려준다.
     *
     * 프론트는 이 응답을 blob 으로 받아 File 로 만들어
     * 이상영역 탐지에 그대로 넘긴다.
     */
    @GetMapping("/jobs/{jobId}/result")
    public ResponseEntity<byte[]> getResult(
            @PathVariable String jobId
    ) {
        byte[] image = xrayStitchService.getResultImage(jobId);

        if (image == null) {
            return ResponseEntity.notFound().build();
        }

        return ResponseEntity.ok()
                .contentType(MediaType.IMAGE_PNG)
                .header(
                        HttpHeaders.CONTENT_DISPOSITION,
                        "inline; filename=\"assembled-"
                                + jobId + ".png\""
                )
                .body(image);
    }

    // ------------------------------------------------------------
    // 오류 처리
    // ------------------------------------------------------------

    /**
     * 없는 jobId 조회나 잘못된 입력을 400 으로 돌려준다.
     *
     * 이것이 없으면 스프링이 500 으로 뭉개 버려서
     * 프론트에서 원인을 알 수 없다.
     */
    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<Map<String, Object>> handleBadRequest(
            IllegalArgumentException e
    ) {
        Map<String, Object> body = new LinkedHashMap<>();

        body.put("success", false);
        body.put("message", e.getMessage());

        return ResponseEntity
                .status(HttpStatus.BAD_REQUEST)
                .body(body);
    }
}
