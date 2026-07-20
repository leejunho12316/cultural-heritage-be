package com.aivle.conservation_backend.conservation_guide_ai.dto;

import java.util.List;
import java.util.Map;

//보존 가이드 AI 첫 실행 요청 DTO (Controller가 받는 입력 계약 - camelCase)

//record : final 데이터를 담는 클래스 빠르게 선언하기.
public record ConservationGuideAiStartRequestDto(
        String taskName,
        String taskManager,
        Map<String, Object> relicInfo,
        List<String> relicPhoto,
        List<String> flow
){

}
