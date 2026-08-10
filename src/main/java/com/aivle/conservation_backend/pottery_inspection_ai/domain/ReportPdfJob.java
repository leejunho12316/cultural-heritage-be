package com.aivle.conservation_backend.vca.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "report_pdf_job")
public class ReportPdfJob {

    @Id
    @Column(name = "id", nullable = false, updatable = false)
    private UUID id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "assessment_run_id", nullable = false)
    private AssessmentRun assessmentRun;

    @Column(name = "status", nullable = false, columnDefinition = "text")
    private String status;

    @Column(name = "layout", columnDefinition = "text")
    private String layout;

    @Column(name = "pdf_object_key", columnDefinition = "text")
    private String pdfObjectKey;

    @Column(name = "requested_at", nullable = false)
    private Instant requestedAt;

    @Column(name = "completed_at")
    private Instant completedAt;

    protected ReportPdfJob() {
    }

    public static ReportPdfJob create(UUID id, AssessmentRun run, String layout) {
        ReportPdfJob job = new ReportPdfJob();
        job.id = id;
        job.assessmentRun = run;
        job.status = "QUEUED";
        job.layout = layout;
        job.requestedAt = Instant.now();
        return job;
    }

    public UUID getId() {
        return id;
    }

    public AssessmentRun getAssessmentRun() {
        return assessmentRun;
    }

    public String getStatus() {
        return status;
    }

    public String getLayout() {
        return layout;
    }

    public String getPdfObjectKey() {
        return pdfObjectKey;
    }

    public Instant getRequestedAt() {
        return requestedAt;
    }

    public Instant getCompletedAt() {
        return completedAt;
    }

    public void markRunning() {
        this.status = "RUNNING";
    }

    public void markCompleted(String pdfObjectKey) {
        this.status = "COMPLETED";
        this.pdfObjectKey = pdfObjectKey;
        this.completedAt = Instant.now();
    }

    public void markFailed() {
        this.status = "FAILED";
        this.completedAt = null;
    }
}
