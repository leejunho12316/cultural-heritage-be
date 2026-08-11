package com.aivle.conservation_backend.report_ai.domain;

import com.aivle.conservation_backend.artifact.domain.Artifact;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;

import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * report-ai가 생성한 report_json + 변환된 .docx를 유물(artifact) 기준으로
 * 저장해서, 나중에 게시판 등에서 다시 조회할 수 있게 한다.
 *
 * `assessment_report`(육안조사 vca 파이프라인의 실행 허브 assessment_run에
 * 딸린 테이블)와는 별개 테이블이다 - report-ai는 vca 결과 중 필요한 일부만
 * 가져다 쓰는 입장이지, vca 파이프라인의 산출물 자체가 아니다. 그래서
 * report_document는 assessment_run에 의존하지 않고 artifact_id만으로
 * 저장·조회한다 - 육안조사 실행 여부와 무관하게 보고서를 저장할 수 있다.
 */
@Entity
@Table(name = "report_document")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class ReportDocument {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    @Column(name = "id", updatable = false, nullable = false)
    private UUID id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "artifact_id", nullable = false)
    private Artifact artifact;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "report_json", nullable = false, columnDefinition = "jsonb")
    private Map<String, Object> reportJson;

    @Column(name = "docx_object_key", length = 1000)
    private String docxObjectKey;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    @Builder
    public ReportDocument(
            Artifact artifact,
            Map<String, Object> reportJson,
            String docxObjectKey
    ) {
        this.artifact = artifact;
        this.reportJson = reportJson == null ? null : new LinkedHashMap<>(reportJson);
        this.docxObjectKey = docxObjectKey;
    }

    @PrePersist
    protected void onCreate() {
        LocalDateTime now = LocalDateTime.now();
        this.createdAt = now;
        this.updatedAt = now;
    }

    @PreUpdate
    protected void onUpdate() {
        this.updatedAt = LocalDateTime.now();
    }
}
