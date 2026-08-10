package com.aivle.conservation_backend.vca.dto;

// resume: true면 같은 artifact의 직전 FAILED run에서 이어서 시작한다(이미지 구성이
// 그 run과 정확히 같을 때만 실제로 적용됨) - false/미지정이면 항상 처음부터 새로 시작한다.
public record CreateRunRequest(String material, Boolean resume) {
}
