package com.aivle.conservation_backend.conservation_guide_ai.client;

import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiResponseDto;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiResumeRequestDto;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiStartApiRequestDto;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiStartRequestDto;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

@RequiredArgsConstructor
@Service
public class ConservationGuideAiClient {
    private final RestClient conservationGuideAiRestClient;

    public ConservationGuideAiResponseDto startTask(String taskId, ConservationGuideAiStartRequestDto request){
        ConservationGuideAiStartApiRequestDto apiRequest = new ConservationGuideAiStartApiRequestDto(
                request.taskName(),
                request.taskManager(),
                request.relicInfo(),
                request.relicPhoto(),
                request.flow()
        );

        return conservationGuideAiRestClient.post()
                .uri("/tasks/{taskId}/start", taskId)
                .body(apiRequest)
                .retrieve()
                .body(ConservationGuideAiResponseDto.class);
    }

    public ConservationGuideAiResponseDto resumeTask(String taskId, ConservationGuideAiResumeRequestDto request){
        return conservationGuideAiRestClient.post()
                .uri("/tasks/{taskId}/resume", taskId)
                .body(request)
                .retrieve()
                .body(ConservationGuideAiResponseDto.class);
    }
}
