package com.aivle.conservation_backend.vca.service;

import com.jayway.jsonpath.JsonPath;
import com.aivle.conservation_backend.vca.config.VcaAccessTokenInterceptor;
import com.aivle.conservation_backend.vca.controller.VcaController;
import com.aivle.conservation_backend.vca.dto.ArtifactDetailResponse;
import com.aivle.conservation_backend.vca.dto.RunResponse;
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
        public VcaAiAssessmentReport getAssessmentReport(String runId) {
            return new VcaAiAssessmentReport(
                    runId,
                    runId.replace("vca-ai-", ""),
                    "COMPLETED",
                    "Gateway generated VCA report.",
                    List.of(new VcaAiAssessmentFinding(
                            "VCA_ANOMALY",
                            "INFO",
                            "Gateway finding propagated."
                    ))
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
                .andExpect(jsonPath("$.summary.overallCondition").value("FAIR"))
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
            public VcaAiAssessmentReport getAssessmentReport(String runId) {
                return new VcaAiAssessmentReport(
                        runId,
                        runId.replace("vca-ai-", ""),
                        "COMPLETED",
                        "Gateway generated VCA report.",
                        List.of(new VcaAiAssessmentFinding(
                                "VCA_ANOMALY",
                                "INFO",
                                "Gateway finding propagated."
                        ))
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
                .andExpect(jsonPath("$.findings[0].title").value("VCA 이상 후보"))
                .andExpect(jsonPath("$.recommendations[0].title").value("전문가 검토 후 보존처리 계획에 반영"))
                .andExpect(jsonPath("$.findings[0].description").value("Gateway finding propagated."));
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
}
