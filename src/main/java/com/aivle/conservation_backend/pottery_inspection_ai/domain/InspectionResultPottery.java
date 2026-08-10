package com.aivle.conservation_backend.pottery_inspection_ai.domain;

import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

@Entity
@Table(name = "inspection_result_pottery")
public class InspectionResultPottery {

    @Id
    @Column(name = "id", nullable = false, updatable = false)
    private UUID id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "assessment_run_id", nullable = false)
    private AssessmentRun assessmentRun;

    @Column(name = "inspection_text", nullable = false, columnDefinition = "text")
    private String inspectionText;

    @Column(name = "human_review_recommended", nullable = false)
    private boolean humanReviewRecommended;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "detail", nullable = false, columnDefinition = "jsonb")
    private Map<String, Object> detail;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected InspectionResultPottery() {
    }

    public static InspectionResultPottery create(
            UUID id,
            AssessmentRun assessmentRun,
            String inspectionText,
            boolean humanReviewRecommended,
            Map<String, Object> detail
    ) {
        InspectionResultPottery result = new InspectionResultPottery();
        result.id = id;
        result.assessmentRun = assessmentRun;
        result.inspectionText = inspectionText;
        result.humanReviewRecommended = humanReviewRecommended;
        result.detail = new LinkedHashMap<>(detail);
        result.createdAt = Instant.now();
        return result;
    }

    public UUID getId() {
        return id;
    }

    public AssessmentRun getAssessmentRun() {
        return assessmentRun;
    }

    public String getInspectionText() {
        return inspectionText;
    }

    public boolean isHumanReviewRecommended() {
        return humanReviewRecommended;
    }

    public Map<String, Object> getDetail() {
        return detail;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
