package com.aivle.conservation_backend.report_ai.service;

import com.aivle.conservation_backend.vca.domain.AssessmentReport;
import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import com.aivle.conservation_backend.vca.dto.ReportResponse;
import com.aivle.conservation_backend.vca.repository.AssessmentReportRepository;
import com.aivle.conservation_backend.vca.repository.AssessmentRunRepository;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * VCA v2의 최신 저장 보고서(assessment_report)를 최종 report-ai 입력으로 변환한다.
 * Pottery와 VCA는 같은 assessment_run 테이블을 쓰지만 run_type이 다르므로
 * VCA run만 골라 최신순으로 확인한다.
 */
@Service
public class VcaSourceAdapter {

    private final AssessmentRunRepository assessmentRunRepository;
    private final AssessmentReportRepository assessmentReportRepository;

    public VcaSourceAdapter(
            AssessmentRunRepository assessmentRunRepository,
            AssessmentReportRepository assessmentReportRepository
    ) {
        this.assessmentRunRepository = assessmentRunRepository;
        this.assessmentReportRepository = assessmentReportRepository;
    }

    public Optional<Map<String, Object>> resolve(String artifactId) {
        UUID uuid;
        try {
            uuid = UUID.fromString(artifactId);
        } catch (IllegalArgumentException error) {
            return Optional.empty();
        }

        List<AssessmentRun> runs = assessmentRunRepository
                .findAllByArtifactIdAndRunTypeOrderByRunNumberDesc(
                        uuid,
                        AssessmentRun.RUN_TYPE_VCA
                );

        if (runs.isEmpty()) {
            return Optional.empty();
        }

        // 최신 VCA run을 사용자가 "결과 없이 완료"로 확정했다면 과거 성공
        // report를 대신 끌어오지 않는다. 이번 조사 결과는 의도적으로 없음이
        // 맞으므로 최종보고서에도 VCA 섹션을 비워둔다.
        AssessmentRun latestRun = runs.get(0);
        if (latestRun.isWorkflowCompletedWithoutResult()) {
            return Optional.empty();
        }

        // 최종보고서는 "가장 최근 VCA 실행"만 근거로 삼는다. 최신 run이
        // 실패했는데 과거 성공 report를 자동 재사용하면 사용자가 방금 실패한
        // 조사를 결과 없음으로 처리하려는 의도와 어긋날 수 있다.
        return assessmentReportRepository.findById(latestRun.getId())
                .map(AssessmentReport::getReportJson)
                .filter(report -> report != null)
                .map(this::toMap);
    }

    private Map<String, Object> toMap(ReportResponse report) {
        Map<String, Object> result = new LinkedHashMap<>();

        if (report.summary() != null) {
            Map<String, Object> summary = new LinkedHashMap<>();
            summary.put("headline", report.summary().headline());
            summary.put("description", report.summary().description());
            summary.put("overall_condition", report.summary().overallCondition());
            summary.put("risk_level", report.summary().riskLevel());
            result.put("summary", summary);
        }

        result.put("findings", report.findings() == null ? List.of() : report.findings().stream()
                .map(finding -> {
                    Map<String, Object> value = new LinkedHashMap<>();
                    value.put("category", finding.category());
                    value.put("severity", finding.severity());
                    value.put("description", finding.description());
                    value.put("concept_family", finding.conceptFamily());
                    value.put("descriptor", finding.descriptor());
                    return value;
                })
                .toList());

        result.put("recommendations", report.recommendations() == null ? List.of() : report.recommendations().stream()
                .map(recommendation -> {
                    Map<String, Object> value = new LinkedHashMap<>();
                    value.put("priority", recommendation.priority());
                    value.put("title", recommendation.title());
                    value.put("description", recommendation.description());
                    return value;
                })
                .toList());

        return result;
    }
}
