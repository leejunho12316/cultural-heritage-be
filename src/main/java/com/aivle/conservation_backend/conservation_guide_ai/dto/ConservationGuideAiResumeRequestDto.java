package com.aivle.conservation_backend.conservation_guide_ai.dto;

import java.util.Map;

//보존 가이드 AI 첫 실행 후 재실행 DTO

public record ConservationGuideAiResumeRequestDto(
        Map<String, Object> resume
){ }
