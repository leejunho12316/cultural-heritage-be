package com.aivle.conservation_backend.xray_api.dto;

/**
 * Deprecated compatibility type. New code uses {@link XrayAiStitchRequest}.
 */
@Deprecated
public record XrayAiJobRequest(
        String jobId,
        String artifactId,
        String colorDirectory,
        String xrayDirectory,
        String outputDirectory,
        String configName
) {
}
