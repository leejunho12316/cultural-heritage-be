package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.ReportPdfJob;

import java.util.Optional;
import java.util.UUID;

public interface ReportPdfJobStore {

    ReportPdfJob save(ReportPdfJob entity);

    Optional<ReportPdfJob> findById(UUID id);

    Optional<ReportPdfJob> findByAssessmentRunId(UUID assessmentRunId);
}
