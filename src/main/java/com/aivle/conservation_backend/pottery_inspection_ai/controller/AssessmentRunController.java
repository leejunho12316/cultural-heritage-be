package com.aivle.conservation_backend.vca.controller;

import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import com.aivle.conservation_backend.vca.dto.AssessmentRunResponseDto;
import com.aivle.conservation_backend.vca.repository.AssessmentRunRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.UUID;

/**
 * VCA 실행(AssessmentRun) 생성/조회.
 *
 * 지금은 육안조사 전용으로 쓰기로 확인했지만(2026-08 대화 기준), 스키마 자체는
 * artifact_id 기준으로 범용이라 X-ray/보존가이드가 나중에 같이 쓸 수도 있다.
 */
@RequiredArgsConstructor
@RestController
@RequestMapping("/api/vca/{artifactId}/runs")
public class AssessmentRunController {

    private static final List<String> ACTIVE_STATUSES = List.of("queued", "running");

    private final AssessmentRunRepository assessmentRunRepository;

    /** 진행 중(active) run이 있으면 409, 없으면 새 run을 만든다. */
    @PostMapping
    public ResponseEntity<AssessmentRunResponseDto> create(@PathVariable UUID artifactId) {
        if (assessmentRunRepository.existsByArtifactIdAndStatusIn(artifactId, ACTIVE_STATUSES)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "ACTIVE_RUN_EXISTS");
        }

        int nextRunNumber = assessmentRunRepository.findMaxRunNumberByArtifactId(artifactId) + 1;

        AssessmentRun run = assessmentRunRepository.save(
                AssessmentRun.create(UUID.randomUUID(), artifactId, nextRunNumber, null, false, null, null)
        );

        return ResponseEntity.ok(AssessmentRunResponseDto.from(run));
    }

    /** 최신순으로 이 유물의 run 전체를 돌려준다 - 409 났을 때 FE가 활성 run을 찾는 용도. */
    @GetMapping
    public ResponseEntity<List<AssessmentRunResponseDto>> list(@PathVariable UUID artifactId) {
        List<AssessmentRunResponseDto> runs = assessmentRunRepository
                .findAllByArtifactIdOrderByRunNumberDesc(artifactId).stream()
                .map(AssessmentRunResponseDto::from)
                .toList();
        return ResponseEntity.ok(runs);
    }

    @GetMapping("/{assessmentRunId}")
    public ResponseEntity<AssessmentRunResponseDto> get(
            @PathVariable UUID artifactId,
            @PathVariable UUID assessmentRunId
    ) {
        return assessmentRunRepository.findByIdAndArtifactId(assessmentRunId, artifactId)
                .map(run -> ResponseEntity.ok(AssessmentRunResponseDto.from(run)))
                .orElseGet(() -> ResponseEntity.notFound().build());
    }
}