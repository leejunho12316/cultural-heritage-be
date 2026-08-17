package com.aivle.conservation_backend.vca.dto;

import java.time.Instant;

public record ImageResponse(
        String imageId,
        String fileName,
        String contentType,
        long sizeBytes,
        String status,
        String imageUrl,
        Instant createdAt,
        Instant uploadedAt
) {
}
