package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.VcaReportPdfJobEntity;

import java.util.Optional;
import java.util.UUID;

public interface VcaReportPdfJobStore {

    VcaReportPdfJobEntity save(VcaReportPdfJobEntity entity);

    Optional<VcaReportPdfJobEntity> findById(UUID id);

    Optional<VcaReportPdfJobEntity> findByAssessmentRunId(UUID assessmentRunId);
}
