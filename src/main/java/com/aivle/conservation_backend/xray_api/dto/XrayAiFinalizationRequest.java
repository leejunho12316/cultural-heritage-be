package com.aivle.conservation_backend.xray_api.dto;

public record XrayAiFinalizationRequest(
        String jobId,
        String artifactId,
        String bundleDownloadUrl,
        String finalLayoutDownloadUrl,
        OutputPutUrls outputPutUrls,
        String callbackUrl,
        String callbackToken
) {
    public record OutputPutUrls(
            String assembledFinal,
            String sourceOwner,
            String fragmentOwner,
            String seamZone,
            String overlapMask,
            String provenance
    ) {
    }
}
