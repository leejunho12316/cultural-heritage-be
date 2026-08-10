package com.aivle.conservation_backend.vca.domain;

import com.aivle.conservation_backend.vca.dto.ReportResponse;
import com.aivle.conservation_backend.vca.dto.RunResponse;
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
import java.util.List;
import java.util.Map;
import java.util.UUID;

// VCA assessment run의 영속 상태. 팀 공유 ERD의 `assessment_run` 테이블.
// ERD에 없는 aiRunId/failureReason/stages/uploadedImageIds/potteryInspection*은
// 지금 API가 실제로 필요로 하는 구현 세부값이라 추가한 컬럼이다 (VcaService.RunState와 1:1 대응).
@Entity
@Table(
        name = "assessment_run",
        uniqueConstraints = @UniqueConstraint(columnNames = {"artifact_id", "run_number"})
)
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class VcaAssessmentRunEntity {

    @Id
    @Column(name = "id")
    private UUID id;

    @Column(name = "artifact_id", nullable = false)
    private UUID artifactId;

    @Column(name = "run_number", nullable = false)
    private int runNumber;

    @Column(name = "legacy_project_name")
    private String legacyProjectName;

    @Column(name = "status", nullable = false)
    private String status;

    @Column(name = "dry_run", nullable = false)
    private boolean dryRun;

    @Column(name = "requested_device")
    private String requestedDevice;

    @Column(name = "resolved_device")
    private String resolvedDevice;

    @Column(name = "current_stage")
    private String currentStage;

    @Column(name = "progress_percent")
    private Integer progressPercent;

    @Column(name = "started_at")
    private Instant startedAt;

    @Column(name = "completed_at")
    private Instant completedAt;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "config_json", columnDefinition = "jsonb")
    private Map<String, Object> configJson;

    // --- 아래부터는 ERD 외 구현 필수 컬럼 ---

    @Column(name = "image_count", nullable = false)
    private int imageCount;

    @Column(name = "material")
    private String material;

    @Column(name = "ai_run_id")
    private String aiRunId;

    // vca-ai가 넘기는 실패 사유는 엔진의 원본 예외/트레이스백 텍스트를 그대로
    // 담을 수 있어 기본 varchar(255)를 쉽게 넘긴다 - 실제로 한 번 이걸로
    // DataIntegrityViolationException이 나서 run 상태 동기화 자체가
    // 무한 반복 실패한 적이 있다(그러면 이 아티팩트의 모든 조회가 막힌다).
    @Column(name = "failure_reason", columnDefinition = "text")
    private String failureReason;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "stages_json", columnDefinition = "jsonb")
    private List<RunResponse.Stage> stages;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "uploaded_image_ids_json", columnDefinition = "jsonb")
    private List<String> uploadedImageIds;

    // 도자기 검사 "결과"는 더 이상 여기 없다 - inspection_result_pottery 테이블로
    // 옮겼다(assessment_run_id 1:1). 이 컬럼은 워크플로우 상태(진행/실패/재시도)만
    // 담당한다 - run 자신의 status/failure_reason과 같은 성격의 process metadata라
    // 결과와 분리해서 여기 남겨뒀다.
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "pottery_inspection_status_json", columnDefinition = "jsonb")
    private ReportResponse.PotteryInspectionStatus potteryInspectionStatus;
}
