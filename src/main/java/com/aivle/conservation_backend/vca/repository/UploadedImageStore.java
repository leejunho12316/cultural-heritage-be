package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.UploadedImage;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface UploadedImageStore {

    UploadedImage save(UploadedImage entity);

    Optional<UploadedImage> findById(UUID id);

    List<UploadedImage> findByArtifactId(UUID artifactId);

    void deleteById(UUID id);
}
