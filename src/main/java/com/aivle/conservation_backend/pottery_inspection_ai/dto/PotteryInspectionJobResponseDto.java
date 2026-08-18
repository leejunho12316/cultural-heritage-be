package com.aivle.conservation_backend.pottery_inspection_ai.dto;

import java.util.UUID;

/**
 * FE가 육안조사 페이지에 재진입할 때 필요한 서버 영속 상태를 한 번에 반환한다.
 * assessmentRunId가 브라우저 저장소 대신 작업 식별자가 된다.
 */
public record PotteryInspectionJobResponseDto(
        UUID assessmentRunId,
        UUID artifactId,
        String aiJobId,
        String status,
        String currentStage,
        int progressPercent,
        String photoUrl,
        String annotatedPhotoUrl,
        PotteryInspectionResponseDto result,
        Integer errorStatus,
        Object errorDetail
) {
}
