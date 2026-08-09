package com.aivle.conservation_backend.xray_api.dto;

import java.util.List;

public record XrayAiFinalizationRequest(
        String jobId,
        String artifactId,
        List<RemoteInput> xrayInputs,
        String layoutFragmentMasksDownloadUrl,
        String finalLayoutDownloadUrl,
        OutputPutUrls outputPutUrls,
        String callbackUrl,
        String callbackToken
) {
    public record RemoteInput(
            String fileName,
            String downloadUrl
    ) {
    }

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
