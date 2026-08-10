package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.AssessmentReport;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;
import java.util.UUID;

public interface AssessmentReportRepository extends JpaRepository<AssessmentReport, UUID> {

    Optional<AssessmentReport> findByAssessmentRun_IdAndAssessmentRun_ArtifactId(
            UUID assessmentRunId,
            UUID artifactId
    );
}
