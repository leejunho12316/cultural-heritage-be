package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.UploadedImage;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

@Repository
public interface UploadedImageRepository
        extends JpaRepository<UploadedImage, UUID>, UploadedImageStore {

    @Override
    List<UploadedImage> findByArtifactId(UUID artifactId);
}
