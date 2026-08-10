package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

@Repository
public interface AssessmentRunRepository
        extends JpaRepository<AssessmentRun, UUID>, AssessmentRunStore {

    @Override
    List<AssessmentRun> findByArtifactId(UUID artifactId);

    @Override
    int countByArtifactId(UUID artifactId);
}
