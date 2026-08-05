package com.aivle.conservation_backend.vca.gateway;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.List;

@Component
public class RestClientVcaAiGateway implements VcaAiGateway {

    private final RestClient restClient;

    public RestClientVcaAiGateway(
            @Qualifier("vcaAiRestClient") RestClient restClient
    ) {
        this.restClient = restClient;
    }

    @Override
    public VcaAiAssessmentRun createAssessmentRun(
            String assessmentId,
            String projectName,
            String inputImageFolder
    ) {
        CreateAssessmentRunResponse response = restClient.post()
                .uri("/internal/vca/assessment-runs")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.APPLICATION_JSON)
                .body(new CreateAssessmentRunRequest(
                        assessmentId,
                        projectName,
                        inputImageFolder
                ))
                .retrieve()
                .body(CreateAssessmentRunResponse.class);

        return new VcaAiAssessmentRun(
                response.runId(),
                response.assessmentId(),
                response.status()
        );
    }

    @Override
    public VcaAiAssessmentStatus getAssessmentStatus(String runId) {
        AssessmentStatusResponse response = restClient.get()
                .uri("/internal/vca/assessment-runs/{runId}", runId)
                .accept(MediaType.APPLICATION_JSON)
                .retrieve()
                .body(AssessmentStatusResponse.class);

        return new VcaAiAssessmentStatus(
                response.runId(),
                response.assessmentId(),
                response.status()
        );
    }

    @Override
    public VcaAiAssessmentReport getAssessmentReport(String runId) {
        AssessmentReportResponse response = restClient.get()
                .uri("/internal/vca/assessment-runs/{runId}/report", runId)
                .accept(MediaType.APPLICATION_JSON)
                .retrieve()
                .body(AssessmentReportResponse.class);

        return new VcaAiAssessmentReport(
                response.runId(),
                response.assessmentId(),
                response.status(),
                response.summary(),
                response.findings().stream()
                        .map(finding -> new VcaAiAssessmentFinding(
                                finding.category(),
                                finding.severity(),
                                finding.message()
                        ))
                        .toList()
        );
    }

    private record CreateAssessmentRunRequest(
            String assessmentId,
            String projectName,
            String inputImageFolder
    ) {
    }

    private record CreateAssessmentRunResponse(
            String runId,
            String assessmentId,
            String status
    ) {
    }

    private record AssessmentStatusResponse(
            String runId,
            String assessmentId,
            String status
    ) {
    }

    private record AssessmentReportResponse(
            String runId,
            String assessmentId,
            String status,
            String summary,
            List<AssessmentFindingResponse> findings
    ) {
    }

    private record AssessmentFindingResponse(
            String category,
            String severity,
            String message
    ) {
    }
}
