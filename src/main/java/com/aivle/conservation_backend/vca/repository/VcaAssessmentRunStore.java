package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.VcaAssessmentRunEntity;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface VcaAssessmentRunStore {

    VcaAssessmentRunEntity save(VcaAssessmentRunEntity entity);

    Optional<VcaAssessmentRunEntity> findById(UUID id);

    List<VcaAssessmentRunEntity> findAll();

    List<VcaAssessmentRunEntity> findByArtifactId(UUID artifactId);

    int countByArtifactId(UUID artifactId);

    void deleteById(UUID id);
}
