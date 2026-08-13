package com.aivle.conservation_backend.xray_api.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(
        name = "xray_job",
        uniqueConstraints = @UniqueConstraint(
                name = "uk_xray_job_artifact",
                columnNames = "artifact_id"
        )
)
public class XrayJob {

    @Id
    @Column(name = "id", nullable = false, updatable = false)
    private UUID id;

    /**
     * 현재는 FE local/session에서 전달받는 UUID를 그대로 저장한다.
     * ARTIFACT 테이블 통합 후 FK를 추가하되, 유물당 X-ray 작업 1회 정책은
     * 지금부터 UNIQUE로 유지한다.
     */
    @Column(name = "artifact_id", nullable = false, unique = true)
    private UUID artifactId;

    /** 인증/사용자 통합 전까지 nullable. 이후 USER FK 연결 예정. */
    @Column(name = "user_id")
    private UUID userId;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 20)
    private XrayJobStatus status;

    @Column(name = "error_message", columnDefinition = "text")
    private String errorMessage;

    @Column(name = "report_text", columnDefinition = "text")
    private String reportText;

    @Column(name = "expected_color_count", nullable = false)
    private int expectedColorCount;

    @Column(name = "expected_xray_count", nullable = false)
    private int expectedXrayCount;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @Column(name = "completed_at")
    private Instant completedAt;

    protected XrayJob() {
    }

    public UUID getId() {
        return id;
    }

    public UUID getArtifactId() {
        return artifactId;
    }

    public UUID getUserId() {
        return userId;
    }

    public XrayJobStatus getStatus() {
        return status;
    }

    public String getErrorMessage() {
        return errorMessage;
    }

    public String getReportText() {
        return reportText;
    }

    public int getExpectedColorCount() {
        return expectedColorCount;
    }

    public int getExpectedXrayCount() {
        return expectedXrayCount;
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

    public static XrayJob create(
            UUID id,
            UUID artifactId,
            UUID userId,
            int expectedColorCount,
            int expectedXrayCount
    ) {
        XrayJob job = new XrayJob();
        Instant now = Instant.now();
        job.id = id;
        job.artifactId = artifactId;
        job.userId = userId;
        job.status = XrayJobStatus.PREPARED;
        job.expectedColorCount = expectedColorCount;
        job.expectedXrayCount = expectedXrayCount;
        job.createdAt = now;
        job.updatedAt = now;
        return job;
    }

    /**
     * 같은 artifact의 기존 작업을 새 X-ray 분석용으로 초기화한다.
     *
     * <p>실행 중 상태(STITCHING/탐지 세부 상태) 차단은 service에서 담당하고,
     * 여기서는 재실행 가능한 작업의 workflow 결과 필드를 초기화한다.</p>
     */
    public void prepareAgain(int expectedColorCount, int expectedXrayCount) {
        this.status = XrayJobStatus.PREPARED;
        this.expectedColorCount = expectedColorCount;
        this.expectedXrayCount = expectedXrayCount;
        this.errorMessage = null;
        this.reportText = null;
        this.completedAt = null;
        touch();
    }

    public void markUploading() {
        this.status = XrayJobStatus.UPLOADING;
        this.errorMessage = null;
        touch();
    }

    public void markStitching() {
        this.status = XrayJobStatus.STITCHING;
        this.errorMessage = null;
        touch();
    }

    public void markStitched() {
        this.status = XrayJobStatus.STITCHED;
        this.errorMessage = null;
        touch();
    }

    public void markDetecting() {
        this.status = XrayJobStatus.DETECTING;
        this.errorMessage = null;
        touch();
    }

    public void markDetectingFragments() {
        this.status = XrayJobStatus.DETECTING_FRAGMENTS;
        this.errorMessage = null;
        touch();
    }

    public void markDetectingAssembled() {
        this.status = XrayJobStatus.DETECTING_ASSEMBLED;
        this.errorMessage = null;
        touch();
    }

    public void markMapping() {
        this.status = XrayJobStatus.MAPPING;
        this.errorMessage = null;
        touch();
    }

    public void markReviewReady() {
        this.status = XrayJobStatus.REVIEW_READY;
        this.errorMessage = null;
        touch();
    }

    public void markReporting() {
        this.status = XrayJobStatus.REPORTING;
        this.errorMessage = null;
        touch();
    }

    public void updateReportText(String reportText) {
        this.reportText = reportText;
        touch();
    }

    public void markCompleted() {
        this.status = XrayJobStatus.COMPLETED;
        this.errorMessage = null;
        this.completedAt = Instant.now();
        touch();
    }

    public void markFailed(String errorMessage) {
        this.status = XrayJobStatus.FAILED;
        this.errorMessage = errorMessage;
        touch();
    }

    private void touch() {
        this.updatedAt = Instant.now();
    }
}