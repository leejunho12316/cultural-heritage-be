package com.aivle.conservation_backend.vca;

import java.time.Instant;
import java.util.List;

final class VcaDemoReportFactory {

    private VcaDemoReportFactory() {
    }

    static VcaResponses.Report create(
            String artifactId,
            String assessmentRunId,
            Instant generatedAt,
            List<VcaResponses.ReportImage> images
    ) {
        String primaryImageId = images.get(0).imageId();
        VcaResponses.ReportSummary summary = new VcaResponses.ReportSummary(
                "FAIR",
                "MEDIUM",
                "표면 안정성은 양호하나 국소 열화 관찰이 필요합니다.",
                "업로드된 가시광 이미지를 기준으로 표면 오염과 미세 균열 가능성을 검토한 MVP 데모 결과입니다."
        );
        List<VcaResponses.Finding> findings = List.of(
                new VcaResponses.Finding(
                        "finding-surface-01",
                        "SURFACE_DETERIORATION",
                        "MEDIUM",
                        "국소 표면 변색",
                        "우측 상단에서 주변부와 대비되는 변색 영역이 관찰됩니다.",
                        0.87,
                        primaryImageId
                ),
                new VcaResponses.Finding(
                        "finding-crack-01",
                        "CRACK_RISK",
                        "LOW",
                        "미세 균열 가능성",
                        "중앙부 선형 흔적은 확대 촬영과 전문가 확인이 필요합니다.",
                        0.72,
                        primaryImageId
                )
        );
        List<VcaResponses.Recommendation> recommendations = List.of(
                new VcaResponses.Recommendation(
                        "recommendation-monitor-01",
                        "HIGH",
                        "동일 조건 재촬영",
                        "조명과 촬영 거리를 고정해 30일 이내에 비교 이미지를 확보하세요."
                ),
                new VcaResponses.Recommendation(
                        "recommendation-environment-01",
                        "MEDIUM",
                        "보관 환경 점검",
                        "온습도 기록과 표면 오염원을 함께 확인하세요."
                )
        );
        return new VcaResponses.Report(
                assessmentRunId,
                artifactId,
                "COMPLETED",
                generatedAt,
                summary,
                findings,
                recommendations,
                List.copyOf(images)
        );
    }
}
