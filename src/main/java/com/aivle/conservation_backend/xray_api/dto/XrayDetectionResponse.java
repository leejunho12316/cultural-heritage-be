package com.aivle.conservation_backend.xray_api.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import java.util.List;

/**
 * X-ray AI 서비스 탐지 응답.
 *
 * FastAPI /detect 및 /detect-batch 응답을 매핑한다.
 * AI 서비스가 필드를 추가해도 깨지지 않도록
 * 알 수 없는 속성은 무시한다.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record XrayDetectionResponse(
        boolean success,
        List<AnomalyRegion> regions,
        DetectionSummary summary,

        // 일괄 탐지 전용
        Integer totalRegionCount,
        List<DetectionSummary> summaries
) {

    /**
     * 탐지된 이상영역 하나.
     *
     * 주의: confidence는 AI 탐지 점수이며
     * 실제 손상 확률이나 심각도가 아니다.
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record AnomalyRegion(
            String regionId,
            String analysisTarget,
            String fileName,
            String className,
            Double confidence,
            String position,
            Double areaRatioPercent,
            BoundingBox bbox,
            Point center
    ) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record BoundingBox(
            Double x1,
            Double y1,
            Double x2,
            Double y2
    ) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Point(
            Double x,
            Double y
    ) {
    }

    /**
     * 이미지 한 장의 처리 요약.
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record DetectionSummary(
            String fileName,
            String analysisTarget,
            Integer imageWidth,
            Integer imageHeight,
            Integer inferenceImgsz,
            Double confidenceThreshold,
            Integer regionCount,
            String error
    ) {
    }
}
