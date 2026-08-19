package com.aivle.conservation_backend.pottery_inspection_ai.controller;

import com.aivle.conservation_backend.artifact.service.ArtifactAccessService;
import com.aivle.conservation_backend.pottery_inspection_ai.dto.InspectionResultPotteryResponseDto;
import com.aivle.conservation_backend.pottery_inspection_ai.dto.PotteryInspectionResponseDto;
import com.aivle.conservation_backend.pottery_inspection_ai.service.PotteryInspectionJobService;
import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import com.aivle.conservation_backend.vca.domain.InspectionResultPottery;
import com.aivle.conservation_backend.vca.repository.AssessmentRunRepository;
import com.aivle.conservation_backend.vca.repository.InspectionResultPotteryRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

/**
 * 육안조사 결과 저장/조회. AI 호출(POST /pottery-inspection)과는 분리돼있다 -
 * 그쪽은 여전히 저장 없이 분석만 하고, 화면에서 결과를 확인한 뒤 저장할지는
 * 별도로 이 엔드포인트를 호출해서 결정한다(Notion 문서에 합의된 경로 기준).
 *
 * X-ray(XrayJob)는 artifact_id에 직접 UNIQUE를 걸어 재실행 시 기존 행을
 * 덮어쓰지만, 육안조사는 이미 merge된 설계대로 AssessmentRun을 경유해서
 * run마다 결과가 쌓이는 이력 구조를 유지한다 - 이 차이는 의도적으로 그대로
 * 뒀다(README/PR 코멘트 참고). 두 파트를 통일할지는 팀 확인 필요.
 */
@RequiredArgsConstructor
@RestController
@RequestMapping("/api/vca/{artifactId}/runs/{assessmentRunId}/inspection-results/pottery")
public class InspectionResultPotteryController {

    private final AssessmentRunRepository assessmentRunRepository;
    private final ArtifactAccessService artifactAccessService;
    private final InspectionResultPotteryRepository inspectionResultPotteryRepository;
    private final PotteryInspectionJobService potteryInspectionJobService;

    @PostMapping
    @Transactional
    public ResponseEntity<InspectionResultPotteryResponseDto> save(
            @PathVariable UUID artifactId,
            @PathVariable UUID assessmentRunId,
            @RequestBody(required = false) PotteryInspectionResponseDto ignoredAiResult
    ) {
        // 레거시 저장 API도 FE가 보낸 결과를 직접 저장하지 않는다.
        // 서버에 검토 대기 상태로 보관된 pendingResult만 최종 확정해
        // 신규 /pottery-inspection/jobs/{id}/complete와 동일한 저장 규칙을 유지한다.
        potteryInspectionJobService.completeJob(artifactId, assessmentRunId);

        InspectionResultPottery saved = inspectionResultPotteryRepository
                .findByAssessmentRunId(assessmentRunId)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.CONFLICT,
                        "완료된 문양 기반 상태 조사 결과를 찾을 수 없습니다."
                ));

        return ResponseEntity.ok(InspectionResultPotteryResponseDto.from(saved));
    }

    @GetMapping
    public ResponseEntity<InspectionResultPotteryResponseDto> get(
            @PathVariable UUID artifactId,
            @PathVariable UUID assessmentRunId
    ) {
        // requireRun이 이미 assessmentRunId가 이 artifactId 소유임을 검증했으므로,
        // 아래 조회는 assessmentRunId 하나만으로 충분하다.
        requireRun(artifactId, assessmentRunId);

        return inspectionResultPotteryRepository
                .findByAssessmentRunId(assessmentRunId)
                .map(result -> ResponseEntity.ok(InspectionResultPotteryResponseDto.from(result)))
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    private AssessmentRun requireRun(UUID artifactId, UUID assessmentRunId) {
        artifactAccessService.requireArtifact(artifactId);
        return assessmentRunRepository.findByIdAndArtifactId(assessmentRunId, artifactId)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND, "VCA 실행을 찾을 수 없습니다: " + assessmentRunId));
    }
}