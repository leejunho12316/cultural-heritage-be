package com.aivle.conservation_backend.vca.dto;

import java.time.Instant;

public record VcaCorpusPdfResponse(
        String fileName,
        String contentType,
        long sizeBytes,
        String sha256,
        Instant updatedAt
) {
}
