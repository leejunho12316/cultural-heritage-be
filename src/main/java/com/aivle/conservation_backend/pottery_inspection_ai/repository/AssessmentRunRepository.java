package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface AssessmentRunRepository extends JpaRepository<AssessmentRun, UUID> {

    Optional<AssessmentRun> findByIdAndArtifactId(UUID id, UUID artifactId);

    List<AssessmentRun> findAllByArtifactIdOrderByRunNumberDesc(UUID artifactId);

    boolean existsByArtifactIdAndStatusIn(UUID artifactId, List<String> statuses);

    @Query("select coalesce(max(r.runNumber), 0) from AssessmentRun r where r.artifactId = :artifactId")
    int findMaxRunNumberByArtifactId(@Param("artifactId") UUID artifactId);
}
