package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.ReportPdfJob;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
public interface ReportPdfJobRepository
        extends JpaRepository<ReportPdfJob, UUID>, ReportPdfJobStore {

    // ReportPdfJobStore.save/findById가 CrudRepository의 제네릭
    // save/findById와 동시에 상속돼 그냥 두면 호출부에서 ambiguous 컴파일
    // 오류가 난다 - 명시적으로 재선언해 하나로 합쳐준다.
    @Override
    ReportPdfJob save(ReportPdfJob entity);

    @Override
    Optional<ReportPdfJob> findById(UUID id);

    @Override
    Optional<ReportPdfJob> findByAssessmentRunId(UUID assessmentRunId);

    // artifact 삭제 캐스케이드(ArtifactService.delete)가 run 하나에 쌓인 PDF
    // job 전부를 지우는 데 쓴다 - findByAssessmentRunId는 최신 1건만 주므로 이걸로는 부족하다.
    List<ReportPdfJob> findAllByAssessmentRunId(UUID assessmentRunId);
}
