package com.aivle.conservation_backend.xray_api.dto;

/**
 * 원본 X-ray 조각 결함분석 결과와 최종 결합본 결함분석 결과를
 * 동일한 최종 layout 좌표계에서 대응시키기 위한 요청 DTO.
 *
 * 기존 /detect/fragments, /detect/assembled 응답을 그대로 재사용한다.
 */
public record XrayDefectMappingRequest(
        XrayDetectionResponse fragmentDetection,
        XrayDetectionResponse assembledDetection
) {
}
