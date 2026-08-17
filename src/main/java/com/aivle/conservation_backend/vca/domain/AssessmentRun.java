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
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

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
public class AssessmentRun {

    public static final String RUN_TYPE_VCA = "VCA";
    public static final String RUN_TYPE_POTTERY_PATTERN = "POTTERY_PATTERN";

    @Id
    @Column(name = "id")
    private UUID id;

    @Column(name = "artifact_id", nullable = false)
    private UUID artifactId;

    @Column(name = "run_number", nullable = false)
    private int runNumber;

    /**
     * AssessmentRun의 종류.
     * VCA          : 실제 부식/손상 상태 조사
     * POTTERY_PATTERN : 도자기 문양 조사
     */
    @Column(name = "run_type", columnDefinition = "text")
    private String runType;

    @Column(name = "legacy_project_name")
    private String legacyProjectName;

    /**
     * 각 AssessmentRun row의 실행 상태.
     * VCA와 Pottery는 서로 다른 row를 사용하므로 공통 컬럼으로 사용한다.
     */
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

    // 모든 AssessmentRun은 생성 시 진행률 0부터 시작하므로 DB의 NOT NULL 제약과 맞춘다.
    @Column(name = "progress_percent", nullable = false)
    private int progressPercent;

    @Column(name = "started_at")
    private Instant startedAt;

    @Column(name = "completed_at")
    private Instant completedAt;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "config_json", columnDefinition = "jsonb")
    private Map<String, Object> configJson;

    @Column(name = "image_count", nullable = false)
    private int imageCount;

    @Column(name = "material")
    private String material;

    /**
     * 각 AssessmentRun row에 대응하는 AI Job ID.
     * runType으로 VCA/Pottery를 구분한다.
     */
    @Column(name = "ai_run_id", columnDefinition = "text")
    private String aiRunId;

    @Column(name = "failure_reason", columnDefinition = "text")
    private String failureReason;

    /**
     * VCA 실제 상태조사 AI의 파이프라인 단계.
     */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "stages_json", columnDefinition = "jsonb")
    private List<RunResponse.Stage> stages;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "uploaded_image_ids_json", columnDefinition = "jsonb")
    private List<String> uploadedImageIds;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "pottery_inspection_status_json", columnDefinition = "jsonb")
    private ReportResponse.PotteryInspectionStatus potteryInspectionStatus;

    /**
     * 문양조사 비동기 Job의 폴링 상태/오류 등의 내부 메타데이터.
     * VCA의 stages_json과 데이터 타입과 의미가 다르므로 별도 컬럼으로 저장한다.
     */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "pottery_job_state_json", columnDefinition = "jsonb")
    private Map<String, Object> potteryJobStateJson;

    /**
     * 문양조사(Pottery) AssessmentRun 생성용.
     * VCA는 기존 builder()를 사용한다.
     */
    public static AssessmentRun create(
            UUID id,
            UUID artifactId,
            int runNumber,
            String legacyProjectName,
            boolean dryRun,
            String requestedDevice,
            Map<String, Object> configJson
    ) {
        return AssessmentRun.builder()
                .id(id)
                .artifactId(artifactId)
                .runNumber(runNumber)
                .runType(RUN_TYPE_POTTERY_PATTERN)
                .legacyProjectName(legacyProjectName)
                .status("queued")
                .dryRun(dryRun)
                .requestedDevice(requestedDevice)
                .progressPercent(0)
                .imageCount(0)
                .configJson(configJson == null
                        ? null
                        : new LinkedHashMap<>(configJson))
                .build();
    }

    public void bindAiRun(String aiRunId, String resolvedDevice) {
        this.aiRunId = aiRunId;
        this.resolvedDevice = resolvedDevice;
    }

    public void mergeConfig(Map<String, Object> values) {
        if (values == null || values.isEmpty()) {
            return;
        }

        if (this.configJson == null) {
            this.configJson = new LinkedHashMap<>();
        }

        this.configJson.putAll(values);
    }

    public void setInputSnapshot(
            int imageCount,
            String material,
            List<UUID> uploadedImageIds
    ) {
        this.imageCount = imageCount;
        this.material = material;
        this.uploadedImageIds = uploadedImageIds == null
                ? null
                : uploadedImageIds.stream()
                        .map(UUID::toString)
                        .toList();
    }

    public void updatePotteryJobState(Map<String, Object> state) {
        this.potteryJobStateJson = state == null
                ? null
                : new LinkedHashMap<>(state);
    }

    public void markRunning(String resolvedDevice, String currentStage) {
        this.status = "running";
        this.resolvedDevice = resolvedDevice;
        this.currentStage = currentStage;

        if (this.startedAt == null) {
            this.startedAt = Instant.now();
        }
    }

    public void updateProgress(String currentStage, int progressPercent) {
        if (progressPercent < 0 || progressPercent > 100) {
            throw new IllegalArgumentException(
                    "progressPercent must be between 0 and 100"
            );
        }

        this.currentStage = currentStage;
        this.progressPercent = progressPercent;
    }

    public void markCompleted() {
        this.status = "completed";
        this.currentStage = "COMPLETED";
        this.progressPercent = 100;
        this.failureReason = null;
        this.completedAt = Instant.now();
    }

    public void markFailed(String currentStage) {
        markFailed(currentStage, null);
    }

    public void markFailed(String currentStage, String failureReason) {
        this.status = "failed";
        this.currentStage = currentStage;
        this.failureReason = failureReason;
        this.completedAt = null;
    }
}
