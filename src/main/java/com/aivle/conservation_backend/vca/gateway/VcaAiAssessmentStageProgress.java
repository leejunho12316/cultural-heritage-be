package com.aivle.conservation_backend.vca.gateway;

// 지금 실행 중인 스테이지 안에서 처리한 입력 단위 개수(completed/total).
// vca-ai가 무거운 스테이지(rough_masking, mask_refining)에서만 채워 보내고,
// 그 외 스테이지는 null이다.
public record VcaAiAssessmentStageProgress(
        Integer completed,
        Integer total
) {
}
