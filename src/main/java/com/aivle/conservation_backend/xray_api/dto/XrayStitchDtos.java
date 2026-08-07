package com.aivle.conservation_backend.xray_api.dto;

import java.util.List;

public final class XrayStitchDtos {

    private XrayStitchDtos() {
    }

    public record PrepareRequest(
            String artifactId,
            String colorFileName,
            List<String> xrayFileNames
    ) {
    }

    public record UploadTarget(
            String fileName,
            String s3Key,
            String uploadUrl
    ) {
    }

    public record PrepareResponse(
            String jobId,
            String artifactId,
            String status,
            UploadTarget color,
            List<UploadTarget> xrays
    ) {
    }

    public record StartRequest(
            String colorFileName,
            List<String> xrayFileNames
    ) {
    }

    public record UrlResponse(
            String url,
            String s3Key
    ) {
    }

    public record ReconcileResponse(
            String jobId,
            String previousStatus,
            String status,
            boolean changed,
            String message
    ) {
    }
}
