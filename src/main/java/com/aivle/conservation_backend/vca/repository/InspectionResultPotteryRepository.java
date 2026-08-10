package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.InspectionResultPottery;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.UUID;

@Repository
public interface InspectionResultPotteryRepository
        extends JpaRepository<InspectionResultPottery, UUID>, InspectionResultPotteryStore {

    @Override
    Optional<InspectionResultPottery> findByAssessmentRunId(UUID assessmentRunId);
}
