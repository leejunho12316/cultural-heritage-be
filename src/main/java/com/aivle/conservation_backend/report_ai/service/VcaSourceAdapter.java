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

        for (AssessmentRun run : runs) {
            Optional<AssessmentReport> report = assessmentReportRepository.findById(run.getId());
            if (report.isPresent() && report.get().getReportJson() != null) {
                return Optional.of(toMap(report.get().getReportJson()));
            }
        }
        return Optional.empty();
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
