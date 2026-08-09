package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.pottery_inspection_ai.client.PotteryInspectionAiClient;
import com.aivle.conservation_backend.pottery_inspection_ai.dto.PotteryInspectionResponseDto;
import com.jayway.jsonpath.JsonPath;
import com.aivle.conservation_backend.vca.config.VcaAccessTokenInterceptor;
import com.aivle.conservation_backend.vca.controller.VcaController;
import com.aivle.conservation_backend.vca.dto.ArtifactDetailResponse;
import com.aivle.conservation_backend.vca.dto.RunResponse;
import com.aivle.conservation_backend.vca.exception.VcaApiException;
import com.aivle.conservation_backend.vca.exception.VcaExceptionHandler;
import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentFinding;
import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentReport;
import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentRun;
import com.aivle.conservation_backend.vca.gateway.VcaAiGateway;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.validation.beanvalidation.LocalValidatorFactoryBean;

import java.nio.file.Files;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.net.URI;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.options;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class VcaControllerTest {

    private static final String SHA256 =
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    private static final byte[] JPEG_BYTES = new byte[]{
            (byte) 0xFF, (byte) 0xD8, (byte) 0xFF, 0x00
    };
    private static final byte[] PNG_BYTES = new byte[]{
            (byte) 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A
    };
    private static final byte[] PDF_BYTES = "%PDF-1.7\nVCA corpus".getBytes(StandardCharsets.UTF_8);

    @TempDir
    private Path tempDirectory;

    private LocalValidatorFactoryBean validator;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        validator = new LocalValidatorFactoryBean();
        validator.afterPropertiesSet();
        mockMvc = mvc(new VcaService(true));
    }

    @AfterEach
    void tearDown() {
        validator.close();
    }

    private MockMvc mvc(VcaService service) {
        return MockMvcBuilders
                .standaloneSetup(new VcaController(service))
                .setControllerAdvice(new VcaExceptionHandler())
                .setValidator(validator)
                .build();
    }

    private MockMvc securedMvc(VcaService service, String token) {
        return MockMvcBuilders
                .standaloneSetup(new VcaController(service))
                .addInterceptors(new VcaAccessTokenInterceptor(token))
                .setControllerAdvice(new VcaExceptionHandler())
                .setValidator(validator)
                .build();
    }

    private static final class StaticVcaAiGateway implements VcaAiGateway {

        @Override
        public VcaAiAssessmentRun createAssessmentRun(
                String assessmentId,
                String projectName,
                String inputImageFolder
        ) {
            return new VcaAiAssessmentRun("vca-ai-" + assessmentId, assessmentId, "RUNNING");
        }

        @Override
        public VcaAiAssessmentRun getAssessmentStatus(String runId) {
            return new VcaAiAssessmentRun(runId, runId.replace("vca-ai-", ""), "COMPLETED");
        }

        @Override
        public VcaAiAssessmentRun cancelAssessmentRun(String runId) {
            return new VcaAiAssessmentRun(
                    runId, runId.replace("vca-ai-", ""), "FAILED", null, List.of(), "cancelled by user"
            );
        }

        @Override
        public VcaAiAssessmentReport getAssessmentReport(String runId) {
            return new VcaAiAssessmentReport(
                    runId,
                    runId.replace("vca-ai-", ""),
                    "COMPLETED",
                    "Gateway generated VCA report.",
                    List.of(new VcaAiAssessmentFinding(
                            "VCA_ANOMALY",
                            "INFO",
                            "Gateway finding propagated.",
                            "image-001",
                            "crack",
                            "line surface",
                            List.of(),
                            new VcaAiAssessmentFinding.Bbox(10.0, 20.0, 30.0, 40.0),
                            null
                    )),
                    null
            );
        }
    }

    private static final class FakeVcaImageStorage implements VcaImageStorage {

        private final Path root;
        private boolean uploadVerified;
        private boolean runInputMaterialized;

        private FakeVcaImageStorage(Path root) {
            this.root = root;
        }

        @Override
        public PresignedUpload presignUpload(
                String artifactId,
                String imageId,
                com.aivle.conservation_backend.vca.dto.PresignImageRequest request,
                Instant expiresAt
        ) {
            String objectKey = objectKey(artifactId, imageId, request.fileName());
            return new PresignedUpload(
                    objectKey,
                    URI.create("http://localhost:9000/conservation-local/" + objectKey),
                    Map.of(
                            "Content-Type", request.contentType(),
                            "x-amz-meta-sha256", request.sha256()
                    )
            );
        }

        @Override
        public StoredImage storeUpload(String artifactId, String imageId, org.springframework.web.multipart.MultipartFile file) {
            String objectKey = objectKey(artifactId, imageId, file.getOriginalFilename());
            try {
                Path objectPath = root.resolve(objectKey);
                Files.createDirectories(objectPath.getParent());
                Files.write(objectPath, file.getBytes());
                return new StoredImage(
                        file.getOriginalFilename(),
                        file.getContentType(),
                        file.getSize(),
                        sha256(file.getBytes()),
                        objectKey
                );
            } catch (java.io.IOException exception) {
                throw new IllegalStateException(exception);
            }
        }

        @Override
        public void verifyUpload(String objectKey, long expectedSizeBytes, String expectedSha256) {
            uploadVerified = true;
        }

        @Override
        public URI presignedDownload(String objectKey) {
            return URI.create("http://localhost:9000/conservation-local/" + objectKey + "?X-Amz-Signature=test");
        }

        @Override
        public VcaSharedStorage.RunInputDirectory materializeRunInput(
                String assessmentRunId,
                List<StoredImageReference> images
        ) {
            try {
                Path inputDirectory = root.resolve(assessmentRunId).resolve("input");
                Files.createDirectories(inputDirectory);
                for (StoredImageReference image : images) {
                    Files.writeString(inputDirectory.resolve(image.imageId() + "-" + image.fileName()), image.objectKey());
                }
                runInputMaterialized = true;
                return new VcaSharedStorage.RunInputDirectory("/shared/vca/" + assessmentRunId + "/input");
            } catch (java.io.IOException exception) {
                throw new IllegalStateException(exception);
            }
        }

        @Override
        public StoredImageContent read(String objectKey, String fileName, String contentType) {
            try {
                return new StoredImageContent(fileName, contentType, Files.readAllBytes(root.resolve(objectKey)));
            } catch (java.io.IOException exception) {
                throw new IllegalStateException(exception);
            }
        }

        @Override
        public void delete(String objectKey) {
            try {
                Files.deleteIfExists(root.resolve(objectKey));
            } catch (java.io.IOException exception) {
                throw new IllegalStateException(exception);
            }
        }

        private static String objectKey(String artifactId, String imageId, String fileName) {
            return "vca/images/" + artifactId + "/" + imageId + "/" + fileName;
        }

        private static String sha256(byte[] bytes) {
            try {
                return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
            } catch (NoSuchAlgorithmException exception) {
                throw new IllegalStateException(exception);
            }
        }
    }

    private static final class FakePotteryInspectionAiClient extends PotteryInspectionAiClient {

        private final AtomicInteger calls = new AtomicInteger();
        private String inspectedFileName;

        private FakePotteryInspectionAiClient() {
            super(null);
        }

        @Override
        public PotteryInspectionResponseDto inspect(
                org.springframework.web.multipart.MultipartFile image,
                int nCalls,
                boolean useVlmPattern
        ) {
            calls.incrementAndGet();
            inspectedFileName = image.getOriginalFilename();
            return new PotteryInspectionResponseDto(
                    "pottery-test-v1",
                    "도자기 문양 검사 결과입니다.",
                    "도자기 문양 요약",
                    true,
                    Map.of("pattern", "cloud")
            );
        }
    }

    private static final class FailingPotteryInspectionAiClient extends PotteryInspectionAiClient {

        private FailingPotteryInspectionAiClient() {
            super(null);
        }

        @Override
        public PotteryInspectionResponseDto inspect(
                org.springframework.web.multipart.MultipartFile image,
                int nCalls,
                boolean useVlmPattern
        ) {
            throw new IllegalStateException("pottery service unavailable");
        }
    }

    @Test
    void supportsUploadRunReportAndPdfLifecycle() throws Exception {
        MvcResult presignResult = mockMvc.perform(post("/api/vca/test-artifact/images/presign")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "fileName": "front.jpg",
                                  "contentType": "image/jpeg",
                                  "sizeBytes": 2048,
                                  "sha256": "%s"
                                }
                                """.formatted(SHA256)))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.imageId").isString())
                .andExpect(jsonPath("$.uploadUrl").value(
                        org.hamcrest.Matchers.startsWith("https://vca-local.invalid/uploads/")))
                .andExpect(jsonPath("$.method").value("PUT"))
                .andExpect(jsonPath("$.uploadMode").value("DIRECT_COMPLETE"))
                .andExpect(jsonPath("$.requiredHeaders['Content-Type']").value("image/jpeg"))
                .andExpect(jsonPath("$.expiresAt").isString())
                .andReturn();
        String imageId = JsonPath.read(
                presignResult.getResponse().getContentAsString(),
                "$.imageId"
        );

        mockMvc.perform(post("/api/vca/test-artifact/images/{imageId}/complete", imageId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"sha256":"%s"}
                                """.formatted(SHA256)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("UPLOADED"))
                .andExpect(jsonPath("$.imageUrl").value(
                        "/api/vca/test-artifact/files/sha256/" + SHA256));

        MvcResult runResult = mockMvc.perform(post("/api/vca/test-artifact/runs"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("QUEUED"))
                .andExpect(jsonPath("$.imageCount").value(1))
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );

        mockMvc.perform(post("/api/vca/test-artifact/runs"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("ACTIVE_RUN_EXISTS"));

        MvcResult reportResult = mockMvc.perform(get(
                        "/api/vca/test-artifact/runs/{assessmentRunId}/report",
                        assessmentRunId
                ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.assessmentRunId").value(assessmentRunId))
                .andExpect(jsonPath("$.summary").exists())
                .andExpect(jsonPath("$.findings").isArray())
                .andExpect(jsonPath("$.recommendations").isArray())
                .andExpect(jsonPath("$.images").isArray())
                .andExpect(jsonPath("$.status").value("COMPLETED"))
                .andExpect(jsonPath("$.summary.headline").value("VCA 육안 조사 결과"))
                .andExpect(jsonPath("$.findings[0].severity").value("MEDIUM"))
                .andExpect(jsonPath("$.recommendations[0].priority").value("HIGH"))
                .andExpect(jsonPath("$.recommendations[0].title").value("동일 조건 재촬영"))
                .andReturn();
        assertThat(reportResult.getResponse().getContentAsString())
                .doesNotContain(
                        "reportJson",
                        "report_json",
                        "assessment_report",
                        "report_summary",
                        "report_finding",
                        "report_recommendation",
                        "placeholder",
                        "Connect production VCA pipeline"
                );

        MvcResult pdfResult = mockMvc.perform(post(
                        "/api/vca/test-artifact/runs/{assessmentRunId}/report/pdf",
                        assessmentRunId
                ))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("QUEUED"))
                .andReturn();
        String jobId = JsonPath.read(
                pdfResult.getResponse().getContentAsString(),
                "$.jobId"
        );

        mockMvc.perform(post(
                        "/api/vca/test-artifact/runs/{assessmentRunId}/report/pdf",
                        assessmentRunId
                ))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.jobId").value(jobId))
                .andExpect(jsonPath("$.status").value("QUEUED"));

        mockMvc.perform(get("/api/vca/test-artifact/report-pdf-jobs/{jobId}/download", jobId))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("PDF_NOT_READY"));

        mockMvc.perform(get("/api/vca/test-artifact/report-pdf-jobs/{jobId}", jobId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("COMPLETED"))
                .andExpect(jsonPath("$.downloadUrl").value(
                        "/api/vca/test-artifact/report-pdf-jobs/" + jobId + "/download"));

        mockMvc.perform(get("/api/vca/test-artifact/report-pdf-jobs/{jobId}/download", jobId))
                .andExpect(status().isSeeOther())
                .andExpect(header().string(
                        "Location",
                        org.hamcrest.Matchers.startsWith("https://vca-local.invalid/downloads/")
                ));

        mockMvc.perform(get("/api/vca/test-artifact/files/sha256/{sha256}", SHA256))
                .andExpect(status().isSeeOther())
                .andExpect(header().string(
                        "Location",
                        org.hamcrest.Matchers.startsWith("https://vca-local.invalid/files/")
                ));

        mockMvc.perform(get("/api/vca"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items").isArray())
                .andExpect(jsonPath("$.items[?(@.artifactId == 'test-artifact')]").exists());

        MvcResult artifactResult = mockMvc.perform(get("/api/vca/test-artifact"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.artifactId").value("test-artifact"))
                .andExpect(jsonPath("$.uploadedImages[0].imageId").value(imageId))
                .andReturn();
        String artifactJson = artifactResult.getResponse().getContentAsString();
        assertThat(artifactJson)
                .doesNotContain(
                        "\"sha256\"",
                        "objectKey",
                        "storageKey",
                        "filesystem",
                        "databaseId",
                        "configPath"
                );
    }

    @Test
    void rejectsDirectCompleteWhenLocalUploadModeIsNotEnabled() throws Exception {
        MockMvc productionMvc = mvc(new VcaService(false));
        MvcResult presignResult = productionMvc.perform(post("/api/vca/production-artifact/images/presign")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "fileName": "front.jpg",
                                  "contentType": "image/jpeg",
                                  "sizeBytes": 2048,
                                  "sha256": "%s"
                                }
                                """.formatted(SHA256)))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.uploadMode").value("SIGNED_PUT"))
                .andReturn();
        String imageId = JsonPath.read(
                presignResult.getResponse().getContentAsString(),
                "$.imageId"
        );

        productionMvc.perform(post("/api/vca/production-artifact/images/{imageId}/complete", imageId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"sha256":"%s"}
                                """.formatted(SHA256)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("UPLOAD_NOT_VERIFIED"));
    }

    @Test
    void usesObjectStorageForSignedVcaUploadDownloadAndRunInput() throws Exception {
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        FakeVcaImageStorage imageStorage = new FakeVcaImageStorage(tempDirectory.resolve("objects"));
        MockMvc productionMvc = mvc(new VcaService(
                false,
                new StaticVcaAiGateway(),
                sharedStorage,
                imageStorage
        ));

        MvcResult presignResult = productionMvc.perform(post("/api/vca/s3-artifact/images/presign")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "fileName": "front.jpg",
                                  "contentType": "image/jpeg",
                                  "sizeBytes": 2048,
                                  "sha256": "%s"
                                }
                                """.formatted(SHA256)))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.uploadMode").value("SIGNED_PUT"))
                .andExpect(jsonPath("$.uploadUrl").value(org.hamcrest.Matchers.startsWith(
                        "http://localhost:9000/conservation-local/vca/images/s3-artifact/")))
                .andExpect(jsonPath("$.requiredHeaders['x-amz-meta-sha256']").value(SHA256))
                .andReturn();
        String imageId = JsonPath.read(presignResult.getResponse().getContentAsString(), "$.imageId");

        productionMvc.perform(post("/api/vca/s3-artifact/images/{imageId}/complete", imageId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"sha256":"%s"}
                                """.formatted(SHA256)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("UPLOADED"));
        assertThat(imageStorage.uploadVerified).isTrue();

        productionMvc.perform(get("/api/vca/s3-artifact/files/sha256/{sha256}", SHA256))
                .andExpect(status().isSeeOther())
                .andExpect(header().string(
                        "Location",
                        org.hamcrest.Matchers.startsWith("http://localhost:9000/conservation-local/vca/images/")
                ));

        productionMvc.perform(post("/api/vca/s3-artifact/runs"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("RUNNING"));
        assertThat(imageStorage.runInputMaterialized).isTrue();
    }

    @Test
    void enforcesConfiguredVcaAccessToken() throws Exception {
        MockMvc secured = securedMvc(new VcaService(true), "test-token");

        secured.perform(get("/api/vca/demo-artifact"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("VCA_UNAUTHORIZED"));

        secured.perform(get("/api/vca/demo-artifact")
                        .header("X-VCA-Access-Token", "wrong-token"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("VCA_UNAUTHORIZED"));

        secured.perform(get("/api/vca/demo-artifact")
                        .header("X-VCA-Access-Token", "test-token"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.artifactId").value("demo-artifact"));

        secured.perform(options("/api/vca/demo-artifact"))
                .andExpect(status().isOk());
    }

    @Test
    void rejectsBlankConfiguredVcaAccessToken() {
        assertThatThrownBy(() -> new VcaAccessTokenInterceptor("  "))
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("VCA access token must be configured.");
    }

    @Test
    void requiresAccessTokenForMultipartUploadAndRunCreation() throws Exception {
        MockMvc secured = securedMvc(new VcaService(true), "test-token");
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "front.jpg",
                "image/jpeg",
                JPEG_BYTES
        );

        secured.perform(multipart("/api/vca/demo-artifact/images").file(file))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("VCA_UNAUTHORIZED"));

        secured.perform(post("/api/vca/demo-artifact/runs"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("VCA_UNAUTHORIZED"));
    }

    @Test
    void acceptsQueryTokenForBrowserManagedMediaRequests() throws Exception {
        VcaService service = new VcaService(true);
        MockMvc secured = securedMvc(service, "test-token");
        MvcResult presignResult = secured.perform(post("/api/vca/demo-artifact/images/presign")
                        .header("X-VCA-Access-Token", "test-token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "fileName": "front.jpg",
                                  "contentType": "image/jpeg",
                                  "sizeBytes": 2048,
                                  "sha256": "%s"
                                }
                                """.formatted(SHA256)))
                .andExpect(status().isCreated())
                .andReturn();
        String imageId = JsonPath.read(
                presignResult.getResponse().getContentAsString(),
                "$.imageId"
        );

        secured.perform(post("/api/vca/demo-artifact/images/{imageId}/complete", imageId)
                        .header("X-VCA-Access-Token", "test-token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"sha256":"%s"}
                                """.formatted(SHA256)))
                .andExpect(status().isOk());

        secured.perform(get("/api/vca/demo-artifact/files/sha256/{sha256}", SHA256)
                        .queryParam("vca_access_token", "test-token"))
                .andExpect(status().isSeeOther());
    }

    @Test
    void cancelsQueuedDemoRunAndReportsFailedWithReason() throws Exception {
        MvcResult presignResult = mockMvc.perform(post("/api/vca/cancel-demo-artifact/images/presign")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "fileName": "front.jpg",
                                  "contentType": "image/jpeg",
                                  "sizeBytes": 2048,
                                  "sha256": "%s"
                                }
                                """.formatted(SHA256)))
                .andExpect(status().isCreated())
                .andReturn();
        String imageId = JsonPath.read(
                presignResult.getResponse().getContentAsString(),
                "$.imageId"
        );
        mockMvc.perform(post("/api/vca/cancel-demo-artifact/images/{imageId}/complete", imageId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"sha256":"%s"}
                                """.formatted(SHA256)))
                .andExpect(status().isOk());

        MvcResult runResult = mockMvc.perform(post("/api/vca/cancel-demo-artifact/runs"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("QUEUED"))
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );

        mockMvc.perform(post("/api/vca/cancel-demo-artifact/runs/{assessmentRunId}/cancel", assessmentRunId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"))
                .andExpect(jsonPath("$.failureReason").value("사용자가 분석을 중지했습니다."));

        // A second stop click on an already-terminal run is a harmless no-op.
        mockMvc.perform(post("/api/vca/cancel-demo-artifact/runs/{assessmentRunId}/cancel", assessmentRunId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"));
    }

    @Test
    void cancelsRunThroughGatewayAndAppliesReturnedStatus() throws Exception {
        VcaAiGateway gateway = new VcaAiGateway() {
            @Override
            public VcaAiAssessmentRun createAssessmentRun(
                    String assessmentId,
                    String projectName,
                    String inputImageFolder
            ) {
                return new VcaAiAssessmentRun("vca-ai-" + assessmentId, assessmentId, "RUNNING");
            }

            @Override
            public VcaAiAssessmentRun getAssessmentStatus(String runId) {
                return new VcaAiAssessmentRun(runId, runId.replace("vca-ai-", ""), "RUNNING");
            }

            @Override
            public VcaAiAssessmentRun cancelAssessmentRun(String runId) {
                return new VcaAiAssessmentRun(
                        runId, runId.replace("vca-ai-", ""), "FAILED", null, List.of(), "cancelled by user"
                );
            }

            @Override
            public VcaAiAssessmentReport getAssessmentReport(String runId) {
                throw new AssertionError("Report must not be fetched while cancelling a run.");
            }
        };
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        MockMvc gatewayMvc = mvc(new VcaService(true, gateway, sharedStorage));

        gatewayMvc.perform(multipart("/api/vca/cancel-gateway-artifact/images").file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/cancel-gateway-artifact/runs"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("RUNNING"))
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );

        gatewayMvc.perform(post("/api/vca/cancel-gateway-artifact/runs/{assessmentRunId}/cancel", assessmentRunId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"))
                .andExpect(jsonPath("$.failureReason").value("cancelled by user"));
    }

    @Test
    void returnsDraftArtifactForFirstVcaEntry() throws Exception {
        mockMvc.perform(get("/api/vca/new-workspace-artifact"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.artifactId").value("new-workspace-artifact"))
                .andExpect(jsonPath("$.displayName").value("Artifact new-workspace-artifact"))
                .andExpect(jsonPath("$.status").value("DRAFT"))
                .andExpect(jsonPath("$.uploadedImages").isEmpty())
                .andExpect(jsonPath("$.runs").isEmpty())
                .andExpect(jsonPath("$.createdAt").isString())
                .andExpect(jsonPath("$.updatedAt").isString());
    }

    @Test
    void advancesDemoRunStatusThroughArtifactPolling() throws Exception {
        MvcResult presignResult = mockMvc.perform(post("/api/vca/polling-artifact/images/presign")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "fileName": "front.jpg",
                                  "contentType": "image/jpeg",
                                  "sizeBytes": 2048,
                                  "sha256": "%s"
                                }
                                """.formatted(SHA256)))
                .andExpect(status().isCreated())
                .andReturn();
        String imageId = JsonPath.read(
                presignResult.getResponse().getContentAsString(),
                "$.imageId"
        );

        mockMvc.perform(post("/api/vca/polling-artifact/images/{imageId}/complete", imageId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"sha256":"%s"}
                                """.formatted(SHA256)))
                .andExpect(status().isOk());

        MvcResult runResult = mockMvc.perform(post("/api/vca/polling-artifact/runs"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("QUEUED"))
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );

        Thread.sleep(1_000);
        mockMvc.perform(get("/api/vca/polling-artifact"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.runs[0].assessmentRunId").value(assessmentRunId))
                .andExpect(jsonPath("$.runs[0].status").value("RUNNING"))
                .andExpect(jsonPath("$.status").value("ANALYZING"));

        Thread.sleep(2_100);
        mockMvc.perform(get("/api/vca/polling-artifact"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.runs[0].assessmentRunId").value(assessmentRunId))
                .andExpect(jsonPath("$.runs[0].status").value("COMPLETED"))
                .andExpect(jsonPath("$.runs[0].completedAt").isString())
                .andExpect(jsonPath("$.status").value("ASSESSED"));
    }

    @Test
    void usesVcaAiGatewayWhenSpringInjectedServiceCreatesRun() throws Exception {
        AtomicReference<String> assessmentId = new AtomicReference<>();
        AtomicReference<String> projectName = new AtomicReference<>();
        AtomicReference<String> inputImageFolder = new AtomicReference<>();
        VcaAiGateway gateway = new VcaAiGateway() {
            @Override
            public VcaAiAssessmentRun createAssessmentRun(
                    String requestedAssessmentId,
                    String requestedProjectName,
                    String requestedInputImageFolder
            ) {
                assessmentId.set(requestedAssessmentId);
                projectName.set(requestedProjectName);
                inputImageFolder.set(requestedInputImageFolder);
                return new VcaAiAssessmentRun(
                        "vca-ai-" + requestedAssessmentId,
                        requestedAssessmentId,
                        "RUNNING"
                );
            }

            @Override
            public VcaAiAssessmentRun getAssessmentStatus(String runId) {
                return new VcaAiAssessmentRun(runId, runId.replace("vca-ai-", ""), "COMPLETED");
            }

            @Override
            public VcaAiAssessmentRun cancelAssessmentRun(String runId) {
                return new VcaAiAssessmentRun(
                        runId, runId.replace("vca-ai-", ""), "FAILED", null, List.of(), "cancelled by user"
                );
            }

            @Override
            public VcaAiAssessmentReport getAssessmentReport(String runId) {
                return new VcaAiAssessmentReport(
                        runId,
                        runId.replace("vca-ai-", ""),
                        "COMPLETED",
                        "Gateway generated VCA report.",
                        List.of(new VcaAiAssessmentFinding(
                                "VCA_ANOMALY",
                                "INFO",
                                "Gateway finding propagated.",
                                "image-001",
                                "crack",
                                "line surface",
                                List.of(),
                                new VcaAiAssessmentFinding.Bbox(10.0, 20.0, 30.0, 40.0),
                                null
                        )),
                        sampleRagArtifacts()
                );
            }
        };
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        MockMvc gatewayMvc = mvc(new VcaService(true, gateway, sharedStorage));

        MockMultipartFile file = new MockMultipartFile(
                "file",
                "front.jpg",
                "image/jpeg",
                JPEG_BYTES
        );
        MvcResult uploadResult = gatewayMvc.perform(multipart("/api/vca/gateway-artifact/images")
                        .file(file))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.status").value("UPLOADED"))
                .andReturn();
        String imageId = JsonPath.read(
                uploadResult.getResponse().getContentAsString(),
                "$.imageId"
        );
        assertThat(uploadResult.getResponse().getContentAsString())
                .doesNotContain("/shared/vca", "inputImageFolder", tempDirectory.toString());

        MvcResult runResult = gatewayMvc.perform(post("/api/vca/gateway-artifact/runs"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("RUNNING"))
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );
        assertThat(assessmentId.get()).isEqualTo(assessmentRunId);
        assertThat(projectName.get()).isEqualTo("gateway-artifact-" + assessmentRunId);
        assertThat(inputImageFolder.get()).isEqualTo("/shared/vca/" + assessmentRunId + "/input");
        assertThat(Files.list(tempDirectory.resolve(assessmentRunId).resolve("input")).toList())
                .hasSize(1)
                .anySatisfy(path -> assertThat(path.getFileName().toString()).contains(imageId));

        gatewayMvc.perform(get("/api/vca/gateway-artifact"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.runs[0].status").value("COMPLETED"));

        gatewayMvc.perform(get(
                        "/api/vca/gateway-artifact/runs/{assessmentRunId}/report",
                        assessmentRunId
                ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("COMPLETED"))
                .andExpect(jsonPath("$.summary.headline").value("VCA 육안 조사 결과"))
                .andExpect(jsonPath("$.summary.description").value("Gateway generated VCA report."))
                .andExpect(jsonPath("$.findings[0].category").value("VCA_ANOMALY"))
                .andExpect(jsonPath("$.findings[0].conceptFamily").value("crack"))
                .andExpect(jsonPath("$.findings[0].descriptor").value("line surface"))
                .andExpect(jsonPath("$.findings[0].bbox.xMin").value(10.0))
                .andExpect(jsonPath("$.findings[0].bbox.yMax").value(40.0))
                .andExpect(jsonPath("$.recommendations[0].title").value("crack"))
                .andExpect(jsonPath("$.findings[0].description").value("Gateway finding propagated."))
                .andExpect(jsonPath("$.ragArtifacts.schema").value("rag_candidate_evidence_v1"))
                .andExpect(jsonPath("$.ragArtifacts.queryCount").value(1))
                .andExpect(jsonPath("$.ragArtifacts.retrievalResults[0].pageNumber").isEmpty())
                .andExpect(jsonPath("$.ragArtifacts.evidenceRows[0].queryId").isEmpty())
                .andExpect(jsonPath("$.ragArtifacts.visualConceptCards[0].conceptFamily").isEmpty());
    }

    private static VcaAiAssessmentReport.RagArtifacts sampleRagArtifacts() {
        return new VcaAiAssessmentReport.RagArtifacts(
                "rag_candidate_evidence_v1",
                1,
                1,
                1,
                1,
                List.of(new VcaAiAssessmentReport.RagQuery(
                        "owlv2_sam2",
                        "surface crack",
                        "q-1"
                )),
                List.of(new VcaAiAssessmentReport.RagRetrievalResult(
                        "chunk-1",
                        "citation-1",
                        "owlv2_sam2",
                        List.of("surface"),
                        null,
                        "surface crack",
                        "q-1",
                        1,
                        0.86,
                        "source text",
                        "source.pdf"
                )),
                List.of(new VcaAiAssessmentReport.RagEvidenceRow(
                        "rag_evidence_not_found",
                        "owlv2_sam2",
                        List.of(),
                        "surface crack",
                        null,
                        "candidate-1",
                        null,
                        null
                )),
                List.of(new VcaAiAssessmentReport.RagVisualConceptCard(
                        "card-1",
                        null,
                        List.of("surface"),
                        List.of("line"),
                        List.of(),
                        "weak",
                        "candidate-1",
                        "sentence",
                        0.52,
                        List.of("citation-1")
                ))
        );
    }

    @Test
    void runsPotteryInspectionFromReportOnlyForPotteryMaterial() throws Exception {
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        FakePotteryInspectionAiClient potteryClient = new FakePotteryInspectionAiClient();
        MockMvc gatewayMvc = mvc(new VcaService(
                true,
                new StaticVcaAiGateway(),
                sharedStorage,
                potteryClient
        ));

        gatewayMvc.perform(multipart("/api/vca/pottery-artifact/images").file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());
        MvcResult potteryRun = gatewayMvc.perform(post("/api/vca/pottery-artifact/runs")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"material":"도자기"}
                                """))
                .andExpect(status().isAccepted())
                .andReturn();
        String potteryRunId = JsonPath.read(potteryRun.getResponse().getContentAsString(), "$.assessmentRunId");

        gatewayMvc.perform(get(
                        "/api/vca/pottery-artifact/runs/{assessmentRunId}/report",
                        potteryRunId
                ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.summary.headline").value("VCA 육안 조사 결과"))
                .andExpect(jsonPath("$.potteryInspection").isEmpty())
                .andExpect(jsonPath("$.potteryInspectionStatus.applicable").value(true))
                .andExpect(jsonPath("$.potteryInspectionStatus.status").value("NOT_STARTED"));
        assertThat(potteryClient.calls.get()).isZero();

        gatewayMvc.perform(post("/api/vca/pottery-artifact/runs/{assessmentRunId}/pottery-inspection", potteryRunId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"material":"도자기"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.summary.headline").value("VCA 육안 조사 결과"))
                .andExpect(jsonPath("$.potteryInspection.moduleVersion").value("pottery-test-v1"))
                .andExpect(jsonPath("$.potteryInspection.summary").value("도자기 문양 요약"))
                .andExpect(jsonPath("$.potteryInspection.humanReviewRecommended").value(true))
                .andExpect(jsonPath("$.potteryInspectionStatus.applicable").value(true))
                .andExpect(jsonPath("$.potteryInspectionStatus.status").value("COMPLETED"))
                .andExpect(jsonPath("$.potteryInspectionStatus.retryable").value(true));
        assertThat(potteryClient.calls.get()).isEqualTo(1);
        assertThat(potteryClient.inspectedFileName).isEqualTo("front.jpg");

        gatewayMvc.perform(multipart("/api/vca/bronze-artifact/images").file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());
        MvcResult bronzeRun = gatewayMvc.perform(post("/api/vca/bronze-artifact/runs")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"material":"청동"}
                                """))
                .andExpect(status().isAccepted())
                .andReturn();
        String bronzeRunId = JsonPath.read(bronzeRun.getResponse().getContentAsString(), "$.assessmentRunId");

        gatewayMvc.perform(get(
                        "/api/vca/bronze-artifact/runs/{assessmentRunId}/report",
                        bronzeRunId
                ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.summary.headline").value("VCA 육안 조사 결과"))
                .andExpect(jsonPath("$.potteryInspection").isEmpty());

        gatewayMvc.perform(post("/api/vca/bronze-artifact/runs/{assessmentRunId}/pottery-inspection", bronzeRunId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"material":"청동"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.summary.headline").value("VCA 육안 조사 결과"))
                .andExpect(jsonPath("$.potteryInspection").isEmpty())
                .andExpect(jsonPath("$.potteryInspectionStatus.applicable").value(false))
                .andExpect(jsonPath("$.potteryInspectionStatus.retryable").value(false));
        assertThat(potteryClient.calls.get()).isEqualTo(1);
    }

    @Test
    void potteryInspectionFailureLeavesVcaReportAvailableAndRetryable() throws Exception {
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        MockMvc gatewayMvc = mvc(new VcaService(
                true,
                new StaticVcaAiGateway(),
                sharedStorage,
                new FailingPotteryInspectionAiClient()
        ));

        gatewayMvc.perform(multipart("/api/vca/pottery-failure-artifact/images").file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/pottery-failure-artifact/runs")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"material":"도자기"}
                                """))
                .andExpect(status().isAccepted())
                .andReturn();
        String runId = JsonPath.read(runResult.getResponse().getContentAsString(), "$.assessmentRunId");

        gatewayMvc.perform(post("/api/vca/pottery-failure-artifact/runs/{assessmentRunId}/pottery-inspection", runId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"material":"도자기"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.summary.headline").value("VCA 육안 조사 결과"))
                .andExpect(jsonPath("$.potteryInspection").isEmpty())
                .andExpect(jsonPath("$.potteryInspectionStatus.applicable").value(true))
                .andExpect(jsonPath("$.potteryInspectionStatus.status").value("FAILED"))
                .andExpect(jsonPath("$.potteryInspectionStatus.retryable").value(true))
                .andExpect(jsonPath("$.potteryInspectionStatus.failureMessage").value("pottery service unavailable"));

        gatewayMvc.perform(get(
                        "/api/vca/pottery-failure-artifact/runs/{assessmentRunId}/report",
                        runId
                ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.summary.headline").value("VCA 육안 조사 결과"))
                .andExpect(jsonPath("$.potteryInspectionStatus.status").value("FAILED"));
    }

    @Test
    void createsPdfAfterSyncingAiStatusWithoutPriorReportOrArtifactPoll() throws Exception {
        VcaAiGateway gateway = new VcaAiGateway() {
            @Override
            public VcaAiAssessmentRun createAssessmentRun(
                    String assessmentId,
                    String projectName,
                    String inputImageFolder
            ) {
                return new VcaAiAssessmentRun("vca-ai-" + assessmentId, assessmentId, "RUNNING");
            }

            @Override
            public VcaAiAssessmentRun getAssessmentStatus(String runId) {
                return new VcaAiAssessmentRun(
                        runId,
                        runId.replace("vca-ai-", ""),
                        "COMPLETED"
                );
            }

            @Override
            public VcaAiAssessmentRun cancelAssessmentRun(String runId) {
                throw new AssertionError("Cancel must not be called while creating a PDF job.");
            }

            @Override
            public VcaAiAssessmentReport getAssessmentReport(String runId) {
                throw new AssertionError("Report must not be fetched while creating a PDF job.");
            }
        };
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        MockMvc gatewayMvc = mvc(new VcaService(true, gateway, sharedStorage));

        gatewayMvc.perform(multipart("/api/vca/pdf-sync-artifact/images").file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/pdf-sync-artifact/runs"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("RUNNING"))
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );

        gatewayMvc.perform(post(
                        "/api/vca/pdf-sync-artifact/runs/{assessmentRunId}/report/pdf",
                        assessmentRunId
                ))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("QUEUED"));
    }

    @Test
    void keepsArtifactReadsAvailableWhileAiRunCreationIsInFlight() throws Exception {
        CountDownLatch gatewayEntered = new CountDownLatch(1);
        CountDownLatch releaseGateway = new CountDownLatch(1);
        VcaAiGateway gateway = new VcaAiGateway() {
            @Override
            public VcaAiAssessmentRun createAssessmentRun(
                    String assessmentId,
                    String projectName,
                    String inputImageFolder
            ) {
                gatewayEntered.countDown();
                try {
                    assertThat(releaseGateway.await(5, TimeUnit.SECONDS)).isTrue();
                } catch (InterruptedException exception) {
                    Thread.currentThread().interrupt();
                    throw new IllegalStateException(exception);
                }
                return new VcaAiAssessmentRun("vca-ai-" + assessmentId, assessmentId, "RUNNING");
            }

            @Override
            public VcaAiAssessmentRun getAssessmentStatus(String runId) {
                return new VcaAiAssessmentRun(runId, runId.replace("vca-ai-", ""), "RUNNING");
            }

            @Override
            public VcaAiAssessmentRun cancelAssessmentRun(String runId) {
                throw new AssertionError("Cancel must not be called while creating a run.");
            }

            @Override
            public VcaAiAssessmentReport getAssessmentReport(String runId) {
                throw new AssertionError("Report must not be fetched while creating a run.");
            }
        };
        VcaService service = new VcaService(
                true,
                gateway,
                new VcaSharedStorage(tempDirectory.toString(), "/shared/vca")
        );
        service.uploadImage(
                "nonblocking-artifact",
                new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)
        );
        ExecutorService executor = Executors.newFixedThreadPool(2);

        try {
            Future<RunResponse> runFuture = executor.submit(() -> service.createRun("nonblocking-artifact"));
            assertThat(gatewayEntered.await(5, TimeUnit.SECONDS)).isTrue();

            Future<ArtifactDetailResponse> detailFuture = executor.submit(
                    () -> service.getArtifact("nonblocking-artifact")
            );
            ArtifactDetailResponse detail = detailFuture.get(1, TimeUnit.SECONDS);
            assertThat(detail.runs()).hasSize(1);
            assertThat(detail.runs().get(0).status()).isEqualTo("QUEUED");

            releaseGateway.countDown();
            assertThat(runFuture.get(5, TimeUnit.SECONDS).status()).isEqualTo("RUNNING");
        } finally {
            releaseGateway.countDown();
            executor.shutdownNow();
        }
    }

    @Test
    void rejectsMultipartUploadWhenImageBytesDoNotMatchDeclaredContentType() throws Exception {
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        MockMvc gatewayMvc = mvc(new VcaService(true, new StaticVcaAiGateway(), sharedStorage));
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "fake.png",
                "image/png",
                "not an image".getBytes()
        );

        gatewayMvc.perform(multipart("/api/vca/gateway-artifact/images").file(file))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("VALIDATION_ERROR"));
    }

    @Test
    void reportFindingImageIdIsTranslatedFromEngineSha256BackToTheUploadUuid() throws Exception {
        // vca-ai only ever knows an image by its content sha256 (see
        // vca_artifacts.py's engine image_id -> sha256 translation, which
        // vca_v2 itself never changes since it must stay standalone-CLI
        // capable). The FE must only ever see Spring's own upload uuid - the
        // same id every other VCA endpoint (image delete/complete, the
        // images[] array itself) uses - so Spring translates sha256 back to
        // that uuid here. sha256 is purely internal vca-ai<->Spring plumbing;
        // it must never reach the FE-facing JSON at all.
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        String expectedSha256 = sha256(JPEG_BYTES);
        VcaAiGateway gateway = new VcaAiGateway() {
            @Override
            public VcaAiAssessmentRun createAssessmentRun(
                    String assessmentId, String projectName, String inputImageFolder
            ) {
                return new VcaAiAssessmentRun("vca-ai-" + assessmentId, assessmentId, "RUNNING");
            }

            @Override
            public VcaAiAssessmentRun getAssessmentStatus(String runId) {
                return new VcaAiAssessmentRun(runId, runId.replace("vca-ai-", ""), "COMPLETED");
            }

            @Override
            public VcaAiAssessmentRun cancelAssessmentRun(String runId) {
                throw new AssertionError("Cancel must not be called while reading a report.");
            }

            @Override
            public VcaAiAssessmentReport getAssessmentReport(String runId) {
                return new VcaAiAssessmentReport(
                        runId,
                        runId.replace("vca-ai-", ""),
                        "COMPLETED",
                        "Gateway generated VCA report.",
                        List.of(new VcaAiAssessmentFinding(
                                "VCA_ANOMALY",
                                "INFO",
                                "Gateway finding propagated.",
                                expectedSha256,
                                "crack",
                                "line surface",
                                List.of(),
                                null,
                                null
                        )),
                        null
                );
            }
        };
        MockMvc gatewayMvc = mvc(new VcaService(true, gateway, sharedStorage));
        MockMultipartFile file = new MockMultipartFile(
                "file", "front.jpg", "image/jpeg", JPEG_BYTES
        );

        MvcResult uploadResult = gatewayMvc.perform(multipart("/api/vca/report-image-id-artifact/images").file(file))
                .andExpect(status().isCreated())
                .andReturn();
        String uploadedImageId = JsonPath.read(
                uploadResult.getResponse().getContentAsString(),
                "$.imageId"
        );
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/report-image-id-artifact/runs"))
                .andExpect(status().isAccepted())
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );

        gatewayMvc.perform(get(
                        "/api/vca/report-image-id-artifact/runs/{assessmentRunId}/report",
                        assessmentRunId
                ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.images[0].imageId").value(uploadedImageId))
                .andExpect(jsonPath("$.findings[0].imageId").value(uploadedImageId));
    }

    private static String sha256(byte[] bytes) throws NoSuchAlgorithmException {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        return HexFormat.of().formatHex(digest.digest(bytes));
    }

    @Test
    void deletesStoredUploadDirectoryWhenImageIsDeleted() throws Exception {
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        MockMvc gatewayMvc = mvc(new VcaService(true, new StaticVcaAiGateway(), sharedStorage));
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "detail.png",
                "image/png",
                PNG_BYTES
        );
        MvcResult uploadResult = gatewayMvc.perform(multipart("/api/vca/delete-storage-artifact/images")
                        .file(file))
                .andExpect(status().isCreated())
                .andReturn();
        String imageId = JsonPath.read(
                uploadResult.getResponse().getContentAsString(),
                "$.imageId"
        );

        gatewayMvc.perform(delete("/api/vca/delete-storage-artifact/images/{imageId}", imageId))
                .andExpect(status().isNoContent());

        assertThat(tempDirectory.resolve("uploads").resolve(imageId)).doesNotExist();
    }

    @Test
    void rejectsDeletingAnImageReferencedByAnExistingRun() throws Exception {
        // Before the Postgres persistence migration, a run's uploaded-images
        // list held direct object references captured at creation time, so
        // deleting an image afterward never affected an already-created run.
        // Now a run only durably stores image UUIDs and re-resolves them from
        // the image store on demand (report/pottery-inspection generation),
        // so deleting a still-referenced image must be refused up front
        // instead of silently breaking those reads later.
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        MockMvc gatewayMvc = mvc(new VcaService(true, new StaticVcaAiGateway(), sharedStorage));
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "detail.png",
                "image/png",
                PNG_BYTES
        );
        MvcResult uploadResult = gatewayMvc.perform(multipart("/api/vca/image-delete-guard-artifact/images")
                        .file(file))
                .andExpect(status().isCreated())
                .andReturn();
        String imageId = JsonPath.read(
                uploadResult.getResponse().getContentAsString(),
                "$.imageId"
        );
        gatewayMvc.perform(post("/api/vca/image-delete-guard-artifact/runs"))
                .andExpect(status().isAccepted());

        gatewayMvc.perform(delete("/api/vca/image-delete-guard-artifact/images/{imageId}", imageId))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("IMAGE_REFERENCED_BY_RUN"));
    }

    @Test
    void reconcilesStuckQueuedRunLeftByAnUncaughtCrashDuringRunCreation() {
        // Given: createRun's catch only handles RuntimeException (a normal,
        // already-handled gateway failure - it rolls back via
        // cancelRunReservation). If the process is killed outright between
        // reserveRun committing a QUEUED row and the AI call returning, no
        // catch block ever runs. An Error (not a RuntimeException) models
        // that: it propagates straight out of createRun uncaught, exactly
        // like a crash would, leaving the QUEUED/aiRunId=null row behind.
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        VcaAiGateway crashingGateway = new VcaAiGateway() {
            @Override
            public VcaAiAssessmentRun createAssessmentRun(
                    String assessmentId, String projectName, String inputImageFolder
            ) {
                throw new OutOfMemoryError("simulated process crash");
            }

            @Override
            public VcaAiAssessmentRun getAssessmentStatus(String runId) {
                throw new UnsupportedOperationException();
            }

            @Override
            public VcaAiAssessmentRun cancelAssessmentRun(String runId) {
                throw new UnsupportedOperationException();
            }

            @Override
            public VcaAiAssessmentReport getAssessmentReport(String runId) {
                throw new UnsupportedOperationException();
            }
        };
        VcaService service = new VcaService(true, crashingGateway, sharedStorage);
        MockMultipartFile file = new MockMultipartFile("file", "detail.png", "image/png", PNG_BYTES);
        service.uploadImage("stuck-run-artifact", file);

        // When: run creation crashes uncaught partway through.
        assertThatThrownBy(() -> service.createRun("stuck-run-artifact"))
                .isInstanceOf(OutOfMemoryError.class);

        // Then: the QUEUED reservation survives and blocks every subsequent
        // run creation attempt for this artifact - the actual bug.
        ArtifactDetailResponse beforeReconcile = service.getArtifact("stuck-run-artifact");
        assertThat(beforeReconcile.runs()).hasSize(1);
        assertThat(beforeReconcile.runs().get(0).status()).isEqualTo("QUEUED");
        assertThatThrownBy(() -> service.createRun("stuck-run-artifact"))
                .isInstanceOf(VcaApiException.class)
                .hasMessageContaining("already queued or running");

        // When: the startup reconciler runs, as it would on the next boot.
        service.reconcileStuckRunsOnStartup();

        // Then: the stuck run is marked FAILED and no longer blocks new runs.
        ArtifactDetailResponse afterReconcile = service.getArtifact("stuck-run-artifact");
        assertThat(afterReconcile.runs().get(0).status()).isEqualTo("FAILED");
    }

    @Test
    void exposesSafeIntermediateResultsForCompletedRun() throws Exception {
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        Path outputRoot = tempDirectory.resolve("engine-output");
        VcaIntermediateResultStorage intermediateStorage =
                new VcaIntermediateResultStorage(outputRoot.toString());
        MockMvc gatewayMvc = mvc(new VcaService(
                true,
                new StaticVcaAiGateway(),
                sharedStorage,
                intermediateStorage
        ));
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "front.jpg",
                "image/jpeg",
                JPEG_BYTES
        );
        gatewayMvc.perform(multipart("/api/vca/intermediate-artifact/images").file(file))
                .andExpect(status().isCreated());
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/intermediate-artifact/runs"))
                .andExpect(status().isAccepted())
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );
        String projectName = "intermediate-artifact-" + assessmentRunId;
        Path manifest = outputRoot.resolve("preprocessing").resolve(projectName).resolve("manifest.json");
        Files.createDirectories(manifest.getParent());
        Files.writeString(manifest, "{\"stage\":\"preprocessing\"}");
        Path receipt = outputRoot.resolve("result").resolve(projectName).resolve("receipts").resolve("startup.json");
        Files.createDirectories(receipt.getParent());
        Files.writeString(receipt, "{\"status\":\"completed\"}");

        MvcResult result = gatewayMvc.perform(get(
                        "/api/vca/intermediate-artifact/runs/{assessmentRunId}/intermediate-results",
                        assessmentRunId
                ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.artifactId").value("intermediate-artifact"))
                .andExpect(jsonPath("$.assessmentRunId").value(assessmentRunId))
                .andExpect(jsonPath("$.projectName").value(projectName))
                .andExpect(jsonPath("$.stages[0].stage").value("preprocessing"))
                .andExpect(jsonPath("$.stages[0].items[0].relativePath").value("manifest.json"))
                .andExpect(jsonPath("$.stages[0].items[0].preview").value("{\"stage\":\"preprocessing\"}"))
                .andExpect(jsonPath("$.stages[1].stage").value("result"))
                .andExpect(jsonPath("$.stages[1].items[0].relativePath").value("receipts/startup.json"))
                .andReturn();

        assertThat(result.getResponse().getContentAsString())
                .doesNotContain(tempDirectory.toString(), "/shared", "/vca_v2");
    }

    @Test
    void boundsIntermediatePreviewToConfiguredPreviewBytes() throws Exception {
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        Path outputRoot = tempDirectory.resolve("engine-output");
        VcaIntermediateResultStorage intermediateStorage =
                new VcaIntermediateResultStorage(outputRoot.toString());
        MockMvc gatewayMvc = mvc(new VcaService(
                true,
                new StaticVcaAiGateway(),
                sharedStorage,
                intermediateStorage
        ));
        gatewayMvc.perform(multipart("/api/vca/preview-artifact/images").file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/preview-artifact/runs"))
                .andExpect(status().isAccepted())
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );
        String projectName = "preview-artifact-" + assessmentRunId;
        Path manifest = outputRoot.resolve("preprocessing").resolve(projectName).resolve("large.json");
        Files.createDirectories(manifest.getParent());
        Files.writeString(
                manifest,
                "HEAD" + "x".repeat(8_188) + "TAIL_MARKER",
                StandardCharsets.UTF_8
        );

        MvcResult result = gatewayMvc.perform(get(
                        "/api/vca/preview-artifact/runs/{assessmentRunId}/intermediate-results",
                        assessmentRunId
                ))
                .andExpect(status().isOk())
                .andReturn();
        String preview = JsonPath.read(
                result.getResponse().getContentAsString(),
                "$.stages[0].items[0].preview"
        );
        assertThat(preview)
                .hasSize(8_192)
                .startsWith("HEAD")
                .doesNotContain("TAIL_MARKER");
    }

    @Test
    void suppressesSensitiveRagAndPromptIntermediatePreviews() throws Exception {
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        Path outputRoot = tempDirectory.resolve("engine-output");
        VcaIntermediateResultStorage intermediateStorage =
                new VcaIntermediateResultStorage(outputRoot.toString());
        MockMvc gatewayMvc = mvc(new VcaService(
                true,
                new StaticVcaAiGateway(),
                sharedStorage,
                intermediateStorage
        ));
        gatewayMvc.perform(multipart("/api/vca/sensitive-preview-artifact/images").file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/sensitive-preview-artifact/runs"))
                .andExpect(status().isAccepted())
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );
        String projectName = "sensitive-preview-artifact-" + assessmentRunId;
        Path ragFile = outputRoot.resolve("rag").resolve(projectName).resolve("prompt_rag_results.jsonl");
        Path promptFile = outputRoot.resolve("prompt_generating").resolve(projectName).resolve("prompt.json");
        Files.createDirectories(ragFile.getParent());
        Files.createDirectories(promptFile.getParent());
        Files.writeString(ragFile, "{\"snippet_text\":\"sensitive retrieved evidence\"}");
        Files.writeString(promptFile, "{\"prompt\":\"sensitive prompt text\"}");

        MvcResult result = gatewayMvc.perform(get(
                        "/api/vca/sensitive-preview-artifact/runs/{assessmentRunId}/intermediate-results",
                        assessmentRunId
                ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.stages[0].stage").value("rag"))
                .andExpect(jsonPath("$.stages[0].items[0].relativePath").value("prompt_rag_results.jsonl"))
                .andExpect(jsonPath("$.stages[0].items[0].preview").isEmpty())
                .andExpect(jsonPath("$.stages[1].stage").value("prompt_generating"))
                .andExpect(jsonPath("$.stages[1].items[0].relativePath").value("prompt.json"))
                .andExpect(jsonPath("$.stages[1].items[0].preview").isEmpty())
                .andReturn();

        assertThat(result.getResponse().getContentAsString())
                .doesNotContain("sensitive retrieved evidence", "sensitive prompt text");
    }

    @Test
    void returnsVcaErrorEnvelopeForValidationNotFoundAndNotReady() throws Exception {
        mockMvc.perform(post("/api/vca/bad!artifact/images/presign")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "fileName": "front.jpg",
                                  "contentType": "text/plain",
                                  "sizeBytes": 0,
                                  "sha256": "bad"
                                }
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.error.message").isString());

        mockMvc.perform(get("/api/vca/empty-artifact"))
                .andExpect(status().isOk());

        mockMvc.perform(get(
                        "/api/vca/empty-artifact/runs/{assessmentRunId}/report",
                        "12345678-1234-5678-1234-567812345678"
                ))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("RUN_NOT_FOUND"));

        mockMvc.perform(post("/api/vca/empty-artifact/runs"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("NOT_READY"));

        mockMvc.perform(get("/api/vca/files/sha256/{sha256}", SHA256))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("VCA_ENDPOINT_NOT_FOUND"));

        mockMvc.perform(get("/api/vca/unknown/contract/path"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("VCA_ENDPOINT_NOT_FOUND"));
    }

    @Test
    void validatesCompletionChecksumAndHardDeletesImageMetadata() throws Exception {
        MvcResult presignResult = mockMvc.perform(post("/api/vca/delete-artifact/images/presign")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "fileName": "detail.png",
                                  "contentType": "image/png",
                                  "sizeBytes": 512,
                                  "sha256": "%s"
                                }
                                """.formatted(SHA256)))
                .andExpect(status().isCreated())
                .andReturn();
        String imageId = JsonPath.read(
                presignResult.getResponse().getContentAsString(),
                "$.imageId"
        );

        mockMvc.perform(post("/api/vca/delete-artifact/images/{imageId}/complete", imageId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
                                """))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("SHA256_MISMATCH"));

        mockMvc.perform(delete("/api/vca/delete-artifact/images/{imageId}", imageId))
                .andExpect(status().isNoContent());

        mockMvc.perform(post("/api/vca/delete-artifact/images/{imageId}/complete", imageId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"sha256":"%s"}
                                """.formatted(SHA256)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("IMAGE_NOT_FOUND"));
    }

    @Test
    void managesRootLevelPdfCorpusThroughVcaEndpoints() throws Exception {
        Path corpusRoot = tempDirectory.resolve("document-corpus");
        MockMvc corpusMvc = mvc(new VcaService(true, new VcaCorpusStorage(corpusRoot.toString())));
        Files.createDirectories(corpusRoot.resolve("nested"));
        Files.write(corpusRoot.resolve("nested").resolve("ignored.pdf"), PDF_BYTES);

        corpusMvc.perform(get("/api/vca/corpus/pdfs"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items").isEmpty());

        corpusMvc.perform(multipart("/api/vca/corpus/pdfs").file(
                        new MockMultipartFile("file", "handbook.pdf", "application/pdf", PDF_BYTES)))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.fileName").value("handbook.pdf"))
                .andExpect(jsonPath("$.contentType").value("application/pdf"))
                .andExpect(jsonPath("$.sizeBytes").value(PDF_BYTES.length))
                .andExpect(jsonPath("$.sha256").value(org.hamcrest.Matchers.matchesPattern("[a-f0-9]{64}")))
                .andExpect(jsonPath("$.updatedAt").isString());

        corpusMvc.perform(get("/api/vca/corpus/pdfs"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].fileName").value("handbook.pdf"))
                .andExpect(jsonPath("$.items[0].contentType").value("application/pdf"))
                .andExpect(jsonPath("$.items[0].sizeBytes").value(PDF_BYTES.length))
                .andExpect(jsonPath("$.items[0].sha256").value(org.hamcrest.Matchers.matchesPattern("[a-f0-9]{64}")))
                .andExpect(jsonPath("$.items[0].updatedAt").isString());

        corpusMvc.perform(delete("/api/vca/corpus/pdfs/{fileName}", "handbook.pdf"))
                .andExpect(status().isNoContent());

        corpusMvc.perform(get("/api/vca/corpus/pdfs"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items").isEmpty());
        assertThat(corpusRoot.resolve("handbook.pdf")).doesNotExist();
    }

    @Test
    void rejectsCorpusUploadWithNonPdfContentType() throws Exception {
        MockMvc corpusMvc = mvc(new VcaService(
                true,
                new VcaCorpusStorage(tempDirectory.resolve("document-corpus").toString())
        ));

        corpusMvc.perform(multipart("/api/vca/corpus/pdfs").file(
                        new MockMultipartFile("file", "handbook.pdf", "application/octet-stream", PDF_BYTES)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("VALIDATION_ERROR"));
    }

    @Test
    void rejectsCorpusUploadWhoseBytesAreNotPdf() throws Exception {
        MockMvc corpusMvc = mvc(new VcaService(
                true,
                new VcaCorpusStorage(tempDirectory.resolve("document-corpus").toString())
        ));

        corpusMvc.perform(multipart("/api/vca/corpus/pdfs").file(
                        new MockMultipartFile(
                                "file",
                                "handbook.pdf",
                                "application/pdf",
                                "not a PDF".getBytes(StandardCharsets.UTF_8)
                        )))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("VALIDATION_ERROR"));
    }

    @Test
    void rejectsDuplicateCorpusPdfWithoutOverwritingIt() throws Exception {
        Path corpusRoot = tempDirectory.resolve("document-corpus");
        Files.createDirectories(corpusRoot);
        Files.write(corpusRoot.resolve("handbook.pdf"), PDF_BYTES);
        MockMvc corpusMvc = mvc(new VcaService(true, new VcaCorpusStorage(corpusRoot.toString())));

        corpusMvc.perform(multipart("/api/vca/corpus/pdfs").file(
                        new MockMultipartFile(
                                "file",
                                "handbook.pdf",
                                "application/pdf",
                                "%PDF-1.7\nreplacement".getBytes(StandardCharsets.UTF_8)
                        )))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("CORPUS_PDF_ALREADY_EXISTS"));

        assertThat(Files.readAllBytes(corpusRoot.resolve("handbook.pdf"))).isEqualTo(PDF_BYTES);
    }

    @Test
    void requiresAccessTokenForCorpusPdfEndpoints() throws Exception {
        MockMvc secured = securedMvc(
                new VcaService(
                        true,
                        new VcaCorpusStorage(tempDirectory.resolve("document-corpus").toString())
                ),
                "test-token"
        );

        secured.perform(get("/api/vca/corpus/pdfs"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("VCA_UNAUTHORIZED"));

        secured.perform(multipart("/api/vca/corpus/pdfs").file(
                        new MockMultipartFile("file", "handbook.pdf", "application/pdf", PDF_BYTES)))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("VCA_UNAUTHORIZED"));

        secured.perform(get("/api/vca/corpus/pdfs").header("X-VCA-Access-Token", "test-token"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items").isEmpty());
    }
}
