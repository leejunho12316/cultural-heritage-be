package com.aivle.conservation_backend.vca.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Entity
@Table(
        name = "assessment_run",
        uniqueConstraints = @UniqueConstraint(
                name = "uk_assessment_run_artifact_number",
                columnNames = {"artifact_id", "run_number"}
        )
)
public class AssessmentRun {

    @Id
    @Column(name = "id", nullable = false, updatable = false)
    private UUID id;

    /** Artifact 엔티티 통합 전까지 UUID만 보관한다. */
    @Column(name = "artifact_id", nullable = false)
    private UUID artifactId;

    @Column(name = "run_number", nullable = false)
    private int runNumber;

    @Column(name = "legacy_project_name", columnDefinition = "text")
    private String legacyProjectName;

    @Column(name = "status", nullable = false, columnDefinition = "text")
    private String status;

    @Column(name = "dry_run", nullable = false)
    private boolean dryRun;

    @Column(name = "requested_device", columnDefinition = "text")
    private String requestedDevice;

    @Column(name = "resolved_device", columnDefinition = "text")
    private String resolvedDevice;

    @Column(name = "current_stage", columnDefinition = "text")
    private String currentStage;

    @Column(name = "progress_percent", nullable = false)
    private int progressPercent;

    @Column(name = "started_at")
    private Instant startedAt;

    @Column(name = "completed_at")
    private Instant completedAt;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "config_json", columnDefinition = "jsonb")
    private Map<String, Object> configJson;

    @Column(name = "ai_run_id", columnDefinition = "text")
    private String aiRunId;

    @Column(name = "image_count")
    private Integer imageCount;

    @Column(name = "material", columnDefinition = "text")
    private String material;

    @Column(name = "failure_reason", columnDefinition = "text")
    private String failureReason;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "stages_json", columnDefinition = "jsonb")
    private Map<String, Object> stagesJson;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "uploaded_image_ids_json", columnDefinition = "jsonb")
    private List<UUID> uploadedImageIdsJson;

    protected AssessmentRun() {
    }

    public static AssessmentRun create(
            UUID id,
            UUID artifactId,
            int runNumber,
            String legacyProjectName,
            boolean dryRun,
            String requestedDevice,
            Map<String, Object> configJson
    ) {
        AssessmentRun run = new AssessmentRun();
        run.id = id;
        run.artifactId = artifactId;
        run.runNumber = runNumber;
        run.legacyProjectName = legacyProjectName;
        run.status = "queued";
        run.dryRun = dryRun;
        run.requestedDevice = requestedDevice;
        run.progressPercent = 0;
        run.configJson = configJson == null ? null : new LinkedHashMap<>(configJson);
        return run;
    }

    public UUID getId() {
        return id;
    }

    public UUID getArtifactId() {
        return artifactId;
    }

    public int getRunNumber() {
        return runNumber;
    }

    public String getLegacyProjectName() {
        return legacyProjectName;
    }

    public String getStatus() {
        return status;
    }

    public boolean isDryRun() {
        return dryRun;
    }

    public String getRequestedDevice() {
        return requestedDevice;
    }

    public String getResolvedDevice() {
        return resolvedDevice;
    }

    public String getCurrentStage() {
        return currentStage;
    }

    public int getProgressPercent() {
        return progressPercent;
    }

    public Instant getStartedAt() {
        return startedAt;
    }

    public Instant getCompletedAt() {
        return completedAt;
    }

    public Map<String, Object> getConfigJson() {
        return configJson;
    }

    public String getAiRunId() {
        return aiRunId;
    }

    public Integer getImageCount() {
        return imageCount;
    }

    public String getMaterial() {
        return material;
    }

    public String getFailureReason() {
        return failureReason;
    }

    public Map<String, Object> getStagesJson() {
        return stagesJson;
    }

    public List<UUID> getUploadedImageIdsJson() {
        return uploadedImageIdsJson;
    }

    public void bindAiRun(String aiRunId, String resolvedDevice) {
        this.aiRunId = aiRunId;
        this.resolvedDevice = resolvedDevice;
    }

    public void setInputSnapshot(
            int imageCount,
            String material,
            List<UUID> uploadedImageIds
    ) {
        this.imageCount = imageCount;
        this.material = material;
        this.uploadedImageIdsJson = uploadedImageIds == null
                ? null
                : new ArrayList<>(uploadedImageIds);
    }

    public void updateStages(Map<String, Object> stagesJson) {
        this.stagesJson = stagesJson == null ? null : new LinkedHashMap<>(stagesJson);
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
            throw new IllegalArgumentException("progressPercent must be between 0 and 100");
        }
        this.currentStage = currentStage;
        this.progressPercent = progressPercent;
    }

    public void markCompleted() {
        this.status = "completed";
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
