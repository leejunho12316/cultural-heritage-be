package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.vca.dto.ReportResponse;
import java.time.Instant;
import java.util.List;

final class VcaDemoReportFactory {

    private VcaDemoReportFactory() {
    }

    static ReportResponse create(
            String artifactId,
            String assessmentRunId,
            Instant generatedAt,
            List<ReportResponse.Image> images
    ) {
        String primaryImageId = images.get(0).imageId();
        ReportResponse.Summary summary = new ReportResponse.Summary(
                "FAIR",
                "MEDIUM",
                "VCA 육안 조사 결과",
                "업로드된 가시광 이미지를 기준으로 표면 오염과 미세 균열 가능성을 검토한 MVP 데모 결과입니다."
        );
        List<ReportResponse.Finding> findings = List.of(
                new ReportResponse.Finding(
                        "finding-surface-01",
                        "SURFACE_DETERIORATION",
                        "MEDIUM",
                        "국소 표면 변색",
                        "우측 상단에서 주변부와 대비되는 변색 영역이 관찰됩니다.",
                        0.87,
                        primaryImageId
                ),
                new ReportResponse.Finding(
                        "finding-crack-01",
                        "CRACK_RISK",
                        "LOW",
                        "미세 균열 가능성",
                        "중앙부 선형 흔적은 확대 촬영과 전문가 확인이 필요합니다.",
                        0.72,
                        primaryImageId
                )
        );
        List<ReportResponse.Recommendation> recommendations = List.of(
                new ReportResponse.Recommendation(
                        "recommendation-monitor-01",
                        "HIGH",
                        "동일 조건 재촬영",
                        "조명과 촬영 거리를 고정해 30일 이내에 비교 이미지를 확보하세요."
                ),
                new ReportResponse.Recommendation(
                        "recommendation-environment-01",
                        "MEDIUM",
                        "보관 환경 점검",
                        "온습도 기록과 표면 오염원을 함께 확인하세요."
                )
        );
        return new ReportResponse(
                assessmentRunId,
                artifactId,
                "COMPLETED",
                generatedAt,
                summary,
                findings,
                recommendations,
                List.copyOf(images),
                null,
                null
        );
    }
}
