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

// VcaController에만 적용되는 예외 핸들러(assignableTypes로 범위 한정 - 다른 컨트롤러의
// 예외에는 관여하지 않는다). 모든 VCA 오류 응답을 ErrorEnvelopeResponse 형태로 통일한다.
@RestControllerAdvice(assignableTypes = VcaController.class)
public class VcaExceptionHandler {

    // VcaService/게이트웨이 등에서 의도적으로 던진 VcaApiException을 그대로 status/code/message로 변환.
    @ExceptionHandler(VcaApiException.class)
    public ResponseEntity<ErrorEnvelopeResponse> handleVcaApiException(
            VcaApiException exception
    ) {
        return error(exception.status(), exception.code(), exception.getMessage());
    }

    // @Valid 바인딩 실패(요청 DTO 검증 실패) 시 첫 번째 필드 오류만 뽑아 메시지로 사용한다.
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

    // 경로 변수 제약 위반이나 요청 본문 파싱 실패처럼 구체적인 필드 정보가 없는 400 오류를 처리.
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
