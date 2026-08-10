package com.aivle.conservation_backend.vca.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(
        name = "uploaded_image",
        uniqueConstraints = @UniqueConstraint(
                name = "uk_uploaded_image_artifact_object_key",
                columnNames = {"artifact_id", "object_key"}
        )
)
public class UploadedImage {

    @Id
    @Column(name = "id", nullable = false, updatable = false)
    private UUID id;

    /** Artifact 엔티티 통합 전까지 UUID만 보관한다. */
    @Column(name = "artifact_id", nullable = false)
    private UUID artifactId;

    @Column(name = "filename", nullable = false, columnDefinition = "text")
    private String filename;

    @Column(name = "object_key", nullable = false, columnDefinition = "text")
    private String objectKey;

    @Column(name = "thumbnail_object_key", columnDefinition = "text")
    private String thumbnailObjectKey;

    @Column(name = "media_type", columnDefinition = "text")
    private String mediaType;

    @Column(name = "content_sha256", columnDefinition = "text")
    private String contentSha256;

    @Column(name = "size_bytes")
    private Long sizeBytes;

    @Column(name = "status", nullable = false, columnDefinition = "text")
    private String status;

    @Column(name = "width")
    private Integer width;

    @Column(name = "height")
    private Integer height;

    @Column(name = "display_order", nullable = false)
    private int displayOrder;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "uploaded_at")
    private Instant uploadedAt;

    @Column(name = "local_path", columnDefinition = "text")
    private String localPath;

    @Column(name = "upload_mode", nullable = false, columnDefinition = "text")
    private String uploadMode;

    protected UploadedImage() {
    }

    public static UploadedImage pending(
            UUID id,
            UUID artifactId,
            String filename,
            String objectKey,
            String mediaType,
            int displayOrder,
            String uploadMode,
            String localPath
    ) {
        UploadedImage image = new UploadedImage();
        image.id = id;
        image.artifactId = artifactId;
        image.filename = filename;
        image.objectKey = objectKey;
        image.mediaType = mediaType;
        image.displayOrder = displayOrder;
        image.status = "PENDING";
        image.uploadMode = uploadMode;
        image.localPath = localPath;
        image.createdAt = Instant.now();
        return image;
    }

    public UUID getId() {
        return id;
    }

    public UUID getArtifactId() {
        return artifactId;
    }

    public String getFilename() {
        return filename;
    }

    public String getObjectKey() {
        return objectKey;
    }

    public String getThumbnailObjectKey() {
        return thumbnailObjectKey;
    }

    public String getMediaType() {
        return mediaType;
    }

    public String getContentSha256() {
        return contentSha256;
    }

    public Long getSizeBytes() {
        return sizeBytes;
    }

    public String getStatus() {
        return status;
    }

    public Integer getWidth() {
        return width;
    }

    public Integer getHeight() {
        return height;
    }

    public int getDisplayOrder() {
        return displayOrder;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUploadedAt() {
        return uploadedAt;
    }

    public String getLocalPath() {
        return localPath;
    }

    public String getUploadMode() {
        return uploadMode;
    }

    public void completeUpload(
            String contentSha256,
            Long sizeBytes,
            Integer width,
            Integer height,
            String thumbnailObjectKey
    ) {
        this.contentSha256 = contentSha256;
        this.sizeBytes = sizeBytes;
        this.width = width;
        this.height = height;
        this.thumbnailObjectKey = thumbnailObjectKey;
        this.status = "UPLOADED";
        this.uploadedAt = Instant.now();
    }
}
