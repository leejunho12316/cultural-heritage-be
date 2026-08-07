package com.aivle.conservation_backend.xray_api.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

@Entity
@Table(name = "xray_job")
public class XrayJob {

    @Id
    @Column(name = "id", nullable = false, updatable = false)
    private UUID id;

    @Column(name = "artifact_id", nullable = false)
    private UUID artifactId;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 20)
    private XrayJobStatus status;

    @Column(name = "message", length = 500)
    private String message;

    @Column(name = "error_message", columnDefinition = "text")
    private String errorMessage;

    @Column(name = "color_file_name", length = 500)
    private String colorFileName;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "xray_file_names", columnDefinition = "jsonb")
    private List<String> xrayFileNames;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @Column(name = "completed_at")
    private Instant completedAt;

    @Column(name = "finalized_at")
    private Instant finalizedAt;

    protected XrayJob() {
    }

    public UUID getId() {
        return id;
    }

    public UUID getArtifactId() {
        return artifactId;
    }

    public XrayJobStatus getStatus() {
        return status;
    }

    public String getMessage() {
        return message;
    }

    public String getErrorMessage() {
        return errorMessage;
    }

    public String getColorFileName() {
        return colorFileName;
    }

    public List<String> getXrayFileNames() {
        return xrayFileNames;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }

    public Instant getCompletedAt() {
        return completedAt;
    }

    public Instant getFinalizedAt() {
        return finalizedAt;
    }

    public static XrayJob create(UUID id, UUID artifactId) {
        XrayJob job = new XrayJob();
        Instant now = Instant.now();
        job.id = id;
        job.artifactId = artifactId;
        job.status = XrayJobStatus.PENDING;
        job.message = "Waiting for S3 input uploads.";
        job.createdAt = now;
        job.updatedAt = now;
        return job;
    }

    public void rememberInputs(String colorFileName, List<String> xrayFileNames) {
        this.colorFileName = colorFileName;
        this.xrayFileNames = xrayFileNames == null ? List.of() : List.copyOf(xrayFileNames);
        touch();
    }

    public void markRunning() {
        this.status = XrayJobStatus.RUNNING;
        this.message = "X-ray stitching is running.";
        this.errorMessage = null;
        touch();
    }

    public void markCompleted(String message) {
        this.status = XrayJobStatus.COMPLETED;
        this.message = defaultMessage(message, "X-ray stitching completed.");
        this.errorMessage = null;
        this.completedAt = Instant.now();
        touch();
    }

    public void markFinalizing() {
        this.status = XrayJobStatus.FINALIZING;
        this.message = "Final X-ray rendering is running.";
        this.errorMessage = null;
        touch();
    }

    public void markFinalized(String message) {
        this.status = XrayJobStatus.FINALIZED;
        this.message = defaultMessage(message, "Final X-ray rendering completed.");
        this.errorMessage = null;
        this.finalizedAt = Instant.now();
        if (this.completedAt == null) {
            this.completedAt = this.finalizedAt;
        }
        touch();
    }

    public void markFailed(String message, String errorMessage) {
        this.status = XrayJobStatus.FAILED;
        this.message = defaultMessage(message, "X-ray processing failed.");
        this.errorMessage = errorMessage;
        touch();
    }

    private void touch() {
        this.updatedAt = Instant.now();
    }

    private static String defaultMessage(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }
}
