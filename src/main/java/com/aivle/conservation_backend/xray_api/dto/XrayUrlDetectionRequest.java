package com.aivle.conservation_backend.xray_api.dto;

import java.util.List;

public final class XrayUrlDetectionRequest {
    private XrayUrlDetectionRequest() {
    }

    public record Single(
            String fileName,
            String downloadUrl,
            String analysisTarget,
            Double confidence,
            Integer sourceIndex
    ) {
    }

    public record Batch(
            List<Single> files,
            String analysisTarget,
            Double confidence
    ) {
    }
}
