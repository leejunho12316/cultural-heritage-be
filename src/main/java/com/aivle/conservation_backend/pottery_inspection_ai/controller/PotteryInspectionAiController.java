package com.aivle.conservation_backend.pottery_inspection_ai.controller;

import com.aivle.conservation_backend.pottery_inspection_ai.client.PotteryInspectionAiClient;
import com.aivle.conservation_backend.pottery_inspection_ai.dto.PotteryInspectionResponseDto;
import lombok.RequiredArgsConstructor;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.multipart.MultipartFile;

import java.util.Map;

@RequiredArgsConstructor
@RestController
@RequestMapping("/pottery-inspection")
public class PotteryInspectionAiController {

    private final PotteryInspectionAiClient client;

    /**
     * 기존 동기 방식. 사진에 문양이 여러 개면 확정된 문양별 상태조사까지
     * 순차로 이어져서 60초를 넘길 수 있고, 그동안 이 요청을 붙들고 있는
     * ALB가 유휴 타임아웃(기본 60초)으로 끊어 504가 나는 경우가 있었다.
     * 새로 만드는 곳에서는 아래 /jobs(접수 후 폴링) 방식을 쓰는 걸
     * 권장한다 - 이 엔드포인트는 하위 호환을 위해 남겨둔다.
     */
    @PostMapping(consumes = "multipart/form-data")
    public ResponseEntity<Object> inspect(
            @RequestParam("image") MultipartFile image,
            @RequestParam(name = "n_calls", defaultValue = "3") int nCalls,
            @RequestParam(name = "use_vlm_pattern", defaultValue = "true") boolean useVlmPattern,
            @RequestParam(name = "treat_as_single_artifact", defaultValue = "false") boolean treatAsSingleArtifact
    ) {
        try {
            PotteryInspectionResponseDto result =
                    client.inspect(image, nCalls, useVlmPattern, treatAsSingleArtifact);
            return ResponseEntity.ok(result);
        } catch (RestClientResponseException e) {
            return ResponseEntity.status(e.getStatusCode())
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(e.getResponseBodyAsString());
        }
    }

    /** 분석을 백그라운드로 접수하고 {job_id, status}를 즉시 반환한다. */
    @PostMapping(path = "/jobs", consumes = "multipart/form-data")
    public ResponseEntity<Object> createJob(
            @RequestParam("image") MultipartFile image,
            @RequestParam(name = "n_calls", defaultValue = "3") int nCalls,
            @RequestParam(name = "use_vlm_pattern", defaultValue = "true") boolean useVlmPattern,
            @RequestParam(name = "treat_as_single_artifact", defaultValue = "false") boolean treatAsSingleArtifact
    ) {
        try {
            Map<String, Object> result =
                    client.createInspectionJob(image, nCalls, useVlmPattern, treatAsSingleArtifact);
            return ResponseEntity.status(202).body(result);
        } catch (RestClientResponseException e) {
            return ResponseEntity.status(e.getStatusCode())
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(e.getResponseBodyAsString());
        }
    }

    /** job 상태를 폴링한다. FE가 보통 1~2초 간격으로 반복 호출한다. */
    @GetMapping("/jobs/{jobId}")
    public ResponseEntity<Object> getJob(@PathVariable String jobId) {
        try {
            Map<String, Object> result = client.getInspectionJob(jobId);
            return ResponseEntity.ok(result);
        } catch (RestClientResponseException e) {
            return ResponseEntity.status(e.getStatusCode())
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(e.getResponseBodyAsString());
        }
    }
}