package com.aivle.conservation_backend.conservation_guide_ai.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;
import java.util.Map;

//보존 가이드 AI FastAPI로 실제로 나가는 요청 계약 (snake_case)
public record ConservationGuideAiStartApiRequestDto(
        @JsonProperty("task_name") String taskName,
        @JsonProperty("task_manager") String taskManager,
        @JsonProperty("relic_info") Map<String, Object> relicInfo,
        @JsonProperty("relic_photo") List<String> relicPhoto,
        List<String> flow
) {
}
