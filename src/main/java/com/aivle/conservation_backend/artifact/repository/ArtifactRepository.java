package com.aivle.conservation_backend.artifact.repository;

import com.aivle.conservation_backend.artifact.domain.Artifact;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.UUID;

public interface ArtifactRepository extends JpaRepository<Artifact, UUID> {
}