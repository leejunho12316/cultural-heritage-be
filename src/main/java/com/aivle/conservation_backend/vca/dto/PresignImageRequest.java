package com.aivle.conservation_backend.vca.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

public record PresignImageRequest(
        @NotBlank
        @Size(max = 255)
        String fileName,

        @NotBlank
        @Pattern(regexp = "^image/(jpeg|png|webp|tiff?)$")
        String contentType,

        @Positive
        long sizeBytes,

        @NotBlank
        @Pattern(regexp = "^[A-Fa-f0-9]{64}$")
        String sha256
) {
}
