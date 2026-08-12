package com.aivle.conservation_backend.conservation_guide_ai.dto;

import java.util.List;
import java.util.Map;

//보존 가이드 AI 첫 실행 요청 DTO (Controller가 받는 입력 계약 - camelCase)

//record : final 데이터를 담는 클래스 빠르게 선언하기.
public record ConservationGuideAiStartRequestDto(
        // 이 작업이 어느 유물에 대한 것인지 (Artifact.id, UUID 문자열).
        // FE가 새로고침 후에도 진행상황을 복구할 수 있도록 tasks.artifact_id에
        // 저장해둔다. 없거나 유효하지 않아도(구버전 FE 등) 작업 시작 자체는
        // 막지 않는다 - TaskService에서 best-effort로만 채운다.
        String artifactId,
        String taskName,
        String taskManager,
        Map<String, Object> relicInfo,
        List<String> relicPhoto,
        List<String> flow
){

}
