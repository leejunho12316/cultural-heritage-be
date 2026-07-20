package com.aivle.conservation_backend.conservation_guide_ai.dto;
import java.util.Map;

//보존 가이드 AI 응답 받는 DTO

public record ConservationGuideAiResponseDto(
        String status,
        Map<String, Object> interrupt,
        Map<String, Object> result
) { }
