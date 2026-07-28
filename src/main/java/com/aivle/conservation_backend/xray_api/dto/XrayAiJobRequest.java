package com.aivle.conservation_backend.xray_api.dto;

import jakarta.validation.constraints.NotBlank;

public record XrayAiJobRequest(

        @NotBlank
        String jobId,

        @NotBlank
        String artifactId,

        @NotBlank
        String colorDirectory,

        @NotBlank
        String xrayDirectory,

        @NotBlank
        String outputDirectory,

        @NotBlank
        String configName

) {
}