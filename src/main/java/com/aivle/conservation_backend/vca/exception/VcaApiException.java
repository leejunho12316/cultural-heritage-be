package com.aivle.conservation_backend.vca.exception;

import org.springframework.http.HttpStatus;

public final class VcaApiException extends RuntimeException {

    private final HttpStatus status;
    private final String code;

    public VcaApiException(HttpStatus status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }

    public HttpStatus status() {
        return status;
    }

    public String code() {
        return code;
    }
}
