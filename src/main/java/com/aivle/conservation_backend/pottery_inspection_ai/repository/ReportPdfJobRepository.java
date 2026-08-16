package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.ReportPdfJob;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface ReportPdfJobRepository extends JpaRepository<ReportPdfJob, UUID> {

    List<ReportPdfJob> findAllByAssessmentRun_Id(UUID assessmentRunId);

    Optional<ReportPdfJob> findByIdAndAssessmentRun_ArtifactId(UUID id, UUID artifactId);

    Optional<ReportPdfJob> findFirstByAssessmentRun_IdAndLayoutOrderByRequestedAtDesc(
            UUID assessmentRunId,
            String layout
    );
}
