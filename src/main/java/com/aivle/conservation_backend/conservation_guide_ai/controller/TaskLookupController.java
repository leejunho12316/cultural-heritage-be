package com.aivle.conservation_backend.conservation_guide_ai.controller;

import com.aivle.conservation_backend.conservation_guide_ai.dto.TaskDetailResponseDto;
import com.aivle.conservation_backend.conservation_guide_ai.service.TaskService;

import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

/**
 * 유물(artifact) 기준으로 보존 가이드 작업을 조회한다.
 *
 * {@link ConservationGuideAiController}는 taskId로만 조회하는데, taskId는
 * FE React Context(DisassemblyContext)에만 살아있어 새로고침하면 사라진다.
 * artifactId는 URL 파라미터로 항상 남아있으므로, 새로고침 후 진행상황을
 * 복구할 때는 이 엔드포인트로 최신 taskId부터 다시 얻는다.
 */
@RequiredArgsConstructor
@RestController
@RequestMapping("/api/artifacts/{artifactId}/conservation-guide-task")
public class TaskLookupController {

    private final TaskService taskService;

    @GetMapping
    public TaskDetailResponseDto latest(@PathVariable UUID artifactId) {
        return TaskDetailResponseDto.from(taskService.getLatestTaskByArtifact(artifactId));
    }
}
