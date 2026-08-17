package com.aivle.conservation_backend.vca.dto;

import java.time.Instant;
import java.util.List;

// resumableRunId: 이어서 시작할 수 있는 직전 FAILED run의 id(없으면 null) - FE가
// 이 값이 있을 때만 "이어서 분석 시작"/"새로 분석 시작" 두 버튼으로 나눠 보여준다.
public record ArtifactDetailResponse(
        String artifactId,
        String displayName,
        String status,
        List<ImageResponse> uploadedImages,
        List<RunResponse> runs,
        String resumableRunId,
        Instant createdAt,
        Instant updatedAt
) {
}
