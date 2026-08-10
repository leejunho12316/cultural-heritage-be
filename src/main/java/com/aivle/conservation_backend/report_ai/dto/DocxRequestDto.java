package com.aivle.conservation_backend.report_ai.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;
import java.util.Map;

// 이미 생성된 report_json(ASSESSMENT_REPORT에 저장된 값)을 .docx로 변환만
// 할 때 쓴다 - LLM 재호출이 없어 /reports/generate/docx보다 훨씬 빠르다.
public record DocxRequestDto(
        @JsonProperty("artifact_id") String artifactId,
        @JsonProperty("report_json") Map<String, Object> reportJson,
        Map<String, List<PhotoAttachmentDto>> photos
) {
}
