package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.VcaAssessmentRunEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

@Repository
public interface VcaAssessmentRunRepository
        extends JpaRepository<VcaAssessmentRunEntity, UUID>, VcaAssessmentRunStore {

    @Override
    List<VcaAssessmentRunEntity> findByArtifactId(UUID artifactId);

    @Override
    int countByArtifactId(UUID artifactId);
}
