package com.aivle.conservation_backend.xray_api.dto;

public record XrayJobResponse(
        String jobId,
        String artifactId,
        String status,
        String message
) {
}