package com.aivle.conservation_backend.vca.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.MapsId;
import jakarta.persistence.OneToOne;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

@Entity
@Table(name = "assessment_report")
public class AssessmentReport {

    @Id
    @Column(name = "assessment_run_id", nullable = false, updatable = false)
    private UUID assessmentRunId;

    @MapsId
    @OneToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "assessment_run_id", nullable = false)
    private AssessmentRun assessmentRun;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "report_json", nullable = false, columnDefinition = "jsonb")
    private Map<String, Object> reportJson;

    @Column(name = "status", nullable = false, columnDefinition = "text")
    private String status;

    @Column(name = "overall_condition", columnDefinition = "text")
    private String overallCondition;

    @Column(name = "risk_level", columnDefinition = "text")
    private String riskLevel;

    @Column(name = "generated_at")
    private Instant generatedAt;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected AssessmentReport() {
    }

    public static AssessmentReport create(
            AssessmentRun run,
            Map<String, Object> reportJson,
            String status,
            String overallCondition,
            String riskLevel,
            Instant generatedAt
    ) {
        AssessmentReport report = new AssessmentReport();
        Instant now = Instant.now();
        report.assessmentRun = run;
        report.assessmentRunId = run.getId();
        report.reportJson = new LinkedHashMap<>(reportJson);
        report.status = status;
        report.overallCondition = overallCondition;
        report.riskLevel = riskLevel;
        report.generatedAt = generatedAt;
        report.createdAt = now;
        report.updatedAt = now;
        return report;
    }

    public UUID getAssessmentRunId() {
        return assessmentRunId;
    }

    public AssessmentRun getAssessmentRun() {
        return assessmentRun;
    }

    public Map<String, Object> getReportJson() {
        return reportJson;
    }

    public String getStatus() {
        return status;
    }

    public String getOverallCondition() {
        return overallCondition;
    }

    public String getRiskLevel() {
        return riskLevel;
    }

    public Instant getGeneratedAt() {
        return generatedAt;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }

    public void update(
            Map<String, Object> reportJson,
            String status,
            String overallCondition,
            String riskLevel,
            Instant generatedAt
    ) {
        this.reportJson = new LinkedHashMap<>(reportJson);
        this.status = status;
        this.overallCondition = overallCondition;
        this.riskLevel = riskLevel;
        this.generatedAt = generatedAt;
        this.updatedAt = Instant.now();
    }
}
