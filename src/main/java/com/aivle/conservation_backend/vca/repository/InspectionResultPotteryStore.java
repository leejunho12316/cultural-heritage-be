package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.InspectionResultPottery;

import java.util.Optional;
import java.util.UUID;

public interface InspectionResultPotteryStore {

    InspectionResultPottery save(InspectionResultPottery entity);

    Optional<InspectionResultPottery> findByAssessmentRunId(UUID assessmentRunId);
}
