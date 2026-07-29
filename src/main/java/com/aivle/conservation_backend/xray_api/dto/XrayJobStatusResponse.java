package com.aivle.conservation_backend.xray_api.dto;

import java.util.List;

/**
 * 결합 작업 상태 응답. 프론트가 폴링하는 대상이다.
 *
 * status 는 PENDING → RUNNING → COMPLETED | FAILED 로 전이한다.
 * 프론트는 COMPLETED 이면 결과를 내려받고, FAILED 이면
 * errorMessage 를 표시한다.
 *
 * 상태의 진실은 Spring이 소유한다. AI 서비스는 상태를 갖지
 * 않으므로, 작업 이력과 검수 기록을 공식 기록으로 남기려면
 * 그 책임이 여기에 있어야 한다.
 */
public record XrayJobStatusResponse(
        String jobId,
        String artifactId,
        String status,
        String message,
        String errorMessage,

        /** 완료 시 결과 이미지 주소. 미완료면 null. */
        String resultUrl,

        /**
         * 조각별 배치 정보. transform 에 2x3 affine 행렬이 들어
         * 있어 원본 조각의 탐지 좌표를 결합본 좌표로 옮길 수 있다.
         */
        List<XrayStitchResponse.FragmentPlacement> fragments,

        /** 캔버스 크기, 배치 성공 조각 수 등. */
        XrayStitchResponse.StitchSummary summary
) {

    /** 진행 중 상태를 만들 때 쓰는 축약 생성자. */
    public static XrayJobStatusResponse of(
            String jobId,
            String artifactId,
            String status,
            String message
    ) {
        return new XrayJobStatusResponse(
                jobId, artifactId, status, message,
                null, null, null, null
        );
    }
}
