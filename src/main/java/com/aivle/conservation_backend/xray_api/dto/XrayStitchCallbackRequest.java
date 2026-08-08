package com.aivle.conservation_backend.xray_api.dto;

public record XrayStitchCallbackRequest(
        String jobId,
        String artifactId,
        String status,
        String message,
        String errorMessage
) {
}
