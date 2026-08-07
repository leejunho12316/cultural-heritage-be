package com.aivle.conservation_backend.xray_api.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;
import java.util.UUID;

/**
 * 공용 S3_FILE 테이블의 X-ray 조회용 최소 매핑.
 * 생성/갱신은 S3 ObjectCreated Lambda가 담당한다.
 */
@Entity
@Table(name = "s3_file")
public class S3FileRecord {

    @Id
    private UUID id;

    @Column(name = "artifact_id", nullable = false)
    private UUID artifactId;

    @Column(name = "module_type", nullable = false, length = 20)
    private String moduleType;

    @Column(name = "usage_name", nullable = false, length = 30)
    private String usageName;

    @Column(name = "source_order")
    private Integer sourceOrder;

    @Column(name = "original_name", length = 500)
    private String originalName;

    @Column(name = "s3_key", nullable = false, unique = true, length = 500)
    private String s3Key;

    @Column(name = "bucket_name", length = 255)
    private String bucketName;

    @Column(name = "status", nullable = false, length = 20)
    private String status;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected S3FileRecord() {
    }

    public UUID getId() {
        return id;
    }

    public UUID getArtifactId() {
        return artifactId;
    }

    public String getModuleType() {
        return moduleType;
    }

    public String getUsageName() {
        return usageName;
    }

    public Integer getSourceOrder() {
        return sourceOrder;
    }

    public String getOriginalName() {
        return originalName;
    }

    public String getS3Key() {
        return s3Key;
    }
}
