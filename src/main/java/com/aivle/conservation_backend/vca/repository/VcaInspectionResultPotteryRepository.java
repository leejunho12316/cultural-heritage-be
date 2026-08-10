package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.InspectionResultPotteryEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.UUID;

@Repository
public interface VcaInspectionResultPotteryRepository
        extends JpaRepository<InspectionResultPotteryEntity, UUID>, VcaInspectionResultPotteryStore {

    @Override
    Optional<InspectionResultPotteryEntity> findByAssessmentRunId(UUID assessmentRunId);
}
