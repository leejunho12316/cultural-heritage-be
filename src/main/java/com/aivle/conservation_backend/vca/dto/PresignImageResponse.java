package com.aivle.conservation_backend.vca.dto;

import java.time.Instant;
import java.util.Map;

public record PresignImageResponse(
        String imageId,
        String status,
        String uploadMode,
        String uploadUrl,
        String method,
        Map<String, String> requiredHeaders,
        Instant expiresAt
) {
}
