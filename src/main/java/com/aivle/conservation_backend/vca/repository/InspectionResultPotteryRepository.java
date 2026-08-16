package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.InspectionResultPottery;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.UUID;

@Repository
public interface InspectionResultPotteryRepository
        extends JpaRepository<InspectionResultPottery, UUID>, InspectionResultPotteryStore {

    // InspectionResultPotteryStore.save가 CrudRepository의 제네릭 save와
    // 동시에 상속돼 그냥 두면 호출부에서 "reference to save is ambiguous"
    // 컴파일 오류가 난다 - 명시적으로 재선언해 하나로 합쳐준다.
    @Override
    InspectionResultPottery save(InspectionResultPottery entity);

    @Override
    Optional<InspectionResultPottery> findByAssessmentRunId(UUID assessmentRunId);
}
