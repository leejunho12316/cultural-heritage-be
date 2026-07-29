package com.aivle.conservation_backend.xray_api.dto;

/**
 * 결합 작업 생성 응답.
 *
 * 결합은 수 분에서 수십 분이 걸리므로 결과를 기다리지 않고
 * jobId를 먼저 돌려준다. 프론트는 이 jobId로 상태를 폴링한다.
 */
public record XrayJobResponse(
        String jobId,
        String artifactId,
        String status,
        String message,
        int colorFileCount,
        int xrayFileCount
) {
}
