package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface AssessmentRunRepository extends JpaRepository<AssessmentRun, UUID> {

    Optional<AssessmentRun> findByIdAndArtifactId(UUID id, UUID artifactId);

    List<AssessmentRun> findAllByArtifactIdOrderByRunNumberDesc(UUID artifactId);

    boolean existsByArtifactIdAndStatusIn(UUID artifactId, List<String> statuses);

    boolean existsByArtifactIdAndStatusInAndAiRunIdIsNotNull(
            UUID artifactId,
            List<String> statuses
    );

    List<AssessmentRun> findAllByStatusInAndAiRunIdIsNotNull(List<String> statuses);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select r from AssessmentRun r where r.id = :id and r.artifactId = :artifactId")
    Optional<AssessmentRun> findByIdAndArtifactIdForUpdate(
            @Param("id") UUID id,
            @Param("artifactId") UUID artifactId
    );

    @Query("select coalesce(max(r.runNumber), 0) from AssessmentRun r where r.artifactId = :artifactId")
    int findMaxRunNumberByArtifactId(@Param("artifactId") UUID artifactId);
}
