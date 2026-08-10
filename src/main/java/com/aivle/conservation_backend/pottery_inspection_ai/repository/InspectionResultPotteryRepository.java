package com.aivle.conservation_backend.pottery_inspection_ai.repository;

import com.aivle.conservation_backend.pottery_inspection_ai.domain.InspectionResultPottery;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;
import java.util.UUID;

public interface InspectionResultPotteryRepository extends JpaRepository<InspectionResultPottery, UUID> {

    Optional<InspectionResultPottery> findFirstByAssessmentRun_IdAndAssessmentRun_ArtifactIdOrderByCreatedAtDesc(
            UUID assessmentRunId,
            UUID artifactId
    );
}
