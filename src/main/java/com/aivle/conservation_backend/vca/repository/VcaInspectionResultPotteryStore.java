package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.InspectionResultPotteryEntity;

import java.util.Optional;
import java.util.UUID;

public interface VcaInspectionResultPotteryStore {

    InspectionResultPotteryEntity save(InspectionResultPotteryEntity entity);

    Optional<InspectionResultPotteryEntity> findByAssessmentRunId(UUID assessmentRunId);
}
