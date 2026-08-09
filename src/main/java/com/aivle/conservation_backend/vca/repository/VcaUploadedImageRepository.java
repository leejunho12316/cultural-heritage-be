package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.VcaUploadedImageEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

@Repository
public interface VcaUploadedImageRepository
        extends JpaRepository<VcaUploadedImageEntity, UUID>, VcaUploadedImageStore {

    @Override
    List<VcaUploadedImageEntity> findByArtifactId(UUID artifactId);
}
