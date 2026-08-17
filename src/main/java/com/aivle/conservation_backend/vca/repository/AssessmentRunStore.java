package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.AssessmentRun;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface AssessmentRunStore {

    AssessmentRun save(AssessmentRun entity);

    Optional<AssessmentRun> findById(UUID id);

    List<AssessmentRun> findAll();

    List<AssessmentRun> findByArtifactId(UUID artifactId);

    int countByArtifactId(UUID artifactId);

    void deleteById(UUID id);
}
