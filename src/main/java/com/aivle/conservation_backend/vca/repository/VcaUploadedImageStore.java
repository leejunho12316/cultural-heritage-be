package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.VcaUploadedImageEntity;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface VcaUploadedImageStore {

    VcaUploadedImageEntity save(VcaUploadedImageEntity entity);

    Optional<VcaUploadedImageEntity> findById(UUID id);

    List<VcaUploadedImageEntity> findByArtifactId(UUID artifactId);

    void deleteById(UUID id);
}
