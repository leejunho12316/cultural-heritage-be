package com.aivle.conservation_backend.vca.gateway;

import com.aivle.conservation_backend.vca.exception.VcaApiException;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.http.converter.HttpMessageConversionException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.util.List;
import java.util.function.Supplier;

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
        AssessmentRunResponse response = request(() -> restClient.post()
                .uri("/internal/vca/assessment-runs")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.APPLICATION_JSON)
                .body(new CreateAssessmentRunRequest(
                        assessmentId,
                        projectName,
                        inputImageFolder
                ))
                .retrieve()
                .body(AssessmentRunResponse.class));

        requireResponse(response, "create assessment run");
        requireText(response.runId(), "runId", "create assessment run");
        requireText(response.assessmentId(), "assessmentId", "create assessment run");
        requireText(response.status(), "status", "create assessment run");

        return new VcaAiAssessmentRun(
                response.runId(),
                response.assessmentId(),
                response.status()
        );
    }

    @Override
    public VcaAiAssessmentRun getAssessmentStatus(String runId) {
        AssessmentRunResponse response = request(() -> restClient.get()
                .uri("/internal/vca/assessment-runs/{runId}", runId)
                .accept(MediaType.APPLICATION_JSON)
                .retrieve()
                .body(AssessmentRunResponse.class));

        requireResponse(response, "get assessment status");
        requireText(response.runId(), "runId", "get assessment status");
        requireText(response.assessmentId(), "assessmentId", "get assessment status");
        requireText(response.status(), "status", "get assessment status");

        return new VcaAiAssessmentRun(
                response.runId(),
                response.assessmentId(),
                response.status()
        );
    }

    @Override
    public VcaAiAssessmentReport getAssessmentReport(String runId) {
        AssessmentReportResponse response = request(() -> restClient.get()
                .uri("/internal/vca/assessment-runs/{runId}/report", runId)
                .accept(MediaType.APPLICATION_JSON)
                .retrieve()
                .body(AssessmentReportResponse.class));

        requireResponse(response, "get assessment report");
        requireText(response.runId(), "runId", "get assessment report");
        requireText(response.assessmentId(), "assessmentId", "get assessment report");
        requireText(response.status(), "status", "get assessment report");
        requireText(response.summary(), "summary", "get assessment report");
        if (response.findings() == null) {
            throw invalidResponse("get assessment report", "findings must not be null");
        }

        return new VcaAiAssessmentReport(
                response.runId(),
                response.assessmentId(),
                response.status(),
                response.summary(),
                response.findings().stream()
                        .map(finding -> toFinding(finding, "get assessment report"))
                        .toList()
        );
    }

    private <T> T request(Supplier<T> action) {
        try {
            return action.get();
        } catch (VcaApiException exception) {
            throw exception;
        } catch (HttpMessageConversionException exception) {
            throw invalidResponse("VCA AI request", "response body could not be decoded");
        } catch (RestClientException exception) {
            if (hasCause(exception, HttpMessageConversionException.class)) {
                throw invalidResponse("VCA AI request", "response body could not be decoded");
            }
            throw new VcaApiException(
                    HttpStatus.BAD_GATEWAY,
                    "VCA_AI_REQUEST_FAILED",
                    "The VCA AI request failed."
            );
        }
    }

    private static VcaAiAssessmentFinding toFinding(
            AssessmentFindingPayload finding,
            String operation
    ) {
        if (finding == null) {
            throw invalidResponse(operation, "finding must not be null");
        }
        requireText(finding.category(), "finding.category", operation);
        requireText(finding.severity(), "finding.severity", operation);
        requireText(finding.message(), "finding.message", operation);
        return new VcaAiAssessmentFinding(
                finding.category(),
                finding.severity(),
                finding.message()
        );
    }

    private static void requireResponse(Object response, String operation) {
        if (response == null) {
            throw invalidResponse(operation, "response body must not be null");
        }
    }

    private static void requireText(String value, String field, String operation) {
        if (value == null || value.isBlank()) {
            throw invalidResponse(operation, field + " must not be blank");
        }
    }

    private static VcaApiException invalidResponse(String operation, String detail) {
        return new VcaApiException(
                HttpStatus.BAD_GATEWAY,
                "VCA_AI_INVALID_RESPONSE",
                "The VCA AI returned an invalid response for " + operation + ": " + detail
        );
    }

    private static boolean hasCause(Throwable exception, Class<? extends Throwable> type) {
        Throwable current = exception;
        while (current != null) {
            if (type.isInstance(current)) {
                return true;
            }
            current = current.getCause();
        }
        return false;
    }

    private record CreateAssessmentRunRequest(
            String assessmentId,
            String projectName,
            String inputImageFolder
    ) {
    }

    private record AssessmentRunResponse(
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
            List<AssessmentFindingPayload> findings
    ) {
    }

    private record AssessmentFindingPayload(
            String category,
            String severity,
            String message
    ) {
    }
}
