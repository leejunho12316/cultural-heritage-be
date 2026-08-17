package com.aivle.conservation_backend.vca.dto;

import java.util.List;

public record IntermediateResultsResponse(
        String artifactId,
        String assessmentRunId,
        String projectName,
        List<Stage> stages
) {

    public record Stage(
            String stage,
            String displayName,
            List<Item> items
    ) {
    }

    public record Item(
            String relativePath,
            String fileName,
            String contentType,
            long sizeBytes,
            String preview
    ) {
    }
}
