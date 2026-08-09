package com.aivle.conservation_backend.vca.domain;

import com.aivle.conservation_backend.vca.dto.ReportResponse;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.util.UUID;

// FE/API에 반환하는 report의 영속 상태. 팀 공유 ERD의 `assessment_report` 테이블 (run당 1행).
// overallCondition/riskLevel은 아직 구현된 위험도 분류 로직이 없어 항상 null이다.
@Entity
@Table(name = "assessment_report")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class VcaAssessmentReportEntity {

    @Id
    @Column(name = "assessment_run_id")
    private UUID assessmentRunId;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "report_json", columnDefinition = "jsonb")
    private ReportResponse reportJson;

    @Column(name = "status")
    private String status;

    @Column(name = "overall_condition")
    private String overallCondition;

    @Column(name = "risk_level")
    private String riskLevel;

    @Column(name = "generated_at")
    private Instant generatedAt;

    @Column(name = "created_at")
    private Instant createdAt;

    @Column(name = "updated_at")
    private Instant updatedAt;
}
