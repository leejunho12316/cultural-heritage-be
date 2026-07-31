package com.aivle.conservation_backend.conservation_guide_ai.controller;

import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiResponseDto;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiResumeRequestDto;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiStartRequestDto;
import com.aivle.conservation_backend.conservation_guide_ai.dto.TaskDetailResponseDto;
import com.aivle.conservation_backend.conservation_guide_ai.service.TaskService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.NoSuchElementException;

@RequiredArgsConstructor
@RestController
@RequestMapping("/tasks/{taskId}")
public class ConservationGuideAiController {

    private final TaskService taskService;

    @PostMapping("/start")
    public ConservationGuideAiResponseDto start(@PathVariable String taskId,
                                                @RequestBody ConservationGuideAiStartRequestDto request){
        return taskService.startTask(taskId, request);
    }

    @PostMapping("/resume")
    public ConservationGuideAiResponseDto resume(@PathVariable String taskId,
                                                 @RequestBody ConservationGuideAiResumeRequestDto request){
        return taskService.resumeTask(taskId, request);
    }

    // 저장된 Task 상태 조회 (재접속 시 진행상황/완료결과 복구용)
    @GetMapping
    public TaskDetailResponseDto get(@PathVariable String taskId){
        return TaskDetailResponseDto.from(taskService.getTask(taskId));
    }

    @ExceptionHandler(NoSuchElementException.class)
    @ResponseStatus(HttpStatus.NOT_FOUND)
    public String handleNotFound(NoSuchElementException ex){
        return ex.getMessage();
    }

}
