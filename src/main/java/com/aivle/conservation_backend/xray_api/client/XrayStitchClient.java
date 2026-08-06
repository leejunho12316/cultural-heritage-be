package com.aivle.conservation_backend.xray_api.client;

import com.aivle.conservation_backend.xray_api.dto.XrayAiJobRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayJobResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayJobStatusResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;

@Component
public class XrayStitchClient {

    private final RestClient restClient;
    private final ObjectMapper objectMapper;

    public XrayStitchClient(
            RestClient.Builder restClientBuilder,
            ObjectMapper objectMapper,
            @Value("${xray.ai.base-url}") String baseUrl,
            @Value("${xray.ai.timeout-seconds:300}") long timeoutSeconds
    ) {
        SimpleClientHttpRequestFactory requestFactory =
                new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(Duration.ofSeconds(10));
        requestFactory.setReadTimeout(Duration.ofSeconds(timeoutSeconds));

        this.restClient = restClientBuilder
                .requestFactory(requestFactory)
                .baseUrl(baseUrl)
                .build();
        this.objectMapper = objectMapper;
    }

    public XrayJobResponse createJob(XrayAiJobRequest request) {
        final String jsonBody;

        try {
            jsonBody = objectMapper.writeValueAsString(request);
        } catch (JacksonException e) {
            throw new IllegalStateException(
                    "Failed to serialize FastAPI job request.",
                    e
            );
        }

        return restClient.post()
                .uri("/api/stitch/jobs")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.APPLICATION_JSON)
                .body(jsonBody)
                .retrieve()
                .body(XrayJobResponse.class);
    }

    public XrayJobStatusResponse getJobStatus(String jobId) {
        return restClient.get()
                .uri("/api/jobs/{jobId}", jobId)
                .accept(MediaType.APPLICATION_JSON)
                .retrieve()
                .body(XrayJobStatusResponse.class);
    }

    public void finalizeJob(String jobId) {
        restClient.post()
                .uri("/api/jobs/{jobId}/finalize", jobId)
                .accept(MediaType.APPLICATION_JSON)
                .retrieve()
                .toBodilessEntity();
    }
}
