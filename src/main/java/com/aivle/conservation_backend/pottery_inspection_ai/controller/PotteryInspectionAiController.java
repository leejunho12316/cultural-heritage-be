package com.aivle.conservation_backend.pottery_inspection_ai.controller;

import com.aivle.conservation_backend.artifact.service.ArtifactAccessService;
import com.aivle.conservation_backend.pottery_inspection_ai.client.PotteryInspectionAiClient;
import com.aivle.conservation_backend.pottery_inspection_ai.dto.PotteryInspectionJobResponseDto;
import com.aivle.conservation_backend.pottery_inspection_ai.dto.PotteryInspectionResponseDto;
import com.aivle.conservation_backend.pottery_inspection_ai.service.PotteryInspectionJobService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.multipart.MultipartFile;

import java.util.UUID;

@RequiredArgsConstructor
@RestController
@RequestMapping("/pottery-inspection")
public class PotteryInspectionAiController {

    private final PotteryInspectionAiClient client;
    private final PotteryInspectionJobService jobService;
    private final ArtifactAccessService artifactAccessService;

    /** 기존 동기 방식. 하위 호환을 위해 유지한다. */
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

    /**
     * 새 육안조사를 접수한다. assessment_run 생성, 원본 S3 저장, FastAPI job
     * 접수를 모두 Spring이 관리하므로 FE는 jobId를 localStorage에 저장하지 않는다.
     */
    @PostMapping(path = "/jobs", consumes = "multipart/form-data")
    public ResponseEntity<PotteryInspectionJobResponseDto> createJob(
            @RequestParam("artifact_id") UUID artifactId,
            @RequestParam("image") MultipartFile image,
            @RequestParam(name = "n_calls", defaultValue = "3") int nCalls,
            @RequestParam(name = "use_vlm_pattern", defaultValue = "true") boolean useVlmPattern,
            @RequestParam(name = "treat_as_single_artifact", defaultValue = "false") boolean treatAsSingleArtifact
    ) {
        artifactAccessService.requireArtifact(artifactId);
        PotteryInspectionJobResponseDto result = jobService.createJob(
                artifactId,
                image,
                nCalls,
                useVlmPattern,
                treatAsSingleArtifact
        );
        return ResponseEntity.accepted().body(result);
    }

    /** artifactId 기준 가장 최근 육안조사 작업을 복원한다. */
    @GetMapping("/jobs/latest")
    public ResponseEntity<PotteryInspectionJobResponseDto> getLatestJob(
            @RequestParam("artifact_id") UUID artifactId
    ) {
        artifactAccessService.requireArtifact(artifactId);
        return ResponseEntity.ok(jobService.getLatestJob(artifactId));
    }

    /** 특정 assessment run의 서버 영속 상태를 조회한다. */
    @GetMapping("/jobs/{assessmentRunId}")
    public ResponseEntity<PotteryInspectionJobResponseDto> getJob(
            @PathVariable UUID assessmentRunId,
            @RequestParam("artifact_id") UUID artifactId
    ) {
        artifactAccessService.requireArtifact(artifactId);
        return ResponseEntity.ok(jobService.getJob(artifactId, assessmentRunId));
    }
}
