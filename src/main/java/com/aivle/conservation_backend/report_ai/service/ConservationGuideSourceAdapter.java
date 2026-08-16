package com.aivle.conservation_backend.report_ai.service;

import com.aivle.conservation_backend.conservation_guide_ai.domain.Task;
import com.aivle.conservation_backend.conservation_guide_ai.repository.TaskRepository;

import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * 보존가이드(conservation-guide-ai)가 실제로 DB(RDS)에 저장한 결과
 * (tasks.artifact_id -&gt; tasks.results)를, report-ai가 기대하는
 * GenerateReportRequestDto.guideResult(= State.guide_result) 형태로
 * 변환한다.
 *
 * report-ai의 guide_stage 노드(app/nodes/guide_stage.py)는
 * guide_result를 {stage_key: {status, ...}} 형태로 기대하고
 * 단계별로 그 키를 꺼내 쓴다 - Task.results가 정확히 이 모양이다
 * (TaskService.applyAiResponse가 완료 시 AI 응답의 results를 그대로
 * 저장한다). 그래서 이 어댑터는 별도 변환 없이 Task.results를 그대로
 * 넘기기만 한다.
 *
 * 다른 어댑터들과 동일하게, artifactId가 UUID 형식이 아니거나 매칭되는
 * 작업이 없거나 아직 어떤 단계도 완료되지 않았으면(results가 비어있으면)
 * 예외 대신 빈 값을 반환한다 - 이 유물은 보존가이드를 아직 시작 안
 * 했거나 진행 중일 수 있다.
 */
@Service
public class ConservationGuideSourceAdapter {

    private final TaskRepository taskRepository;

    public ConservationGuideSourceAdapter(TaskRepository taskRepository) {
        this.taskRepository = taskRepository;
    }

    public Optional<Map<String, Object>> resolve(String artifactId) {
        UUID uuid;
        try {
            uuid = UUID.fromString(artifactId);
        } catch (IllegalArgumentException e) {
            return Optional.empty();
        }

        return taskRepository.findFirstByArtifact_IdOrderByCreatedDateDesc(uuid)
                .map(Task::getResults)
                .filter(results -> results != null && !results.isEmpty());
    }
}
