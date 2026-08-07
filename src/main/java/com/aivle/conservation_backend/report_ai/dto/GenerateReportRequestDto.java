package com.aivle.conservation_backend.report_ai.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;
import java.util.Map;

/**
 * report-ai POST /reports/generate, /reports/generate/docx 요청 바디.
 *
 * report-ai 자체는 GUIDE_TASK/XRAY_JOB/INSPECTION_RESULT_POTTERY를 직접
 * 조회하지 않는다 - 호출자(Spring)가 각 파트 API 결과를 모아서 채워 보낸다.
 * 아직 이 세 파트의 실제 DB 저장이 없어서, 지금은 호출부에서 값을 직접
 * 채우거나 비워서 테스트하는 용도로 쓴다.
 */
public record GenerateReportRequestDto(
        @JsonProperty("artifact_id") String artifactId,
        @JsonProperty("relic_info") Map<String, Object> relicInfo,
        @JsonProperty("guide_result") Map<String, Object> guideResult,   // GUIDE_TASK.result
        @JsonProperty("xray_report_text") String xrayReportText,        // XRAY_JOB.report_text
        @JsonProperty("xray_regions") List<Map<String, Object>> xrayRegions,  // XRAY_REGION 행
        @JsonProperty("pottery_inspection") Map<String, Object> potteryInspection,  // INSPECTION_RESULT_POTTERY
        // key: header/pre_investigation/disassembly/cleaning/reinforcement/bonding/restoration/conclusion
        Map<String, List<PhotoAttachmentDto>> photos
) {
}
