package com.aivle.conservation_backend.vca;

import org.springframework.http.HttpStatus;

final class VcaApiException extends RuntimeException {

    private final HttpStatus status;
    private final String code;

    VcaApiException(HttpStatus status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }

    HttpStatus status() {
        return status;
    }

    String code() {
        return code;
    }
}
