package com.aivle.conservation_backend.xray_api.dto;

import java.util.List;

public record XrayAiStitchRequest(
        String jobId,
        String artifactId,
        String configName,
        RemoteInput colorInput,
        List<RemoteInput> xrayInputs,
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
            String assembled,
            String layout,
            String report,
            String layoutFragmentMasks
    ) {
    }
}
