package com.aivle.conservation_backend.vca.exception;

import com.aivle.conservation_backend.vca.controller.VcaController;
import com.aivle.conservation_backend.vca.dto.ErrorEnvelopeResponse;
import jakarta.validation.ConstraintViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice(assignableTypes = VcaController.class)
public class VcaExceptionHandler {

    @ExceptionHandler(VcaApiException.class)
    public ResponseEntity<ErrorEnvelopeResponse> handleVcaApiException(
            VcaApiException exception
    ) {
        return error(exception.status(), exception.code(), exception.getMessage());
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ErrorEnvelopeResponse> handleValidation(
            MethodArgumentNotValidException exception
    ) {
        String message = exception.getBindingResult().getFieldErrors().stream()
                .findFirst()
                .map(error -> error.getField() + ": " + error.getDefaultMessage())
                .orElse("Request validation failed.");
        return error(HttpStatus.BAD_REQUEST, "VALIDATION_ERROR", message);
    }

    @ExceptionHandler({
            ConstraintViolationException.class,
            HttpMessageNotReadableException.class
    })
    public ResponseEntity<ErrorEnvelopeResponse> handleMalformedRequest(
            Exception exception
    ) {
        return error(
                HttpStatus.BAD_REQUEST,
                "VALIDATION_ERROR",
                "The request body or parameter is invalid."
        );
    }

    private ResponseEntity<ErrorEnvelopeResponse> error(
            HttpStatus status,
            String code,
            String message
    ) {
        ErrorEnvelopeResponse.ErrorDetail detail = new ErrorEnvelopeResponse.ErrorDetail(code, message);
        return ResponseEntity.status(status).body(new ErrorEnvelopeResponse(detail));
    }
}
