package com.aivle.conservation_backend.vca.gateway;

import com.aivle.conservation_backend.vca.exception.VcaApiException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.http.converter.HttpMessageConversionException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.util.List;
import java.util.Map;
import java.util.function.Supplier;

@Component
public class RestClientVcaAiGateway implements VcaAiGateway {

    private static final Logger log = LoggerFactory.getLogger(RestClientVcaAiGateway.class);

    private final RestClient restClient;

    public RestClientVcaAiGateway(
            @Qualifier("vcaAiRestClient") RestClient restClient
    ) {
        this.restClient = restClient;
    }

    // vca-ai에 POST /assessment-runs 호출. VcaService.createAiAssessmentRun에서 사용.
    @Override
    public VcaAiAssessmentRun createAssessmentRun(
            String assessmentId,
            String projectName,
            String inputImageFolder,
            List<InputImageUrl> inputImageUrls,
            String resumeFromProjectName
    ) {
        AssessmentRunResponse response = request(() -> restClient.post()
                .uri("/internal/vca/assessment-runs")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.APPLICATION_JSON)
                .body(new CreateAssessmentRunRequest(
                        assessmentId,
                        projectName,
                        inputImageFolder,
                        inputImageUrls == null ? List.of() : inputImageUrls.stream()
                                .map(image -> new InputImageUrlPayload(image.fileName(), image.downloadUrl().toString()))
                                .toList(),
                        resumeFromProjectName
                ))
                .retrieve()
                .body(AssessmentRunResponse.class));

        requireResponse(response, "create assessment run");
        requireText(response.runId(), "runId", "create assessment run");
        requireText(response.assessmentId(), "assessmentId", "create assessment run");
        requireText(response.status(), "status", "create assessment run");

        return toAssessmentRun(response);
    }

    // run 상태 폴링. VcaService.syncRunWithAi가 진행 상황을 갱신할 때마다 호출한다.
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

        return toAssessmentRun(response);
    }

    // "분석 중지" 요청을 vca-ai에 전달. VcaService.cancelRun에서 호출된다.
    @Override
    public VcaAiAssessmentRun cancelAssessmentRun(String runId) {
        AssessmentRunResponse response = request(() -> restClient.post()
                .uri("/internal/vca/assessment-runs/{runId}/cancel", runId)
                .accept(MediaType.APPLICATION_JSON)
                .retrieve()
                .body(AssessmentRunResponse.class));

        requireResponse(response, "cancel assessment run");
        requireText(response.runId(), "runId", "cancel assessment run");
        requireText(response.assessmentId(), "assessmentId", "cancel assessment run");
        requireText(response.status(), "status", "cancel assessment run");

        return toAssessmentRun(response);
    }

    // 내부 응답 레코드 -> gateway 도메인 타입(VcaAiAssessmentRun) 변환 공통 헬퍼.
    private static VcaAiAssessmentRun toAssessmentRun(AssessmentRunResponse response) {
        return new VcaAiAssessmentRun(
                response.runId(),
                response.assessmentId(),
                response.status(),
                response.currentStage(),
                response.currentStageProgress(),
                response.stages() == null ? List.of() : response.stages(),
                response.failureReason()
        );
    }

    // 완료된 run의 리포트(요약/findings/RAG 근거)를 조회. VcaService.getReport/ensureReportReady에서 호출.
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
                        .toList(),
                response.ragArtifacts()
        );
    }

    // 리포트 하단 "시스템 환경 정보" 조회. VcaService.getSystemInfo에서 사용.
    // health와 마찬가지로 /internal/vca 접두어 없이 vca-ai 최상위 경로다.
    @Override
    public VcaAiSystemInfo getSystemInfo() {
        SystemInfoResponse response = request(() -> restClient.get()
                .uri("/system-info")
                .accept(MediaType.APPLICATION_JSON)
                .retrieve()
                .body(SystemInfoResponse.class));

        requireResponse(response, "get system info");

        List<VcaAiSystemInfo.Model> models = response.models() == null
                ? List.of()
                : response.models().stream()
                        .filter(model -> model != null)
                        .map(model -> new VcaAiSystemInfo.Model(model.key(), model.repoId(), model.revision()))
                        .toList();
        return new VcaAiSystemInfo(
                response.os(),
                response.pythonVersion(),
                response.device(),
                response.libraries() == null ? Map.of() : response.libraries(),
                models
        );
    }

    // 모든 vca-ai 호출이 거치는 공통 에러 변환 래퍼: 통신/디코딩 실패를 VcaApiException(502)으로 통일한다.
    private <T> T request(Supplier<T> action) {
        try {
            return action.get();
        } catch (VcaApiException exception) {
            throw exception;
        } catch (HttpMessageConversionException exception) {
            throw invalidResponse("VCA AI request", "response body could not be decoded");
        } catch (RestClientException exception) {
            // RestClient는 디코딩 실패를 HttpMessageConversionException으로 바로 던지지 않고
            // RestClientException으로 감싸서 던지는 경우가 있어, cause 체인까지 확인해야 한다.
            if (hasCause(exception, HttpMessageConversionException.class)) {
                throw invalidResponse("VCA AI request", "response body could not be decoded");
            }
            log.error("VCA AI request failed", exception);
            throw new VcaApiException(
                    HttpStatus.BAD_GATEWAY,
                    "VCA_AI_REQUEST_FAILED",
                    "The VCA AI request failed."
            );
        }
    }

    // finding 페이로드 필수 필드를 검증하며 gateway 도메인 타입으로 변환.
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
                finding.message(),
                finding.imageId(),
                finding.conceptFamily(),
                finding.descriptor(),
                toFindingCitations(finding.citations()),
                toFindingBbox(finding.bbox()),
                toFindingPolygons(finding.polygons())
        );
    }

    private static List<VcaAiAssessmentFinding.Citation> toFindingCitations(
            List<AssessmentFindingCitationPayload> citations
    ) {
        if (citations == null) {
            return List.of();
        }
        return citations.stream()
                .filter(citation -> citation != null)
                .map(citation -> new VcaAiAssessmentFinding.Citation(
                        citation.citationId(),
                        citation.sourceCitation(),
                        citation.pageNumber()
                ))
                .toList();
    }

    private static VcaAiAssessmentFinding.Bbox toFindingBbox(AssessmentFindingBboxPayload bbox) {
        if (bbox == null) {
            return null;
        }
        return new VcaAiAssessmentFinding.Bbox(
                bbox.xMin(),
                bbox.yMin(),
                bbox.xMax(),
                bbox.yMax()
        );
    }

    // vca-ai(Pydantic tuple[tuple[tuple[float,float], ...], ...])는 폴리곤 점을
    // {"x":..,"y":..} 객체가 아니라 [x, y] 2요소 배열로, 폴리곤 자체는 여러
    // 개(마스크의 분리된 조각마다 하나씩)를 배열로 직렬화한다 - 여기서 그 실제
    // 배열 모양 그대로 파싱해야 한다(객체로 파싱하려 하면
    // HttpMessageConversionException).
    private static List<List<VcaAiAssessmentFinding.Point>> toFindingPolygons(
            List<List<List<Double>>> polygons
    ) {
        if (polygons == null) {
            return null;
        }
        return polygons.stream()
                .filter(polygon -> polygon != null)
                .map(RestClientVcaAiGateway::toFindingPolygon)
                .toList();
    }

    private static List<VcaAiAssessmentFinding.Point> toFindingPolygon(
            List<List<Double>> polygon
    ) {
        return polygon.stream()
                .filter(point -> point != null && point.size() == 2)
                .map(point -> new VcaAiAssessmentFinding.Point(point.get(0), point.get(1)))
                .toList();
    }

    // vca-ai는 별도의 Python 서비스라 응답 구조가 컴파일 타임에 보장되지 않는다.
    // 그래서 필수 필드를 여기서 명시적으로 검증해, 나중에 getter에서 NPE가 터지는 대신
    // VCA_AI_INVALID_RESPONSE 502로 변환해 던진다.
    private static void requireResponse(Object response, String operation) {
        if (response == null) {
            throw invalidResponse(operation, "response body must not be null");
        }
    }

    // requireResponse와 함께 vca-ai 응답의 문자열 필수 필드를 검증하는 공통 헬퍼.
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
            String inputImageFolder,
            List<InputImageUrlPayload> inputImageUrls,
            String resumeFromProjectName
    ) {
    }

    private record InputImageUrlPayload(
            String fileName,
            String url
    ) {
    }

    private record AssessmentRunResponse(
            String runId,
            String assessmentId,
            String status,
            String currentStage,
            VcaAiAssessmentStageProgress currentStageProgress,
            List<VcaAiAssessmentStage> stages,
            String failureReason
    ) {
    }

    private record AssessmentReportResponse(
            String runId,
            String assessmentId,
            String status,
            String summary,
            List<AssessmentFindingPayload> findings,
            VcaAiAssessmentReport.RagArtifacts ragArtifacts
    ) {
    }

    private record AssessmentFindingPayload(
            String category,
            String severity,
            String message,
            String imageId,
            String conceptFamily,
            String descriptor,
            List<AssessmentFindingCitationPayload> citations,
            AssessmentFindingBboxPayload bbox,
            List<List<List<Double>>> polygons
    ) {
    }

    private record AssessmentFindingCitationPayload(
            String citationId,
            String sourceCitation,
            Integer pageNumber
    ) {
    }

    private record AssessmentFindingBboxPayload(
            Double xMin,
            Double yMin,
            Double xMax,
            Double yMax
    ) {
    }

    private record SystemInfoResponse(
            String os,
            String pythonVersion,
            String device,
            Map<String, String> libraries,
            List<SystemInfoModelPayload> models
    ) {
    }

    private record SystemInfoModelPayload(
            String key,
            String repoId,
            String revision
    ) {
    }

}
