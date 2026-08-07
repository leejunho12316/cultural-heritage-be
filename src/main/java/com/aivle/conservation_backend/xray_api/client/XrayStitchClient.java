package com.aivle.conservation_backend.xray_api.client;

import com.aivle.conservation_backend.xray_api.dto.XrayAiFinalizationRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayAiStitchRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayJobResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayJobStatusResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.time.Duration;

@Component
public class XrayStitchClient {

    private final RestClient restClient;

    public XrayStitchClient(
            RestClient.Builder restClientBuilder,
            @Value("${xray.ai.base-url}") String baseUrl,
            @Value("${xray.ai.timeout-seconds:300}") long timeoutSeconds
    ) {
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(Duration.ofSeconds(10));
        requestFactory.setReadTimeout(Duration.ofSeconds(timeoutSeconds));
        this.restClient = restClientBuilder
                .requestFactory(requestFactory)
                .baseUrl(baseUrl)
                .build();
    }

    public XrayJobResponse startStitch(XrayAiStitchRequest request) {
        return restClient.post()
                .uri("/api/stitch/jobs")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.APPLICATION_JSON)
                .body(request)
                .retrieve()
                .body(XrayJobResponse.class);
    }

    public XrayJobResponse startFinalization(XrayAiFinalizationRequest request) {
        return restClient.post()
                .uri("/api/finalize/jobs")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.APPLICATION_JSON)
                .body(request)
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
}
