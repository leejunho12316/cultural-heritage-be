package com.aivle.conservation_backend.conservation_guide_ai.controller;

import com.aivle.conservation_backend.conservation_guide_ai.client.ConservationGuideAiClient;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiResponseDto;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiResumeRequestDto;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiStartRequestDto;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

@RequiredArgsConstructor
@RestController
@RequestMapping("/tasks/{taskId}")
public class ConservationGuideAiController {

    private final ConservationGuideAiClient client;

    @PostMapping("/start")
    public ConservationGuideAiResponseDto start(@PathVariable String taskId,
                                                @RequestBody ConservationGuideAiStartRequestDto request){
        return client.startTask(taskId, request);
    }

    @PostMapping("/resume")
    public ConservationGuideAiResponseDto resume(@PathVariable String taskId,
                                                 @RequestBody ConservationGuideAiResumeRequestDto request){
        return client.resumeTask(taskId, request);
    }

}
