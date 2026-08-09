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

import static org.assertj.core.api.Assertions.assertThat;
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
    void passesRagArtifactsFromReportResponse() {
        server.expect(requestTo(
                        "http://vca-ai.test/internal/vca/assessment-runs/run-1/report"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess(
                        """
                                {
                                  "runId":"run-1",
                                  "assessmentId":"assessment-1",
                                  "status":"COMPLETED",
                                  "summary":"summary",
                                  "findings":[{"category":"VCA_ANOMALY","severity":"INFO","message":"message"}],
                                  "ragArtifacts":{
                                    "schema":"rag_candidate_evidence_v1",
                                    "queryCount":1,
                                    "retrievalResultCount":1,
                                    "evidenceRowCount":1,
                                    "visualConceptCardCount":1,
                                    "queries":[{"lane":"owlv2_sam2","promptText":"surface crack","queryId":"q-1"}],
                                    "retrievalResults":[{"chunkId":"chunk-1","citationId":"citation-1","lane":"owlv2_sam2","matchedTerms":["surface"],"pageNumber":null,"promptText":"surface crack","queryId":"q-1","rank":1,"score":0.86,"snippetText":"source text","sourceCitation":"source.pdf"}],
                                    "evidenceRows":[{"evidenceState":"rag_evidence_not_found","lane":"owlv2_sam2","matchedCitationIds":[],"promptText":"surface crack","queryId":null,"ragParentCandidateId":"candidate-1","topCitationId":null,"topRetrievalScore":null}],
                                    "visualConceptCards":[{"conceptCardId":"card-1","conceptFamily":null,"contextTerms":["surface"],"descriptorTerms":["line"],"materialTerms":[],"provenanceStrength":"weak","ragParentCandidateId":"candidate-1","rawRetrievedSentence":"sentence","retrievalScore":0.52,"sourceCitationIds":["citation-1"]}]
                                  }
                                }
                                """,
                        MediaType.APPLICATION_JSON
                ));

        VcaAiAssessmentReport report = gateway.getAssessmentReport("run-1");

        assertThat(report.ragArtifacts()).isNotNull();
        assertThat(report.ragArtifacts().queryCount()).isEqualTo(1);
        assertThat(report.ragArtifacts().retrievalResults().get(0).pageNumber()).isNull();
        assertThat(report.ragArtifacts().evidenceRows().get(0).queryId()).isNull();
        assertThat(report.ragArtifacts().visualConceptCards().get(0).conceptFamily()).isNull();
    }

    @Test
    void parsesFindingBboxFromReportResponse() {
        server.expect(requestTo(
                        "http://vca-ai.test/internal/vca/assessment-runs/run-1/report"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess(
                        """
                                {
                                  "runId":"run-1",
                                  "assessmentId":"assessment-1",
                                  "status":"COMPLETED",
                                  "summary":"summary",
                                  "findings":[{
                                    "category":"VCA_ANOMALY",
                                    "severity":"INFO",
                                    "message":"message",
                                    "bbox":{"xMin":10.0,"yMin":20.0,"xMax":30.0,"yMax":40.0}
                                  }]
                                }
                                """,
                        MediaType.APPLICATION_JSON
                ));

        VcaAiAssessmentReport report = gateway.getAssessmentReport("run-1");

        VcaAiAssessmentFinding.Bbox bbox = report.findings().get(0).bbox();
        assertThat(bbox).isNotNull();
        assertThat(bbox.xMin()).isEqualTo(10.0);
        assertThat(bbox.yMin()).isEqualTo(20.0);
        assertThat(bbox.xMax()).isEqualTo(30.0);
        assertThat(bbox.yMax()).isEqualTo(40.0);
    }

    @Test
    void treatsMissingFindingBboxAsNull() {
        server.expect(requestTo(
                        "http://vca-ai.test/internal/vca/assessment-runs/run-1/report"))
                .andExpect(method(HttpMethod.GET))
                .andRespond(withSuccess(
                        "{\"runId\":\"run-1\",\"assessmentId\":\"assessment-1\","
                                + "\"status\":\"COMPLETED\",\"summary\":\"summary\","
                                + "\"findings\":[{\"category\":\"VCA_ANOMALY\",\"severity\":\"INFO\","
                                + "\"message\":\"message\"}]}",
                        MediaType.APPLICATION_JSON
                ));

        VcaAiAssessmentReport report = gateway.getAssessmentReport("run-1");

        assertThat(report.findings().get(0).bbox()).isNull();
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
