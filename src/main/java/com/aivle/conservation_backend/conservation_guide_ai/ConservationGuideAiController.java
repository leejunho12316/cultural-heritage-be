package com.aivle.conservation_backend.conservation_guide_ai;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/tasks/{taskId}")
public class ConservationGuideAiController {

    private final ConservationGuideAiClient client;

    public ConservationGuideAiController(ConservationGuideAiClient client){
        this.client = client;
    }

    @PostMapping("/start")
    public ConservationGuideAiResponse start(@PathVariable String taskId,
                                             @RequestBody ConservationGuideAiStartRequest request){
        return client.startTask(taskId, request);
    }

    @PostMapping("/resume")
    public ConservationGuideAiResponse resume(@PathVariable String taskId,
                                              @RequestBody ConservationGuideAiResumeRequest request){
        return client.resumeTask(taskId, request);
    }

}
