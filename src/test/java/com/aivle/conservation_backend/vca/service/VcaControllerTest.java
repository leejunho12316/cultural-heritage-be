package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.pottery_inspection_ai.client.PotteryInspectionAiClient;
import com.aivle.conservation_backend.pottery_inspection_ai.dto.PotteryInspectionResponseDto;
import com.jayway.jsonpath.JsonPath;
import com.aivle.conservation_backend.vca.config.VcaAccessTokenInterceptor;
import com.aivle.conservation_backend.vca.controller.VcaController;
import com.aivle.conservation_backend.vca.dto.ArtifactDetailResponse;
import com.aivle.conservation_backend.vca.dto.CreateArtifactRequest;
import com.aivle.conservation_backend.vca.dto.RunResponse;
import com.aivle.conservation_backend.vca.exception.VcaApiException;
import com.aivle.conservation_backend.vca.exception.VcaExceptionHandler;
import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentFinding;
import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentReport;
import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentRun;
import com.aivle.conservation_backend.vca.gateway.VcaAiGateway;
import com.aivle.conservation_backend.vca.gateway.VcaAiSystemInfo;
import org.apache.pdfbox.Loader;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.text.PDFTextStripper;
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

import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
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
import javax.imageio.ImageIO;

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

    // artifactId가 이제 서버 생성 UUID라, 테스트도 실제 API처럼 먼저 POST /api/vca로
    // 만들고 응답에서 그 UUID를 받아써야 한다(예전처럼 임의 슬러그를 URL에 바로 못 씀).
    private String createArtifact(MockMvc mvc, String name) throws Exception {
        MvcResult result = mvc.perform(post("/api/vca")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"" + name + "\"}"))
                .andExpect(status().isCreated())
                .andReturn();
        return JsonPath.read(result.getResponse().getContentAsString(), "$.artifactId");
    }

    private String createArtifact(MockMvc mvc) throws Exception {
        return createArtifact(mvc, "test artifact");
    }

    private static final class StaticVcaAiGateway implements VcaAiGateway {

        @Override
        public VcaAiSystemInfo getSystemInfo() {
            return new VcaAiSystemInfo("test-os", "3.13", "cpu", Map.of(), List.of());
        }

        @Override
        public VcaAiAssessmentRun createAssessmentRun(
                String assessmentId,
                String projectName,
                String inputImageFolder,
                List<VcaAiGateway.InputImageUrl> inputImageUrls,
                String resumeFromProjectName
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
                    runId, runId.replace("vca-ai-", ""), "FAILED", null, null, List.of(), "cancelled by user"
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
                return new VcaSharedStorage.RunInputDirectory("/shared/vca/" + assessmentRunId + "/input", List.of());
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

        @Override
        public void storeBytes(String objectKey, byte[] bytes, String contentType) {
            try {
                Path objectPath = root.resolve(objectKey);
                Files.createDirectories(objectPath.getParent());
                Files.write(objectPath, bytes);
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
        String artifactId = createArtifact(mockMvc, "test artifact");

        MvcResult presignResult = mockMvc.perform(post("/api/vca/{artifactId}/images/presign", artifactId)
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

        mockMvc.perform(post("/api/vca/{artifactId}/images/{imageId}/complete", artifactId, imageId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"sha256":"%s"}
                                """.formatted(SHA256)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("UPLOADED"))
                .andExpect(jsonPath("$.imageUrl").value(
                        "/api/vca/" + artifactId + "/files/sha256/" + SHA256));

        MvcResult runResult = mockMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("QUEUED"))
                .andExpect(jsonPath("$.imageCount").value(1))
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );

        mockMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("ACTIVE_RUN_EXISTS"));

        MvcResult reportResult = mockMvc.perform(get(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/report",
                        artifactId, assessmentRunId
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
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/report/pdf",
                        artifactId, assessmentRunId
                ))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("QUEUED"))
                .andReturn();
        String jobId = JsonPath.read(
                pdfResult.getResponse().getContentAsString(),
                "$.jobId"
        );

        mockMvc.perform(post(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/report/pdf",
                        artifactId, assessmentRunId
                ))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.jobId").value(jobId))
                .andExpect(jsonPath("$.status").value("QUEUED"));

        mockMvc.perform(get("/api/vca/{artifactId}/report-pdf-jobs/{jobId}/download", artifactId, jobId))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("PDF_NOT_READY"));

        mockMvc.perform(get("/api/vca/{artifactId}/report-pdf-jobs/{jobId}", artifactId, jobId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("COMPLETED"))
                .andExpect(jsonPath("$.downloadUrl").value(
                        "/api/vca/" + artifactId + "/report-pdf-jobs/" + jobId + "/download"));

        mockMvc.perform(get("/api/vca/{artifactId}/report-pdf-jobs/{jobId}/download", artifactId, jobId))
                .andExpect(status().isSeeOther())
                .andExpect(header().string(
                        "Location",
                        org.hamcrest.Matchers.startsWith("https://vca-local.invalid/downloads/")
                ));

        mockMvc.perform(get("/api/vca/{artifactId}/files/sha256/{sha256}", artifactId, SHA256))
                .andExpect(status().isSeeOther())
                .andExpect(header().string(
                        "Location",
                        org.hamcrest.Matchers.startsWith("https://vca-local.invalid/files/")
                ));

        mockMvc.perform(get("/api/vca"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items").isArray())
                .andExpect(jsonPath("$.items[?(@.artifactId == '" + artifactId + "')]").exists());

        MvcResult artifactResult = mockMvc.perform(get("/api/vca/{artifactId}", artifactId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.artifactId").value(artifactId))
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

    private static byte[] realJpegBytes() throws Exception {
        BufferedImage image = new BufferedImage(64, 48, BufferedImage.TYPE_INT_RGB);
        for (int y = 0; y < image.getHeight(); y++) {
            for (int x = 0; x < image.getWidth(); x++) {
                image.setRGB(x, y, (x * 4) << 16 | (y * 4) << 8);
            }
        }
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        assertThat(ImageIO.write(image, "jpg", buffer)).isTrue();
        return buffer.toByteArray();
    }

    @Test
    void rendersRealReportPdfWhenObjectStorageIsConfigured() throws Exception {
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        FakeVcaImageStorage imageStorage = new FakeVcaImageStorage(tempDirectory.resolve("objects"));
        MockMvc pdfMvc = mvc(new VcaService(true, new StaticVcaAiGateway(), sharedStorage, imageStorage));
        String artifactId = createArtifact(pdfMvc, "pdf artifact");

        pdfMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId).file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", realJpegBytes())))
                .andExpect(status().isCreated());
        MvcResult runResult = pdfMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted())
                .andReturn();
        String runId = JsonPath.read(runResult.getResponse().getContentAsString(), "$.assessmentRunId");

        // When: a PDF job is created for the completed run.
        MvcResult pdfResult = pdfMvc.perform(post(
                        "/api/vca/{artifactId}/runs/{runId}/report/pdf", artifactId, runId
                ))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("COMPLETED"))
                .andReturn();
        String jobId = JsonPath.read(pdfResult.getResponse().getContentAsString(), "$.jobId");

        // Then: the job is COMPLETED immediately (rendered synchronously) and the
        // download endpoint redirects to a real object-storage URL backed by an
        // actual file - not the old "/downloads/{jobId}.pdf" stub that nothing served.
        MvcResult downloadResult = pdfMvc.perform(get(
                        "/api/vca/{artifactId}/report-pdf-jobs/{jobId}/download", artifactId, jobId
                ))
                .andExpect(status().isSeeOther())
                .andReturn();
        String location = downloadResult.getResponse().getHeader("Location");
        assertThat(location).startsWith("http://localhost:9000/conservation-local/report-pdfs/" + jobId);

        Path pdfFile = tempDirectory.resolve("objects").resolve("report-pdfs/" + jobId + ".pdf");
        assertThat(Files.exists(pdfFile)).isTrue();
        byte[] pdfBytes = Files.readAllBytes(pdfFile);
        assertThat(pdfBytes.length).isGreaterThan(1_000);
        try (PDDocument document = Loader.loadPDF(pdfBytes)) {
            // Cover page + at least one overview/stats page + one page per finding
            // (StaticVcaAiGateway returns exactly one finding).
            assertThat(document.getNumberOfPages()).isGreaterThanOrEqualTo(3);
        }
    }

    @Test
    void includesPotteryInspectionSectionInReportPdfWhenPresent() throws Exception {
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        FakeVcaImageStorage imageStorage = new FakeVcaImageStorage(tempDirectory.resolve("objects"));
        FakePotteryInspectionAiClient potteryClient = new FakePotteryInspectionAiClient();
        MockMvc pdfMvc = mvc(new VcaService(true, new StaticVcaAiGateway(), sharedStorage, imageStorage, potteryClient));
        String artifactId = createArtifact(pdfMvc, "pottery pdf artifact");

        pdfMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId).file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", realJpegBytes())))
                .andExpect(status().isCreated());
        MvcResult runResult = pdfMvc.perform(post("/api/vca/{artifactId}/runs", artifactId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"material":"도자기"}
                                """))
                .andExpect(status().isAccepted())
                .andReturn();
        String runId = JsonPath.read(runResult.getResponse().getContentAsString(), "$.assessmentRunId");

        // Polling the report once triggers the auto pottery inspection (same
        // transition-based trigger as runsPotteryInspectionFromReportOnlyForPotteryMaterial).
        pdfMvc.perform(get("/api/vca/{artifactId}/runs/{runId}/report", artifactId, runId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.potteryInspection.moduleVersion").value("pottery-test-v1"));

        MvcResult pdfResult = pdfMvc.perform(post(
                        "/api/vca/{artifactId}/runs/{runId}/report/pdf", artifactId, runId
                ))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("COMPLETED"))
                .andReturn();
        String jobId = JsonPath.read(pdfResult.getResponse().getContentAsString(), "$.jobId");

        Path pdfFile = tempDirectory.resolve("objects").resolve("report-pdfs/" + jobId + ".pdf");
        byte[] pdfBytes = Files.readAllBytes(pdfFile);
        String text;
        try (PDDocument document = Loader.loadPDF(pdfBytes)) {
            text = new PDFTextStripper().getText(document);
        }
        assertThat(text).contains("도자기 검사");
        assertThat(text).contains("pottery-test-v1");
        assertThat(text).contains("도자기 문양 요약");
        assertThat(text).contains("도자기 문양 검사 결과입니다.");
        assertThat(text).contains("cloud");
    }

    @Test
    void rejectsDirectCompleteWhenLocalUploadModeIsNotEnabled() throws Exception {
        MockMvc productionMvc = mvc(new VcaService(false));
        String artifactId = createArtifact(productionMvc, "production artifact");
        MvcResult presignResult = productionMvc.perform(post("/api/vca/{artifactId}/images/presign", artifactId)
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

        productionMvc.perform(post("/api/vca/{artifactId}/images/{imageId}/complete", artifactId, imageId)
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
        String artifactId = createArtifact(productionMvc, "s3 artifact");

        MvcResult presignResult = productionMvc.perform(post("/api/vca/{artifactId}/images/presign", artifactId)
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
                        "http://localhost:9000/conservation-local/vca/images/" + artifactId + "/")))
                .andExpect(jsonPath("$.requiredHeaders['x-amz-meta-sha256']").value(SHA256))
                .andReturn();
        String imageId = JsonPath.read(presignResult.getResponse().getContentAsString(), "$.imageId");

        productionMvc.perform(post("/api/vca/{artifactId}/images/{imageId}/complete", artifactId, imageId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"sha256":"%s"}
                                """.formatted(SHA256)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("UPLOADED"));
        assertThat(imageStorage.uploadVerified).isTrue();

        productionMvc.perform(get("/api/vca/{artifactId}/files/sha256/{sha256}", artifactId, SHA256))
                .andExpect(status().isSeeOther())
                .andExpect(header().string(
                        "Location",
                        org.hamcrest.Matchers.startsWith("http://localhost:9000/conservation-local/vca/images/")
                ));

        productionMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("RUNNING"));
        assertThat(imageStorage.runInputMaterialized).isTrue();
    }

    @Test
    void enforcesConfiguredVcaAccessToken() throws Exception {
        MockMvc secured = securedMvc(new VcaService(true), "test-token");
        MvcResult createResult = secured.perform(post("/api/vca")
                        .header("X-VCA-Access-Token", "test-token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"demo artifact\"}"))
                .andExpect(status().isCreated())
                .andReturn();
        String artifactId = JsonPath.read(createResult.getResponse().getContentAsString(), "$.artifactId");

        secured.perform(get("/api/vca/{artifactId}", artifactId))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("VCA_UNAUTHORIZED"));

        secured.perform(get("/api/vca/{artifactId}", artifactId)
                        .header("X-VCA-Access-Token", "wrong-token"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("VCA_UNAUTHORIZED"));

        secured.perform(get("/api/vca/{artifactId}", artifactId)
                        .header("X-VCA-Access-Token", "test-token"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.artifactId").value(artifactId));

        secured.perform(options("/api/vca/{artifactId}", artifactId))
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
        MvcResult createResult = secured.perform(post("/api/vca")
                        .header("X-VCA-Access-Token", "test-token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"demo artifact\"}"))
                .andExpect(status().isCreated())
                .andReturn();
        String artifactId = JsonPath.read(createResult.getResponse().getContentAsString(), "$.artifactId");

        MvcResult presignResult = secured.perform(post("/api/vca/{artifactId}/images/presign", artifactId)
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

        secured.perform(post("/api/vca/{artifactId}/images/{imageId}/complete", artifactId, imageId)
                        .header("X-VCA-Access-Token", "test-token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"sha256":"%s"}
                                """.formatted(SHA256)))
                .andExpect(status().isOk());

        secured.perform(get("/api/vca/{artifactId}/files/sha256/{sha256}", artifactId, SHA256)
                        .queryParam("vca_access_token", "test-token"))
                .andExpect(status().isSeeOther());
    }

    @Test
    void cancelsQueuedDemoRunAndReportsFailedWithReason() throws Exception {
        String artifactId = createArtifact(mockMvc, "cancel demo artifact");
        MvcResult presignResult = mockMvc.perform(post("/api/vca/{artifactId}/images/presign", artifactId)
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
        mockMvc.perform(post("/api/vca/{artifactId}/images/{imageId}/complete", artifactId, imageId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"sha256":"%s"}
                                """.formatted(SHA256)))
                .andExpect(status().isOk());

        MvcResult runResult = mockMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("QUEUED"))
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );

        mockMvc.perform(post("/api/vca/{artifactId}/runs/{assessmentRunId}/cancel", artifactId, assessmentRunId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"))
                .andExpect(jsonPath("$.failureReason").value("사용자가 분석을 중지했습니다."));

        // A second stop click on an already-terminal run is a harmless no-op.
        mockMvc.perform(post("/api/vca/{artifactId}/runs/{assessmentRunId}/cancel", artifactId, assessmentRunId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"));
    }

    @Test
    void cancelsRunThroughGatewayAndAppliesReturnedStatus() throws Exception {
        VcaAiGateway gateway = new VcaAiGateway() {
            @Override
            public VcaAiSystemInfo getSystemInfo() {
                return new VcaAiSystemInfo("test-os", "3.13", "cpu", Map.of(), List.of());
            }

            @Override
            public VcaAiAssessmentRun createAssessmentRun(
                    String assessmentId,
                    String projectName,
                    String inputImageFolder,
                    List<VcaAiGateway.InputImageUrl> inputImageUrls,
                    String resumeFromProjectName
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
                        runId, runId.replace("vca-ai-", ""), "FAILED", null, null, List.of(), "cancelled by user"
                );
            }

            @Override
            public VcaAiAssessmentReport getAssessmentReport(String runId) {
                throw new AssertionError("Report must not be fetched while cancelling a run.");
            }
        };
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        MockMvc gatewayMvc = mvc(new VcaService(true, gateway, sharedStorage));
        String artifactId = createArtifact(gatewayMvc, "cancel gateway artifact");

        gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId).file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("RUNNING"))
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );

        gatewayMvc.perform(post("/api/vca/{artifactId}/runs/{assessmentRunId}/cancel", artifactId, assessmentRunId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"))
                .andExpect(jsonPath("$.failureReason").value("cancelled by user"));
    }

    // 예전엔 "처음 보는 artifactId로 GET하면 DRAFT 상태로 자동 생성됨"을 검증했는데,
    // 이제 artifactId가 서버 생성 UUID라 자동 생성 자체가 없다 - 그 대신 POST(생성) 직후
    // 응답이 DRAFT 상태(업로드/run 없음)인지를 검증하는 걸로 의도를 유지한다.
    @Test
    void returnsDraftArtifactForFirstVcaEntry() throws Exception {
        MvcResult createResult = mockMvc.perform(post("/api/vca")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"new workspace artifact\"}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.artifactId").isString())
                .andExpect(jsonPath("$.status").value("DRAFT"))
                .andExpect(jsonPath("$.uploadedImages").isEmpty())
                .andExpect(jsonPath("$.runs").isEmpty())
                .andExpect(jsonPath("$.createdAt").isString())
                .andExpect(jsonPath("$.updatedAt").isString())
                .andReturn();
        String artifactId = JsonPath.read(createResult.getResponse().getContentAsString(), "$.artifactId");

        mockMvc.perform(get("/api/vca/{artifactId}", artifactId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.artifactId").value(artifactId))
                .andExpect(jsonPath("$.status").value("DRAFT"));
    }

    @Test
    void advancesDemoRunStatusThroughArtifactPolling() throws Exception {
        String artifactId = createArtifact(mockMvc, "polling artifact");
        MvcResult presignResult = mockMvc.perform(post("/api/vca/{artifactId}/images/presign", artifactId)
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

        mockMvc.perform(post("/api/vca/{artifactId}/images/{imageId}/complete", artifactId, imageId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"sha256":"%s"}
                                """.formatted(SHA256)))
                .andExpect(status().isOk());

        MvcResult runResult = mockMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("QUEUED"))
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );

        Thread.sleep(1_000);
        mockMvc.perform(get("/api/vca/{artifactId}", artifactId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.runs[0].assessmentRunId").value(assessmentRunId))
                .andExpect(jsonPath("$.runs[0].status").value("RUNNING"))
                .andExpect(jsonPath("$.status").value("ANALYZING"));

        Thread.sleep(2_100);
        mockMvc.perform(get("/api/vca/{artifactId}", artifactId))
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
            public VcaAiSystemInfo getSystemInfo() {
                return new VcaAiSystemInfo("test-os", "3.13", "cpu", Map.of(), List.of());
            }

            @Override
            public VcaAiAssessmentRun createAssessmentRun(
                    String requestedAssessmentId,
                    String requestedProjectName,
                    String requestedInputImageFolder,
                    List<VcaAiGateway.InputImageUrl> inputImageUrls,
                    String requestedResumeFromProjectName
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
                        runId, runId.replace("vca-ai-", ""), "FAILED", null, null, List.of(), "cancelled by user"
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
        String artifactId = createArtifact(gatewayMvc, "gateway artifact");

        MockMultipartFile file = new MockMultipartFile(
                "file",
                "front.jpg",
                "image/jpeg",
                JPEG_BYTES
        );
        MvcResult uploadResult = gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId)
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

        MvcResult runResult = gatewayMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("RUNNING"))
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );
        assertThat(assessmentId.get()).isEqualTo(assessmentRunId);
        assertThat(projectName.get()).isEqualTo(artifactId + "-" + assessmentRunId);
        assertThat(inputImageFolder.get()).isEqualTo("/shared/vca/" + assessmentRunId + "/input");
        assertThat(Files.list(tempDirectory.resolve(assessmentRunId).resolve("input")).toList())
                .hasSize(1)
                .anySatisfy(path -> assertThat(path.getFileName().toString()).contains(imageId));

        gatewayMvc.perform(get("/api/vca/{artifactId}", artifactId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.runs[0].status").value("COMPLETED"));

        gatewayMvc.perform(get(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/report",
                        artifactId,
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

    @Test
    void resumesFromPriorFailedRunWhenImagesAreUnchanged() throws Exception {
        AtomicInteger createCallCount = new AtomicInteger();
        AtomicReference<String> firstResumeFromProjectName = new AtomicReference<>();
        AtomicReference<String> secondResumeFromProjectName = new AtomicReference<>();
        VcaAiGateway gateway = new VcaAiGateway() {
            @Override
            public VcaAiSystemInfo getSystemInfo() {
                return new VcaAiSystemInfo("test-os", "3.13", "cpu", Map.of(), List.of());
            }

            @Override
            public VcaAiAssessmentRun createAssessmentRun(
                    String assessmentId,
                    String projectName,
                    String inputImageFolder,
                    List<VcaAiGateway.InputImageUrl> inputImageUrls,
                    String resumeFromProjectName
            ) {
                if (createCallCount.getAndIncrement() == 0) {
                    firstResumeFromProjectName.set(resumeFromProjectName);
                } else {
                    secondResumeFromProjectName.set(resumeFromProjectName);
                }
                return new VcaAiAssessmentRun("vca-ai-" + assessmentId, assessmentId, "RUNNING");
            }

            @Override
            public VcaAiAssessmentRun getAssessmentStatus(String runId) {
                return new VcaAiAssessmentRun(runId, runId.replace("vca-ai-", ""), "RUNNING");
            }

            @Override
            public VcaAiAssessmentRun cancelAssessmentRun(String runId) {
                return new VcaAiAssessmentRun(
                        runId, runId.replace("vca-ai-", ""), "FAILED", null, null, List.of(), "simulated failure"
                );
            }

            @Override
            public VcaAiAssessmentReport getAssessmentReport(String runId) {
                throw new AssertionError("Report must not be fetched in this test.");
            }
        };
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        MockMvc gatewayMvc = mvc(new VcaService(true, gateway, sharedStorage));
        String artifactId = createArtifact(gatewayMvc, "resume gateway artifact");

        gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId).file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());

        MvcResult firstRunResult = gatewayMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted())
                .andReturn();
        String firstRunId = JsonPath.read(
                firstRunResult.getResponse().getContentAsString(), "$.assessmentRunId"
        );

        // Then: a first run has no prior attempt to resume from.
        assertThat(firstResumeFromProjectName.get()).isNull();

        gatewayMvc.perform(post(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/cancel", artifactId, firstRunId
                ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FAILED"));

        // And: the artifact now advertises a resumable run so FE can offer
        // both "이어서 분석 시작" and "새로 분석 시작".
        gatewayMvc.perform(get("/api/vca/{artifactId}", artifactId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.resumableRunId").value(firstRunId));

        // When: the same artifact is re-run with the exact same uploaded images,
        // explicitly choosing "이어서 분석 시작" (resume=true).
        MvcResult secondRunResult = gatewayMvc.perform(post("/api/vca/{artifactId}/runs", artifactId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"resume":true}
                                """))
                .andExpect(status().isAccepted())
                .andReturn();
        String secondRunId = JsonPath.read(
                secondRunResult.getResponse().getContentAsString(), "$.assessmentRunId"
        );

        // Then: vca-ai is told to resume from the failed run's project name,
        // and the new run still gets its own distinct id (audit trail intact).
        assertThat(secondResumeFromProjectName.get())
                .isEqualTo(artifactId + "-" + firstRunId);
        assertThat(secondRunId).isNotEqualTo(firstRunId);
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
        String potteryArtifactId = createArtifact(gatewayMvc, "pottery artifact");

        gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", potteryArtifactId).file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());
        MvcResult potteryRun = gatewayMvc.perform(post("/api/vca/{artifactId}/runs", potteryArtifactId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"material":"도자기"}
                                """))
                .andExpect(status().isAccepted())
                .andReturn();
        String potteryRunId = JsonPath.read(potteryRun.getResponse().getContentAsString(), "$.assessmentRunId");

        // Then: the very first report poll already carries the pottery result -
        // the transition to a COMPLETED VCA report auto-triggers pottery
        // inspection server-side, with no separate manual call required.
        gatewayMvc.perform(get(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/report",
                        potteryArtifactId,
                        potteryRunId
                ))
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

        // When: the manual endpoint is still used to explicitly re-run it (eg. as a retry).
        gatewayMvc.perform(post(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/pottery-inspection",
                        potteryArtifactId,
                        potteryRunId
                )
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"material":"도자기"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.summary.headline").value("VCA 육안 조사 결과"))
                .andExpect(jsonPath("$.potteryInspection.moduleVersion").value("pottery-test-v1"))
                .andExpect(jsonPath("$.potteryInspectionStatus.applicable").value(true))
                .andExpect(jsonPath("$.potteryInspectionStatus.status").value("COMPLETED"))
                .andExpect(jsonPath("$.potteryInspectionStatus.retryable").value(true));
        assertThat(potteryClient.calls.get()).isEqualTo(2);

        String bronzeArtifactId = createArtifact(gatewayMvc, "bronze artifact");
        gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", bronzeArtifactId).file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());
        MvcResult bronzeRun = gatewayMvc.perform(post("/api/vca/{artifactId}/runs", bronzeArtifactId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"material":"청동"}
                                """))
                .andExpect(status().isAccepted())
                .andReturn();
        String bronzeRunId = JsonPath.read(bronzeRun.getResponse().getContentAsString(), "$.assessmentRunId");

        gatewayMvc.perform(get(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/report",
                        bronzeArtifactId,
                        bronzeRunId
                ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.summary.headline").value("VCA 육안 조사 결과"))
                .andExpect(jsonPath("$.potteryInspection").isEmpty());

        gatewayMvc.perform(post(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/pottery-inspection",
                        bronzeArtifactId,
                        bronzeRunId
                )
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"material":"청동"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.summary.headline").value("VCA 육안 조사 결과"))
                .andExpect(jsonPath("$.potteryInspection").isEmpty())
                .andExpect(jsonPath("$.potteryInspectionStatus.applicable").value(false))
                .andExpect(jsonPath("$.potteryInspectionStatus.retryable").value(false));
        assertThat(potteryClient.calls.get()).isEqualTo(2);
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
        String artifactId = createArtifact(gatewayMvc, "pottery failure artifact");

        gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId).file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/{artifactId}/runs", artifactId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"material":"도자기"}
                                """))
                .andExpect(status().isAccepted())
                .andReturn();
        String runId = JsonPath.read(runResult.getResponse().getContentAsString(), "$.assessmentRunId");

        gatewayMvc.perform(post(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/pottery-inspection", artifactId, runId
                )
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
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/report",
                        artifactId,
                        runId
                ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.summary.headline").value("VCA 육안 조사 결과"))
                .andExpect(jsonPath("$.potteryInspectionStatus.status").value("FAILED"));
    }

    @Test
    void createsPdfAfterSyncingAiStatusThatAlsoCachesTheReportOnFirstCompletion() throws Exception {
        VcaAiGateway gateway = new VcaAiGateway() {
            @Override
            public VcaAiSystemInfo getSystemInfo() {
                return new VcaAiSystemInfo("test-os", "3.13", "cpu", Map.of(), List.of());
            }

            @Override
            public VcaAiAssessmentRun createAssessmentRun(
                    String assessmentId,
                    String projectName,
                    String inputImageFolder,
                    List<VcaAiGateway.InputImageUrl> inputImageUrls,
                    String resumeFromProjectName
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
                // syncRunWithAi now fetches and caches the report itself the
                // moment it observes a run transition to COMPLETED (whatever
                // triggered the sync) - vca-ai only keeps a completed run's
                // output on the GPU pod's own local disk, and that disk is
                // gone the moment the pod is recreated, so this DB copy is
                // the only durable record once vca-ai forgets. This test
                // used to assert the opposite (report must NOT be fetched
                // here); a real run losing its only-ever-viewed-once report
                // to a pod restart is what changed that.
                return new VcaAiAssessmentReport(
                        runId,
                        runId.replace("vca-ai-", ""),
                        "COMPLETED",
                        "Gateway generated VCA report.",
                        List.of(),
                        sampleRagArtifacts()
                );
            }
        };
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        MockMvc gatewayMvc = mvc(new VcaService(true, gateway, sharedStorage));
        String artifactId = createArtifact(gatewayMvc, "pdf sync artifact");

        gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId).file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("RUNNING"))
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );

        gatewayMvc.perform(post(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/report/pdf",
                        artifactId,
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
            public VcaAiSystemInfo getSystemInfo() {
                return new VcaAiSystemInfo("test-os", "3.13", "cpu", Map.of(), List.of());
            }

            @Override
            public VcaAiAssessmentRun createAssessmentRun(
                    String assessmentId,
                    String projectName,
                    String inputImageFolder,
                    List<VcaAiGateway.InputImageUrl> inputImageUrls,
                    String resumeFromProjectName
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
        String artifactId = service.createArtifact(new CreateArtifactRequest("nonblocking artifact")).artifactId();
        service.uploadImage(
                artifactId,
                new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)
        );
        ExecutorService executor = Executors.newFixedThreadPool(2);

        try {
            Future<RunResponse> runFuture = executor.submit(() -> service.createRun(artifactId));
            assertThat(gatewayEntered.await(5, TimeUnit.SECONDS)).isTrue();

            Future<ArtifactDetailResponse> detailFuture = executor.submit(
                    () -> service.getArtifact(artifactId)
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
        String artifactId = createArtifact(gatewayMvc, "gateway artifact");
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "fake.png",
                "image/png",
                "not an image".getBytes()
        );

        gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId).file(file))
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
            public VcaAiSystemInfo getSystemInfo() {
                return new VcaAiSystemInfo("test-os", "3.13", "cpu", Map.of(), List.of());
            }

            @Override
            public VcaAiAssessmentRun createAssessmentRun(
                    String assessmentId, String projectName, String inputImageFolder,
                    List<VcaAiGateway.InputImageUrl> inputImageUrls,
                    String resumeFromProjectName
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
        String artifactId = createArtifact(gatewayMvc, "report image id artifact");
        MockMultipartFile file = new MockMultipartFile(
                "file", "front.jpg", "image/jpeg", JPEG_BYTES
        );

        MvcResult uploadResult = gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId).file(file))
                .andExpect(status().isCreated())
                .andReturn();
        String uploadedImageId = JsonPath.read(
                uploadResult.getResponse().getContentAsString(),
                "$.imageId"
        );
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted())
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );

        gatewayMvc.perform(get(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/report",
                        artifactId,
                        assessmentRunId
                ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.images[0].imageId").value(uploadedImageId))
                .andExpect(jsonPath("$.findings[0].imageId").value(uploadedImageId));
    }

    @Test
    void reportSurvivesGatewayBecomingUnreachableAfterItWasCachedOnFirstCompletion() throws Exception {
        // vca-ai keeps a completed run's output only on the GPU pod's own
        // local disk (not any durable store) - if the pod gets recreated
        // after a run completes, that output is gone even though the run
        // legitimately succeeded. The only way a report survives that is if
        // Spring captures it into its own DB the moment it first observes
        // COMPLETED (syncRunWithAi), and getReport then serves that cached
        // copy without needing a live vca-ai call at all. This simulates
        // the pod going unreachable right after that first capture.
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        boolean[] gatewayReachable = {true};
        VcaAiGateway gateway = new VcaAiGateway() {
            @Override
            public VcaAiSystemInfo getSystemInfo() {
                return new VcaAiSystemInfo("test-os", "3.13", "cpu", Map.of(), List.of());
            }

            @Override
            public VcaAiAssessmentRun createAssessmentRun(
                    String assessmentId, String projectName, String inputImageFolder,
                    List<VcaAiGateway.InputImageUrl> inputImageUrls,
                    String resumeFromProjectName
            ) {
                return new VcaAiAssessmentRun("vca-ai-" + assessmentId, assessmentId, "RUNNING");
            }

            @Override
            public VcaAiAssessmentRun getAssessmentStatus(String runId) {
                if (!gatewayReachable[0]) {
                    throw new AssertionError(
                            "Status must not be polled once the report is already cached."
                    );
                }
                return new VcaAiAssessmentRun(runId, runId.replace("vca-ai-", ""), "COMPLETED");
            }

            @Override
            public VcaAiAssessmentRun cancelAssessmentRun(String runId) {
                throw new AssertionError("Cancel must not be called in this test.");
            }

            @Override
            public VcaAiAssessmentReport getAssessmentReport(String runId) {
                if (!gatewayReachable[0]) {
                    throw new AssertionError("Report must not be re-fetched once cached.");
                }
                return new VcaAiAssessmentReport(
                        runId,
                        runId.replace("vca-ai-", ""),
                        "COMPLETED",
                        "Gateway generated VCA report.",
                        List.of(),
                        null
                );
            }
        };
        MockMvc gatewayMvc = mvc(new VcaService(true, gateway, sharedStorage));
        String artifactId = createArtifact(gatewayMvc, "resilient report artifact");
        MockMultipartFile file = new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES);
        gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId).file(file))
                .andExpect(status().isCreated());
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted())
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(), "$.assessmentRunId"
        );

        // When: an artifact poll (as FE does regularly) observes the run
        // transitioning to COMPLETED for the first time - this is what
        // caches the report, independent of anyone ever opening it.
        gatewayMvc.perform(get("/api/vca/{artifactId}", artifactId))
                .andExpect(status().isOk());

        // And: vca-ai/the pod becomes unreachable afterward.
        gatewayReachable[0] = false;

        // Then: the report is still readable from the cache captured
        // during that poll, with no live gateway call needed.
        gatewayMvc.perform(get(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/report",
                        artifactId,
                        assessmentRunId
                ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.summary.headline").value("VCA 육안 조사 결과"));
    }

    private static String sha256(byte[] bytes) throws NoSuchAlgorithmException {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        return HexFormat.of().formatHex(digest.digest(bytes));
    }

    @Test
    void deletesStoredUploadDirectoryWhenImageIsDeleted() throws Exception {
        VcaSharedStorage sharedStorage = new VcaSharedStorage(tempDirectory.toString(), "/shared/vca");
        MockMvc gatewayMvc = mvc(new VcaService(true, new StaticVcaAiGateway(), sharedStorage));
        String artifactId = createArtifact(gatewayMvc, "delete storage artifact");
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "detail.png",
                "image/png",
                PNG_BYTES
        );
        MvcResult uploadResult = gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId)
                        .file(file))
                .andExpect(status().isCreated())
                .andReturn();
        String imageId = JsonPath.read(
                uploadResult.getResponse().getContentAsString(),
                "$.imageId"
        );

        gatewayMvc.perform(delete("/api/vca/{artifactId}/images/{imageId}", artifactId, imageId))
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
        String artifactId = createArtifact(gatewayMvc, "image delete guard artifact");
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "detail.png",
                "image/png",
                PNG_BYTES
        );
        MvcResult uploadResult = gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId)
                        .file(file))
                .andExpect(status().isCreated())
                .andReturn();
        String imageId = JsonPath.read(
                uploadResult.getResponse().getContentAsString(),
                "$.imageId"
        );
        gatewayMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted());

        gatewayMvc.perform(delete("/api/vca/{artifactId}/images/{imageId}", artifactId, imageId))
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
            public VcaAiSystemInfo getSystemInfo() {
                return new VcaAiSystemInfo("test-os", "3.13", "cpu", Map.of(), List.of());
            }

            @Override
            public VcaAiAssessmentRun createAssessmentRun(
                    String assessmentId, String projectName, String inputImageFolder,
                    List<VcaAiGateway.InputImageUrl> inputImageUrls,
                    String resumeFromProjectName
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
        String artifactId = service.createArtifact(new CreateArtifactRequest("stuck run artifact")).artifactId();
        MockMultipartFile file = new MockMultipartFile("file", "detail.png", "image/png", PNG_BYTES);
        service.uploadImage(artifactId, file);

        // When: run creation crashes uncaught partway through.
        assertThatThrownBy(() -> service.createRun(artifactId))
                .isInstanceOf(OutOfMemoryError.class);

        // Then: the QUEUED reservation survives and blocks every subsequent
        // run creation attempt for this artifact - the actual bug.
        ArtifactDetailResponse beforeReconcile = service.getArtifact(artifactId);
        assertThat(beforeReconcile.runs()).hasSize(1);
        assertThat(beforeReconcile.runs().get(0).status()).isEqualTo("QUEUED");
        assertThatThrownBy(() -> service.createRun(artifactId))
                .isInstanceOf(VcaApiException.class)
                .hasMessageContaining("already queued or running");

        // When: the startup reconciler runs, as it would on the next boot.
        service.reconcileStuckRunsOnStartup();

        // Then: the stuck run is marked FAILED and no longer blocks new runs.
        ArtifactDetailResponse afterReconcile = service.getArtifact(artifactId);
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
        String artifactId = createArtifact(gatewayMvc, "intermediate artifact");
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "front.jpg",
                "image/jpeg",
                JPEG_BYTES
        );
        gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId).file(file))
                .andExpect(status().isCreated());
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted())
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );
        String projectName = artifactId + "-" + assessmentRunId;
        Path manifest = outputRoot.resolve("preprocessing").resolve(projectName).resolve("manifest.json");
        Files.createDirectories(manifest.getParent());
        Files.writeString(manifest, "{\"stage\":\"preprocessing\"}");
        Path receipt = outputRoot.resolve("result").resolve(projectName).resolve("receipts").resolve("startup.json");
        Files.createDirectories(receipt.getParent());
        Files.writeString(receipt, "{\"status\":\"completed\"}");

        MvcResult result = gatewayMvc.perform(get(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/intermediate-results",
                        artifactId,
                        assessmentRunId
                ))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.artifactId").value(artifactId))
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
        String artifactId = createArtifact(gatewayMvc, "preview artifact");
        gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId).file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted())
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );
        String projectName = artifactId + "-" + assessmentRunId;
        Path manifest = outputRoot.resolve("preprocessing").resolve(projectName).resolve("large.json");
        Files.createDirectories(manifest.getParent());
        Files.writeString(
                manifest,
                "HEAD" + "x".repeat(8_188) + "TAIL_MARKER",
                StandardCharsets.UTF_8
        );

        MvcResult result = gatewayMvc.perform(get(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/intermediate-results",
                        artifactId,
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
        String artifactId = createArtifact(gatewayMvc, "sensitive preview artifact");
        gatewayMvc.perform(multipart("/api/vca/{artifactId}/images", artifactId).file(
                        new MockMultipartFile("file", "front.jpg", "image/jpeg", JPEG_BYTES)))
                .andExpect(status().isCreated());
        MvcResult runResult = gatewayMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
                .andExpect(status().isAccepted())
                .andReturn();
        String assessmentRunId = JsonPath.read(
                runResult.getResponse().getContentAsString(),
                "$.assessmentRunId"
        );
        String projectName = artifactId + "-" + assessmentRunId;
        Path ragFile = outputRoot.resolve("rag").resolve(projectName).resolve("prompt_rag_results.jsonl");
        Path promptFile = outputRoot.resolve("prompt_generating").resolve(projectName).resolve("prompt.json");
        Files.createDirectories(ragFile.getParent());
        Files.createDirectories(promptFile.getParent());
        Files.writeString(ragFile, "{\"snippet_text\":\"sensitive retrieved evidence\"}");
        Files.writeString(promptFile, "{\"prompt\":\"sensitive prompt text\"}");

        MvcResult result = gatewayMvc.perform(get(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/intermediate-results",
                        artifactId,
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

        String artifactId = createArtifact(mockMvc, "empty artifact");

        mockMvc.perform(get("/api/vca/{artifactId}", artifactId))
                .andExpect(status().isOk());

        mockMvc.perform(get(
                        "/api/vca/{artifactId}/runs/{assessmentRunId}/report",
                        artifactId,
                        "12345678-1234-5678-1234-567812345678"
                ))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("RUN_NOT_FOUND"));

        mockMvc.perform(post("/api/vca/{artifactId}/runs", artifactId))
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
        String artifactId = createArtifact(mockMvc, "delete artifact");
        MvcResult presignResult = mockMvc.perform(post("/api/vca/{artifactId}/images/presign", artifactId)
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

        mockMvc.perform(post("/api/vca/{artifactId}/images/{imageId}/complete", artifactId, imageId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
                                """))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("SHA256_MISMATCH"));

        mockMvc.perform(delete("/api/vca/{artifactId}/images/{imageId}", artifactId, imageId))
                .andExpect(status().isNoContent());

        mockMvc.perform(post("/api/vca/{artifactId}/images/{imageId}/complete", artifactId, imageId)
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
