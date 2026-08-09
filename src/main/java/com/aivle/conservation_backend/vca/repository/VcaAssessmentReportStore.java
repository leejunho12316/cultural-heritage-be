package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.VcaAssessmentReportEntity;

import java.util.Optional;
import java.util.UUID;

public interface VcaAssessmentReportStore {

    VcaAssessmentReportEntity save(VcaAssessmentReportEntity entity);

    Optional<VcaAssessmentReportEntity> findById(UUID assessmentRunId);
}
