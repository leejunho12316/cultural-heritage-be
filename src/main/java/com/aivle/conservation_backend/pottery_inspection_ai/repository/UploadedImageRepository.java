package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.UploadedImage;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface UploadedImageRepository extends JpaRepository<UploadedImage, UUID> {

    Optional<UploadedImage> findByIdAndArtifactId(UUID id, UUID artifactId);

    Optional<UploadedImage> findByArtifactIdAndObjectKey(UUID artifactId, String objectKey);

    Optional<UploadedImage> findFirstByArtifactIdAndContentSha256OrderByCreatedAtDesc(
            UUID artifactId,
            String contentSha256
    );

    List<UploadedImage> findAllByArtifactIdOrderByDisplayOrderAscCreatedAtAsc(UUID artifactId);

    List<UploadedImage> findAllByArtifactIdAndStatusOrderByDisplayOrderAscCreatedAtAsc(
            UUID artifactId,
            String status
    );
}
