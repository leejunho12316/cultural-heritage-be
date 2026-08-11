package com.aivle.conservation_backend.pottery_inspection_ai.dto;

import com.aivle.conservation_backend.pottery_inspection_ai.domain.InspectionResultPottery;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

public record InspectionResultPotteryResponseDto(
        UUID id,
        UUID assessmentRunId,
        String inspectionText,
        boolean humanReviewRecommended,
        Map<String, Object> detail,
        Instant createdAt
) {
    public static InspectionResultPotteryResponseDto from(InspectionResultPottery result) {
        return new InspectionResultPotteryResponseDto(
                result.getId(),
                result.getAssessmentRun().getId(),
                result.getInspectionText(),
                result.isHumanReviewRecommended(),
                result.getDetail(),
                result.getCreatedAt()
        );
    }
}