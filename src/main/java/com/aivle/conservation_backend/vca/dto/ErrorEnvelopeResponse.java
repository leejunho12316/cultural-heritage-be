package com.aivle.conservation_backend.vca.dto;

public record ErrorEnvelopeResponse(ErrorDetail error) {

    public record ErrorDetail(String code, String message) {
    }
}
