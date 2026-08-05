package com.aivle.conservation_backend.vca.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

public record CompleteImageRequest(
        @NotBlank
        @Pattern(regexp = "^[A-Fa-f0-9]{64}$")
        String sha256
) {
}
