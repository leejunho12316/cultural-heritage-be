package com.aivle.conservation_backend.vca.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.Instant;
import java.util.UUID;

// report PDF 생성 job의 영속 상태. 팀 공유 ERD의 `report_pdf_job` 테이블.
// layout/pdfObjectKey는 아직 만들지 않은 실제 PDF 렌더링 기능을 위한 컬럼이라 지금은 항상 null이다.
@Entity
@Table(name = "report_pdf_job")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class VcaReportPdfJobEntity {

    @Id
    @Column(name = "id")
    private UUID id;

    @Column(name = "assessment_run_id", nullable = false)
    private UUID assessmentRunId;

    @Column(name = "status", nullable = false)
    private String status;

    @Column(name = "layout")
    private String layout;

    @Column(name = "pdf_object_key")
    private String pdfObjectKey;

    @Column(name = "requested_at")
    private Instant requestedAt;

    @Column(name = "completed_at")
    private Instant completedAt;
}
