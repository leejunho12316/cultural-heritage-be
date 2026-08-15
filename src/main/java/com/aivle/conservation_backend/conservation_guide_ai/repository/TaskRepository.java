package com.aivle.conservation_backend.conservation_guide_ai.repository;

import com.aivle.conservation_backend.conservation_guide_ai.domain.Task;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface TaskRepository extends JpaRepository<Task, String> {

    // 유물(artifact) 기준으로 가장 최근에 시작/갱신된 작업을 찾는다.
    // taskId는 FE React Context에만 살아있어 새로고침하면 사라지므로,
    // artifactId(URL 파라미터로 항상 남아있음)로 복구할 때 쓴다.
    Optional<Task> findFirstByArtifact_IdOrderByCreatedDateDesc(UUID artifactId);

    List<Task> findAllByArtifact_Id(UUID artifactId);
}
