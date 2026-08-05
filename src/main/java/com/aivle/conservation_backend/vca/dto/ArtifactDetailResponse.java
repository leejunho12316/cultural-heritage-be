package com.aivle.conservation_backend.vca.dto;

import java.time.Instant;
import java.util.List;

public record ArtifactDetailResponse(
        String artifactId,
        String displayName,
        String status,
        List<ImageResponse> uploadedImages,
        List<RunResponse> runs,
        Instant createdAt,
        Instant updatedAt
) {
}
