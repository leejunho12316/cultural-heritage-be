package com.aivle.conservation_backend.conservation_guide_ai;

import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

@Service
public class ConservationGuideAiClient {
    private final RestClient restClient;

    public ConservationGuideAiClient(RestClient conservationGuideAiRestClient){
        this.restClient = conservationGuideAiRestClient;
    }

    public ConservationGuideAiResponse startTask(String taskId, ConservationGuideAiStartRequest request){
        return restClient.post()
                .uri("/tasks/{taskId}/start", taskId)
                .body(request)
                .retrieve()
                .body(ConservationGuideAiResponse.class);
    }

    public ConservationGuideAiResponse resumeTask(String taskId, ConservationGuideAiResumeRequest request){
        return restClient.post()
                .uri("/tasks/{taskId}/resume", taskId)
                .body(request)
                .retrieve()
                .body(ConservationGuideAiResponse.class);
    }
}
