package com.aivle.conservation_backend.xray_api.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import java.util.List;

/**
 * X-ray 조각 결합 응답.
 *
 * FastAPI /stitch/run 응답을 매핑한다.
 * AI 서비스가 필드를 추가해도 깨지지 않도록
 * 알 수 없는 속성은 무시한다.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record XrayStitchResponse(
        boolean success,

        /** 결합본 PNG의 base64 문자열. */
        String compositeImage,

        String compositeFormat,

        List<FragmentPlacement> fragments,

        StitchSummary summary
) {

    /**
     * 조각 하나의 배치 정보.
     *
     * transform은 2x3 affine 행렬이다.
     *
     *     [[a, b, tx],
     *      [c, d, ty]]
     *
     * 원본 조각 좌표 (x, y)의 결합본 좌표는
     *
     *     X = a*x + b*y + tx
     *     Y = c*x + d*y + ty
     *
     * 주의: matched가 false면 transform이 null이다.
     * 엔진이 그 조각의 위치를 찾지 못했다는 뜻이며,
     * 결합본에 반영되지 않았다.
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record FragmentPlacement(
            String fileName,
            List<List<Double>> transform,
            Boolean matched,
            List<Double> cropBBoxXYWH,
            Integer subfragmentIndex,
            Double rotationDeg
    ) {
    }

    /**
     * 결합 처리 요약.
     *
     * canvasWidth/Height는 항상 원본 해상도다.
     * previewScale이 1.0 미만이면 compositeImage가
     * 축소되어 반환된 것이므로, 화면에 좌표를 겹칠 때
     * 이 계수를 곱해야 한다.
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record StitchSummary(
            String artifactId,
            Integer totalFragments,
            Integer matchedCount,
            Integer canvasWidth,
            Integer canvasHeight,
            Double previewScale
    ) {
    }
}
