package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.vca.dto.ReportResponse;
import java.time.Instant;
import java.util.List;

// vca-ai 게이트웨이가 설정되지 않은 데모/로컬 모드에서 사용할 고정된 가짜 리포트를 만든다.
// VcaService의 completeRunReservation/ensureReportReady에서, 실제 AI 응답 대신 사용된다.
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
                "VCA 육안 조사 결과",
                "업로드된 가시광 이미지를 기준으로 표면 오염과 미세 균열 가능성을 검토한 MVP 데모 결과입니다.",
                null,
                null
        );
        List<ReportResponse.Finding> findings = List.of(
                new ReportResponse.Finding(
                        "finding-surface-01",
                        "SURFACE_DETERIORATION",
                        "MEDIUM",
                        "우측 상단에서 주변부와 대비되는 변색 영역이 관찰됩니다.",
                        "surface_deterioration",
                        "국소 표면 변색",
                        primaryImageId,
                        List.of(),
                        new ReportResponse.Bbox(220.0, 40.0, 360.0, 150.0),
                        null
                ),
                new ReportResponse.Finding(
                        "finding-crack-01",
                        "CRACK_RISK",
                        "LOW",
                        "중앙부 선형 흔적은 확대 촬영과 전문가 확인이 필요합니다.",
                        "crack_risk",
                        "미세 균열 가능성",
                        primaryImageId,
                        List.of(),
                        new ReportResponse.Bbox(150.0, 200.0, 280.0, 260.0),
                        null
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
                null,
                null
        );
    }
}
