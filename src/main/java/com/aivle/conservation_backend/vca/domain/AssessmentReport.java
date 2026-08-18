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
public class AssessmentReport {

    @Id
    @Column(name = "assessment_run_id")
    private UUID assessmentRunId;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "report_json", nullable = false, columnDefinition = "jsonb")
    private ReportResponse reportJson;

    @Column(name = "status", nullable = false)
    private String status;

    // 기본 varchar(255)로 만들어졌던 컬럼이라(짧은 위험도 분류용으로
    // 설계됐던 자리) LLM이 생성하는 문장 수 제한 없는 전체 문단을 못
    // 담았다 - db/assessment_report_overall_condition_text_migration.sql로
    // text로 넓힘.
    @Column(name = "overall_condition", columnDefinition = "text")
    private String overallCondition;

    @Column(name = "risk_level")
    private String riskLevel;

    @Column(name = "generated_at")
    private Instant generatedAt;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;
}
