package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.VcaReportPdfJobEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.UUID;

@Repository
public interface VcaReportPdfJobRepository
        extends JpaRepository<VcaReportPdfJobEntity, UUID>, VcaReportPdfJobStore {

    @Override
    Optional<VcaReportPdfJobEntity> findByAssessmentRunId(UUID assessmentRunId);
}
