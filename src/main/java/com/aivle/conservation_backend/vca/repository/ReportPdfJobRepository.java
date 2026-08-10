package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.ReportPdfJob;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.UUID;

@Repository
public interface ReportPdfJobRepository
        extends JpaRepository<ReportPdfJob, UUID>, ReportPdfJobStore {

    @Override
    Optional<ReportPdfJob> findByAssessmentRunId(UUID assessmentRunId);
}
