package com.aivle.conservation_backend.report_ai.repository;

import com.aivle.conservation_backend.report_ai.domain.ReportDocument;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface ReportDocumentRepository extends JpaRepository<ReportDocument, UUID> {

    Optional<ReportDocument> findFirstByArtifact_IdOrderByCreatedAtDesc(UUID artifactId);

    List<ReportDocument> findAllByArtifact_IdOrderByCreatedAtDesc(UUID artifactId);
}
