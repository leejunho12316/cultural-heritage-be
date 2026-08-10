package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.AssessmentReport;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.UUID;

@Repository
public interface AssessmentReportRepository
        extends JpaRepository<AssessmentReport, UUID>, AssessmentReportStore {
}
