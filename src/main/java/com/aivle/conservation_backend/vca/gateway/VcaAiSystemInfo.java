package com.aivle.conservation_backend.vca.gateway;

import java.util.List;
import java.util.Map;

// 리포트 하단 "시스템 환경 정보" 표기용 - vca-ai GET /system-info를 그대로 옮긴 값이다.
// 분석 결과에 영향을 주지 않는 표시 전용 정보라 findings처럼 엄격히 검증하지 않는다.
public record VcaAiSystemInfo(
        String os,
        String pythonVersion,
        String device,
        Map<String, String> libraries,
        List<Model> models
) {

    public record Model(String key, String repoId, String revision) {
    }
}
