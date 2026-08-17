package com.aivle.conservation_backend.vca.dto;

import java.util.List;

public record ArtifactCollectionResponse(List<ArtifactSummary> items) {

    public record ArtifactSummary(
            String artifactId,
            String displayName,
            String thumbnailUrl,
            RunResponse latestRun,
            int runCount,
            java.time.Instant updatedAt
    ) {
    }
}
