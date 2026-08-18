package com.aivle.conservation_backend.artifact.dto;

import java.util.UUID;

public record ArtifactWorkflowStatusResponse(
        UUID artifactId,
        String guide,
        String xray,
        String visual,
        boolean finalReportExists,
        boolean allCompleted
) {
}
