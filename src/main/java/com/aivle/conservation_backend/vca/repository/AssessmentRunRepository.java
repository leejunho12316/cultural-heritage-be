package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
public interface AssessmentRunRepository
        extends JpaRepository<AssessmentRun, UUID>, AssessmentRunStore {

    // AssessmentRunStore.save/findById(UUID)가 CrudRepository의 제네릭
    // save/findById와 동시에 상속돼 그냥 두면 호출부에서 "reference to
    // save/findById is ambiguous" 컴파일 오류가 난다 - 명시적으로 재선언해
    // 하나로 합쳐준다(다른 메서드들과 같은 패턴).
    @Override
    AssessmentRun save(AssessmentRun entity);

    @Override
    Optional<AssessmentRun> findById(UUID id);

    @Override
    List<AssessmentRun> findByArtifactId(UUID artifactId);

    @Override
    int countByArtifactId(UUID artifactId);

    // 육안조사 결과 저장/조회 엔드포인트가 artifactId 소유권까지 같이 검증하는 데 쓴다.
    Optional<AssessmentRun> findByIdAndArtifactId(UUID id, UUID artifactId);

    // artifact 삭제 캐스케이드, report-ai의 PotterySourceAdapter(최신 run부터
    // 훑어 육안조사 결과가 있는 run을 찾음)가 쓴다.
    List<AssessmentRun> findAllByArtifactIdOrderByRunNumberDesc(UUID artifactId);
}
