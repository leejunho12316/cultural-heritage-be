package com.aivle.conservation_backend.conservation_guide_ai.service;

import com.aivle.conservation_backend.conservation_guide_ai.client.ConservationGuideAiClient;
import com.aivle.conservation_backend.conservation_guide_ai.domain.Task;
import com.aivle.conservation_backend.conservation_guide_ai.domain.TaskStatus;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiResponseDto;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiResumeRequestDto;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiStartRequestDto;
import com.aivle.conservation_backend.conservation_guide_ai.repository.TaskRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Map;

// start/resume 호출 시 보존 가이드 AI를 호출하고, 응답을 tasks 테이블에 반영(upsert)하는 orchestration 계층.
// 컨트롤러는 이 서비스만 알고, AI 호출 자체는 ConservationGuideAiClient가 담당한다.
@RequiredArgsConstructor
@Service
public class TaskService {

    private static final DateTimeFormatter KST_FORMAT = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssXXX");
    private static final ZoneId KST = ZoneId.of("Asia/Seoul");

    private final ConservationGuideAiClient client;
    private final TaskRepository taskRepository;

    @Transactional
    public ConservationGuideAiResponseDto startTask(String taskId, ConservationGuideAiStartRequestDto request) {
        ConservationGuideAiResponseDto response = client.startTask(taskId, request);

        String now = nowKst();
        Task task = taskRepository.findById(taskId).orElseGet(() -> Task.builder()
                .taskId(taskId)
                .createdDate(now)
                .build());

        task.setTaskName(request.taskName());
        task.setTaskManager(request.taskManager());
        task.setRelicInfo(request.relicInfo());
        task.setRelicPhoto(request.relicPhoto());
        task.setFlow(request.flow());

        applyAiResponse(task, response, now);
        taskRepository.save(task);

        return response;
    }

    @Transactional
    public ConservationGuideAiResponseDto resumeTask(String taskId, ConservationGuideAiResumeRequestDto request) {
        ConservationGuideAiResponseDto response = client.resumeTask(taskId, request);

        String now = nowKst();
        Task task = taskRepository.findById(taskId).orElseGet(() -> Task.builder()
                .taskId(taskId)
                .createdDate(now)
                .build());

        applyAiResponse(task, response, now);
        taskRepository.save(task);

        return response;
    }

    @Transactional(readOnly = true)
    public Task getTask(String taskId) {
        return taskRepository.findById(taskId)
                .orElseThrow(() -> new java.util.NoSuchElementException("task not found: " + taskId));
    }

    // AI 응답을 Task 엔티티에 반영.
    // - waiting_for_input: 진행중 상태로 표시하고, FE가 이어서 resume할 수 있도록 마지막 interrupt를 저장.
    // - completed: 최종 상태(결과/문서경로)를 저장하고 interrupt는 비운다.
    private void applyAiResponse(Task task, ConservationGuideAiResponseDto response, String now) {
        boolean completed = "completed".equals(response.status());

        if (completed) {
            task.setTotalState(TaskStatus.COMPLETED);
            task.setCurrentInterrupt(null);

            Map<String, Object> finalState = response.result();
            if (finalState != null) {
                task.setResults(castToMap(finalState.get("results")));
                task.setDocumentPath(castToMap(finalState.get("document_path")));
            }
        } else {
            task.setTotalState(TaskStatus.IN_PROGRESS);
            task.setCurrentInterrupt(response.interrupt());
        }

        task.setLastEditedDate(now);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> castToMap(Object value) {
        if (value instanceof Map<?, ?>) {
            return (Map<String, Object>) value;
        }
        return null;
    }

    private String nowKst() {
        return ZonedDateTime.now(KST).format(KST_FORMAT);
    }
}
