package com.aivle.conservation_backend.conservation_guide_ai;
import java.util.Map;

//보존 가이드 AI 응답 받는 DTO

public record ConservationGuideAiResponse (
        String status,
        Map<String, Object> interrupt,
        Map<String, Object> result
) {
}
