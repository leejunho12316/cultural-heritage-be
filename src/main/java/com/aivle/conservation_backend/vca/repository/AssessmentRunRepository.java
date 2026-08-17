package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
public interface AssessmentRunRepository
        extends JpaRepository<AssessmentRun, UUID>, AssessmentRunStore {

    @Override
    AssessmentRun save(AssessmentRun entity);

    @Override
    Optional<AssessmentRun> findById(UUID id);

    @Override
    List<AssessmentRun> findByArtifactId(UUID artifactId);

    @Override
    int countByArtifactId(UUID artifactId);

    Optional<AssessmentRun> findByIdAndArtifactId(UUID id, UUID artifactId);

    List<AssessmentRun> findAllByArtifactIdOrderByRunNumberDesc(UUID artifactId);
    // 문양조사와 VCA 상태조사는 서로 별도의 AssessmentRun row를 생성하므로,
    // 특정 조사 유형의 최근 run만 조회할 수 있도록 runType 조건을 함께 사용한다.
    List<AssessmentRun> findAllByArtifactIdAndRunTypeOrderByRunNumberDesc(
        UUID artifactId,
        String runType
    );

    boolean existsByArtifactIdAndRunTypeAndStatusInAndAiRunIdIsNotNull(
        UUID artifactId,
        String runType,
        List<String> statuses
    );

    List<AssessmentRun> findAllByRunTypeAndStatusInAndAiRunIdIsNotNull(
                String runType,
                List<String> statuses
    );

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("""
            select r
            from AssessmentRun r
            where r.id = :id
              and r.artifactId = :artifactId
            """)
    Optional<AssessmentRun> findByIdAndArtifactIdForUpdate(
            @Param("id") UUID id,
            @Param("artifactId") UUID artifactId
    );

    @Query("""
            select coalesce(max(r.runNumber), 0)
            from AssessmentRun r
            where r.artifactId = :artifactId
            """)
    int findMaxRunNumberByArtifactId(
            @Param("artifactId") UUID artifactId
    );
}