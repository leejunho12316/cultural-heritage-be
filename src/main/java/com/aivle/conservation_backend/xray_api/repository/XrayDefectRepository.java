package com.aivle.conservation_backend.xray_api.repository;

import com.aivle.conservation_backend.xray_api.domain.XrayDefect;
import com.aivle.conservation_backend.xray_api.domain.XrayDefectReviewDecision;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.UUID;

public interface XrayDefectRepository extends JpaRepository<XrayDefect, Long> {
    List<XrayDefect> findAllByXrayJob_IdOrderByIdAsc(UUID jobId);
    List<XrayDefect> findAllByXrayJob_IdAndReviewDecisionOrderByIdAsc(
            UUID jobId,
            XrayDefectReviewDecision reviewDecision
    );
    @Transactional
    void deleteAllByXrayJob_Id(UUID jobId);
}
