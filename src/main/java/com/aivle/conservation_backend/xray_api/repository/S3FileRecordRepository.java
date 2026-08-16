package com.aivle.conservation_backend.xray_api.repository;

import com.aivle.conservation_backend.xray_api.domain.S3FileRecord;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.UUID;

public interface S3FileRecordRepository extends JpaRepository<S3FileRecord, UUID> {
    List<S3FileRecord> findAllByArtifactId(UUID artifactId);

    List<S3FileRecord> findAllByArtifactIdAndModuleTypeAndUsageNameOrderBySourceOrderAsc(
            UUID artifactId,
            String moduleType,
            String usageName
    );
}
