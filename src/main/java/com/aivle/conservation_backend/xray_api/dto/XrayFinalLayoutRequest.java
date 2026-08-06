package com.aivle.conservation_backend.xray_api.dto;

import java.util.List;

/**
 * Konva에서 사용자가 최종 확정한 조각별 배치 정보.
 *
 * 자동 결합 엔진이 생성한 layout.json 자체를 덮어쓰지 않고,
 * 이 요청의 이동/회전 값을 기존 layout에 반영하여
 * layout.final.json을 별도로 생성한다.
 */
public record XrayFinalLayoutRequest(
        List<FragmentTransform> fragments
) {
    public record FragmentTransform(
            int index,
            int originalSourceIndex,
            String originalSourceName,
            int subfragmentIndex,
            double centerX,
            double centerY,
            double rotationDeg
    ) {
    }
}
