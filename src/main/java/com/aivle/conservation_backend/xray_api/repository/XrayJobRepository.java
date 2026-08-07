package com.aivle.conservation_backend.xray_api.repository;

import com.aivle.conservation_backend.xray_api.domain.XrayJob;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;
import java.util.UUID;

public interface XrayJobRepository extends JpaRepository<XrayJob, UUID> {
    Optional<XrayJob> findTopByArtifactIdOrderByCreatedAtDesc(UUID artifactId);
}
