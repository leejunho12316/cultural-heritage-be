package com.aivle.conservation_backend.xray_api.dto;

/**
 * X-ray 분석 대상 구분.
 *
 * AI 서비스는 대상에 따라 추론 해상도를 다르게 적용한다.
 * 결합 완료본은 원본이 5000px 이상으로 크기 때문에
 * 축소 시 이상영역이 사라지는 문제가 있어 2048을 사용한다.
 */
public enum AnalysisTarget {

    /** 결합 완료본. imgsz 2048 */
    ASSEMBLED("결합 완료본"),

    /** 원본 조각. imgsz 1280 */
    FRAGMENT("원본 조각");

    private final String value;

    AnalysisTarget(String value) {
        this.value = value;
    }

    /**
     * FastAPI가 기대하는 문자열 값.
     */
    public String getValue() {
        return value;
    }
}
