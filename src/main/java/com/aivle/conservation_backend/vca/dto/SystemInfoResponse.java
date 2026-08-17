package com.aivle.conservation_backend.vca.dto;

import java.util.List;
import java.util.Map;

// 조사 보고서 하단 "시스템 환경 정보" 표기용. VcaController의 별도 조회
// 엔드포인트가 내려주며, 특정 run이 아니라 vca-ai 엔진이 지금 도는 환경
// 자체를 설명한다(CPU/GPU, OS, 라이브러리·모델 버전).
public record SystemInfoResponse(
        String os,
        String pythonVersion,
        String device,
        Map<String, String> libraries,
        List<Model> models
) {

    public record Model(String key, String repoId, String revision) {
    }
}
