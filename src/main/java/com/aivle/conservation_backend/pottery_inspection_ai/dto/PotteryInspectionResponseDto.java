package com.aivle.conservation_backend.pottery_inspection_ai.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.Map;

// pottery_api.py의 POST /inspect 응답을 그대로 매핑한다.
// AI 쪽 JSON 키가 snake_case라서 자동 매핑을 믿지 않고 명시적으로 지정한다.
// detail은 문양/시대/유약 등 상세 정보를 담은 복잡한 중첩 구조라서,
// 아직 자주 바뀔 수 있으므로 우선 Map으로 느슨하게 받는다.
public record PotteryInspectionResponseDto(
        @JsonProperty("module_version") String moduleVersion,
        @JsonProperty("inspection_text") String inspectionText,
        @JsonProperty("summary") String summary,
        @JsonProperty("human_review_recommended") boolean humanReviewRecommended,
        @JsonProperty("detail") Map<String, Object> detail
) {
}
