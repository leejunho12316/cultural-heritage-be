package com.aivle.conservation_backend.vca.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

// 도자기 검사 "결과"의 영속 상태 - assessment_run 하나당 완료된 결과 1건.
// 진행/실패/재시도 같은 워크플로우 상태(pottery_inspection_status_json)는
// assessment_run 쪽에 그대로 남아있다 - 그건 run 자신의 status/failure_reason과
// 같은 성격의 process metadata라 결과 테이블과 분리해서 유지하기로 했다.
@Entity
@Table(
        name = "inspection_result_pottery",
        uniqueConstraints = @UniqueConstraint(columnNames = {"assessment_run_id"})
)
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class InspectionResultPottery {

    @Id
    @Column(name = "id")
    private UUID id;

    @Column(name = "assessment_run_id", nullable = false, unique = true)
    private UUID assessmentRunId;

    @Column(name = "inspection_text", columnDefinition = "text")
    private String inspectionText;

    @Column(name = "human_review_recommended", nullable = false)
    private boolean humanReviewRecommended;

    // moduleVersion/summary는 별도 컬럼 없이 이 jsonb 안에 예약 키로 같이 담는다
    // (VcaService.DETAIL_MODULE_VERSION_KEY/DETAIL_SUMMARY_KEY) - 컬럼을 추가하지
    // 않기로 한 결정 때문. 실제 분석 근거(시대/문양 등)와는 저장 시점에 합쳐지고
    // 읽을 때 다시 분리된다.
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "detail", columnDefinition = "jsonb")
    private Map<String, Object> detail;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
}
