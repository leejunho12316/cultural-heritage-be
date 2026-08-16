package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.AssessmentReport;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.UUID;

@Repository
public interface AssessmentReportRepository
        extends JpaRepository<AssessmentReport, UUID>, AssessmentReportStore {

    // AssessmentReportStore.save/findById가 CrudRepository의 제네릭
    // save/findById와 동시에 상속돼 그냥 두면 호출부에서 ambiguous 컴파일
    // 오류가 난다 - 명시적으로 재선언해 하나로 합쳐준다.
    @Override
    AssessmentReport save(AssessmentReport entity);

    @Override
    Optional<AssessmentReport> findById(UUID assessmentRunId);
}
