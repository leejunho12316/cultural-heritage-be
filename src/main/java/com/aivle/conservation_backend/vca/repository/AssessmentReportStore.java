package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.AssessmentReport;

import java.util.Optional;
import java.util.UUID;

public interface AssessmentReportStore {

    AssessmentReport save(AssessmentReport entity);

    Optional<AssessmentReport> findById(UUID assessmentRunId);
}
