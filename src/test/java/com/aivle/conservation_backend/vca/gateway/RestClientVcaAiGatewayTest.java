package com.aivle.conservation_backend.vca.gateway;

import com.aivle.conservation_backend.vca.exception.VcaApiException;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withServerError;

class RestClientVcaAiGatewayTest {

    private MockRestServiceServer server;
    private RestClientVcaAiGateway gateway;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder()
                .baseUrl("http://vca-ai.test");
        server = MockRestServiceServer.bindTo(builder).build();
        gateway = new RestClientVcaAiGateway(builder.build());
    }

    @AfterEach
    void verifyRequests() {
        server.verify();
    }

    @Test
    void rejectsNullCreateResponseAsBadGateway() {
        server.expect(requestTo("http://vca-ai.test/internal/vca/assessment-runs"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withSuccess("", MediaType.APPLICATION_JSON));

        assertBadGateway(
                () -> gateway.createAssessmentRun("assessment-1", "project-1", "/input"),
                "VCA_AI_INVALID_RESPONSE"
        );
    }

    @Test
    void rejectsInvalidStatusResponseAsBadGateway() {
        server.expect(requestTo("http://vca-ai.test/internal/vca/assessment-runs/run-1"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess(
                        "{\"runId\":\"\",\"assessmentId\":\"assessment-1\",\"status\":\"COMPLETED\"}",
                        MediaType.APPLICATION_JSON
                ));

        assertBadGateway(
                () -> gateway.getAssessmentStatus("run-1"),
                "VCA_AI_INVALID_RESPONSE"
        );
    }

    @Test
    void rejectsNullStatusResponseAsBadGateway() {
        server.expect(requestTo("http://vca-ai.test/internal/vca/assessment-runs/run-1"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess("", MediaType.APPLICATION_JSON));

        assertBadGateway(
                () -> gateway.getAssessmentStatus("run-1"),
                "VCA_AI_INVALID_RESPONSE"
        );
    }

    @Test
    void rejectsInvalidCreateResponseAsBadGateway() {
        server.expect(requestTo("http://vca-ai.test/internal/vca/assessment-runs"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withSuccess(
                        "{\"runId\":\"run-1\",\"assessmentId\":null,\"status\":\"COMPLETED\"}",
                        MediaType.APPLICATION_JSON
                ));

        assertBadGateway(
                () -> gateway.createAssessmentRun("assessment-1", "project-1", "/input"),
                "VCA_AI_INVALID_RESPONSE"
        );
    }

    @Test
    void translatesAiHttpFailureToBadGatewayRequestError() {
        server.expect(requestTo("http://vca-ai.test/internal/vca/assessment-runs"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withServerError());

        assertBadGateway(
                () -> gateway.createAssessmentRun("assessment-1", "project-1", "/input"),
                "VCA_AI_REQUEST_FAILED"
        );
    }

    @Test
    void rejectsNullReportResponseAsBadGateway() {
        server.expect(requestTo(
                        "http://vca-ai.test/internal/vca/assessment-runs/run-1/report"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess("", MediaType.APPLICATION_JSON));

        assertBadGateway(
                () -> gateway.getAssessmentReport("run-1"),
                "VCA_AI_INVALID_RESPONSE"
        );
    }

    @Test
    void rejectsNullFindingsInReportResponseAsBadGateway() {
        server.expect(requestTo(
                        "http://vca-ai.test/internal/vca/assessment-runs/run-1/report"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess(
                        "{\"runId\":\"run-1\",\"assessmentId\":\"assessment-1\","
                                + "\"status\":\"COMPLETED\",\"summary\":\"summary\","
                                + "\"findings\":null}",
                        MediaType.APPLICATION_JSON
                ));

        assertBadGateway(
                () -> gateway.getAssessmentReport("run-1"),
                "VCA_AI_INVALID_RESPONSE"
        );
    }

    @Test
    void rejectsInvalidFindingFieldsInReportResponseAsBadGateway() {
        server.expect(requestTo(
                        "http://vca-ai.test/internal/vca/assessment-runs/run-1/report"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess(
                        "{\"runId\":\"run-1\",\"assessmentId\":\"assessment-1\","
                                + "\"status\":\"COMPLETED\",\"summary\":\"summary\","
                                + "\"findings\":[{\"category\":null,\"severity\":\"INFO\","
                                + "\"message\":\"message\"}]}",
                        MediaType.APPLICATION_JSON
                ));

        assertBadGateway(
                () -> gateway.getAssessmentReport("run-1"),
                "VCA_AI_INVALID_RESPONSE"
        );
    }

    private static void assertBadGateway(
            org.assertj.core.api.ThrowableAssert.ThrowingCallable action,
            String expectedCode
    ) {
        assertThatThrownBy(action)
                .isInstanceOfSatisfying(VcaApiException.class, exception -> {
                    org.assertj.core.api.Assertions.assertThat(exception.status())
                            .isEqualTo(HttpStatus.BAD_GATEWAY);
                    org.assertj.core.api.Assertions.assertThat(exception.code())
                            .isEqualTo(expectedCode);
                });
    }
}
