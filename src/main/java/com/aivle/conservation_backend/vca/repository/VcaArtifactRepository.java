package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.VcaArtifactEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.UUID;

@Repository
public interface VcaArtifactRepository extends JpaRepository<VcaArtifactEntity, UUID>, VcaArtifactStore {

    @Override
    Optional<VcaArtifactEntity> findByArtifactCode(String artifactCode);
}
