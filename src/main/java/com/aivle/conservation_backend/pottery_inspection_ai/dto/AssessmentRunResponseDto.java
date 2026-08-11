package com.aivle.conservation_backend.vca.dto;

import com.aivle.conservation_backend.vca.domain.AssessmentRun;

import java.util.UUID;

public record AssessmentRunResponseDto(
        UUID id,
        UUID artifactId,
        int runNumber,
        String status
) {
    public static AssessmentRunResponseDto from(AssessmentRun run) {
        return new AssessmentRunResponseDto(
                run.getId(),
                run.getArtifactId(),
                run.getRunNumber(),
                run.getStatus()
        );
    }
}