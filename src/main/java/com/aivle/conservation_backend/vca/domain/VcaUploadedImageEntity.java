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

import java.time.Instant;
import java.util.UUID;

// 업로드 이미지의 영속 상태. 팀 공유 ERD의 `uploaded_image` 테이블.
// thumbnailObjectKey/width/height는 아직 만들지 않은 썸네일/치수 추출 기능을 위한 컬럼이라
// 지금은 항상 null이다. uploadMode/localPath는 ERD에 없지만 업로드 검증/로컬 폴백 경로에 필요해 추가했다.
@Entity
@Table(
        name = "uploaded_image",
        uniqueConstraints = @UniqueConstraint(columnNames = {"artifact_id", "object_key"})
)
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class VcaUploadedImageEntity {

    @Id
    @Column(name = "id")
    private UUID id;

    @Column(name = "artifact_id", nullable = false)
    private UUID artifactId;

    @Column(name = "filename")
    private String filename;

    @Column(name = "object_key")
    private String objectKey;

    @Column(name = "thumbnail_object_key")
    private String thumbnailObjectKey;

    @Column(name = "media_type")
    private String mediaType;

    @Column(name = "content_sha256")
    private String contentSha256;

    @Column(name = "size_bytes", nullable = false)
    private long sizeBytes;

    @Column(name = "status", nullable = false)
    private String status;

    @Column(name = "width")
    private Integer width;

    @Column(name = "height")
    private Integer height;

    @Column(name = "display_order", nullable = false)
    private int displayOrder;

    @Column(name = "created_at")
    private Instant createdAt;

    @Column(name = "uploaded_at")
    private Instant uploadedAt;

    // --- ERD 외 구현 필수 컬럼 ---

    @Column(name = "upload_mode")
    private String uploadMode;

    @Column(name = "local_path")
    private String localPath;
}
