package com.aivle.conservation_backend.xray_api.dto;

import java.util.List;
import java.util.Map;

public final class XrayStitchDtos {

    private XrayStitchDtos() {
    }

    public record PrepareRequest(
            String artifactId,
            String colorFileName,
            List<String> xrayFileNames
    ) {
    }

    /**
     * uploadHeaders는 Presigned PUT 요청에 반드시 함께 전송해야 하는 헤더다.
     * S3 Object metadata는 Lambda가 S3_FILE을 만들 때 사용한다.
     */
    public record UploadTarget(
            String fileName,
            String s3Key,
            String uploadUrl,
            Map<String, String> uploadHeaders
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

    public record SourceTarget(
            int sourceOrder,
            String fileName,
            String url
    ) {
    }

    public record SourceResponse(
            List<SourceTarget> sources
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