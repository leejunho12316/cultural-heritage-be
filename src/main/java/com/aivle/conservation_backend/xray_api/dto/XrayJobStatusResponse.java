package com.aivle.conservation_backend.xray_api.dto;

public record XrayJobStatusResponse(
        String jobId,
        String artifactId,
        String status,
        String message,
        String resultUrl,
        String errorMessage
) {
}
