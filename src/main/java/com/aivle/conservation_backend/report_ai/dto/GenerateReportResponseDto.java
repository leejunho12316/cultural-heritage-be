package com.aivle.conservation_backend.report_ai.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.Map;

// report_json 자체는 8개 고정 섹션(title + fields 또는 title + body)으로 이뤄져
// 있어 구조가 자주 바뀔 수 있으므로, 우선 Map으로 느슨하게 받는다
// (PotteryInspectionResponseDto.detail과 같은 이유).
public record GenerateReportResponseDto(
        @JsonProperty("report_json") Map<String, Object> reportJson
) {
}
