package com.aivle.conservation_backend.xray_api.service;

import com.aivle.conservation_backend.xray_api.client.XrayStitchClient;
import com.aivle.conservation_backend.xray_api.dto.XrayAiJobRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayJobResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayJobStatusResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;
import java.util.regex.Pattern;

@Service
public class XrayStitchService {

    private static final Pattern SAFE_ARTIFACT_ID =
            Pattern.compile("^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$");

    private final XrayStitchClient xrayStitchClient;
    private final ObjectMapper objectMapper;

    private final Path storageRoot;
    private final String containerRoot;
    private final String configName;

    /*
     * DB를 사용하지 않으므로 현재 실행 중인 Spring Boot 메모리에
     * 작업 상태를 임시로 보관한다.
     */
    private final ConcurrentMap<String, XrayJobStatusResponse> jobs =
            new ConcurrentHashMap<>();

    public XrayStitchService(
            XrayStitchClient xrayStitchClient,
            ObjectMapper objectMapper,
            @Value("${xray.storage.local-root}") String localRoot,
            @Value("${xray.storage.container-root}") String containerRoot,
            @Value("${xray.ai.config-name}") String configName
    ) {
        this.xrayStitchClient = xrayStitchClient;
        this.objectMapper = objectMapper;
        this.storageRoot = Path.of(localRoot).toAbsolutePath().normalize();
        this.containerRoot = removeTrailingSlash(containerRoot);
        this.configName = configName;
    }

    public XrayJobResponse createJob(
            String artifactId,
            List<MultipartFile> colorFiles,
            List<MultipartFile> xrayFiles
    ) {
        validateRequest(artifactId, colorFiles, xrayFiles);

        String jobId = UUID.randomUUID().toString();

        Path jobDirectory = storageRoot.resolve(jobId);
        Path colorDirectory = jobDirectory.resolve("inputs").resolve("color");
        Path xrayDirectory = jobDirectory.resolve("inputs").resolve("xray");
        Path outputDirectory = jobDirectory.resolve("outputs");

        String containerJobDirectory = containerRoot + "/" + jobId;

        XrayAiJobRequest aiRequest = new XrayAiJobRequest(
                jobId,
                artifactId,
                containerJobDirectory + "/inputs/color",
                containerJobDirectory + "/inputs/xray",
                containerJobDirectory + "/outputs",
                configName
        );

        try {
            Files.createDirectories(colorDirectory);
            Files.createDirectories(xrayDirectory);
            Files.createDirectories(outputDirectory);

            saveFiles(colorFiles, colorDirectory);
            saveFiles(xrayFiles, xrayDirectory);

            XrayJobStatusResponse pendingStatus =
                    new XrayJobStatusResponse(
                            jobId,
                            artifactId,
                            "PENDING",
                            "X-ray stitching job was created.",
                            null,
                            null
                    );

            jobs.put(jobId, pendingStatus);
            writeJobJson(jobDirectory, aiRequest, pendingStatus);

            /*
             * FastAPI에는 파일을 다시 보내지 않고
             * Docker 컨테이너 내부 경로가 포함된 JSON만 전송한다.
             */
            XrayJobResponse aiResponse = xrayStitchClient.createJob(aiRequest);

            if (aiResponse == null) {
                throw new IllegalStateException(
                        "FastAPI returned an empty job response."
                );
            }

            return aiResponse;

        } catch (Exception e) {
            XrayJobStatusResponse failedStatus =
                    new XrayJobStatusResponse(
                            jobId,
                            artifactId,
                            "FAILED",
                            "Failed to create X-ray stitching job.",
                            null,
                            e.getMessage()
                    );

            jobs.put(jobId, failedStatus);

            try {
                if (Files.exists(jobDirectory)) {
                    writeJobJson(jobDirectory, aiRequest, failedStatus);
                }
            } catch (IOException ignored) {
                // 원래 발생한 예외를 유지한다.
            }

            throw new IllegalStateException(
                    "Failed to create X-ray stitching job: " + jobId,
                    e
            );
        }
    }

    public XrayJobStatusResponse getLocalJobStatus(String jobId) {
        XrayJobStatusResponse status = xrayStitchClient.getJobStatus(jobId);
        if (status == null) {
            throw new IllegalStateException(
                    "FastAPI returned an empty job status: " + jobId
            );
        }

        String resultUrl = status.resultUrl();
        if ("COMPLETED".equalsIgnoreCase(status.status())) {
            resultUrl = "/api/xray/stitch/jobs/" + jobId + "/result";
        }

        return new XrayJobStatusResponse(
                status.jobId(),
                status.artifactId(),
                status.status(),
                status.message(),
                resultUrl,
                status.errorMessage()
        );
    }

    public Resource getResult(String jobId) {
        XrayJobStatusResponse status = getLocalJobStatus(jobId);
        if (!"COMPLETED".equalsIgnoreCase(status.status())) {
            throw new IllegalStateException(
                    "X-ray stitching result is not ready: " + status.status()
            );
        }

        Path jobDirectory = resolveJobDirectory(jobId);
        Path resultPath = jobDirectory
                .resolve("outputs")
                .resolve("assembly")
                .resolve("artifacts")
                .resolve(status.artifactId())
                .resolve("assembled_xray.png")
                .normalize();

        if (!resultPath.startsWith(jobDirectory)) {
            throw new IllegalStateException(
                    "Invalid X-ray result path: " + resultPath
            );
        }
        if (!Files.isRegularFile(resultPath)) {
            throw new IllegalStateException(
                    "X-ray result file was not found: " + resultPath
            );
        }

        return new FileSystemResource(resultPath);
    }

    /**
     * 조각별 배치 정보를 반환한다.
     *
     * 결합 엔진이 만든 layout.json 을 그대로 내려준다. 조각마다
     * 어떤 위치와 각도로 놓였는지가 담겨 있어, 화면에서 수동
     * 보정을 하거나 원본 조각의 탐지 좌표를 결합본 좌표로
     * 옮길 때 쓴다.
     *
     * 파일을 그대로 전달하는 이유는, 엔진이 항목을 추가해도
     * 중간 계층을 고치지 않아도 되기 때문이다.
     */
    public String getLayout(String jobId) {
        XrayJobStatusResponse status = getLocalJobStatus(jobId);

        if (!"COMPLETED".equalsIgnoreCase(status.status())) {
            throw new IllegalStateException(
                    "X-ray stitching result is not ready: " + status.status()
            );
        }

        Path jobDirectory = resolveJobDirectory(jobId);
        Path layoutPath = jobDirectory
                .resolve("outputs")
                .resolve("assembly")
                .resolve("artifacts")
                .resolve(status.artifactId())
                .resolve("layout.json")
                .normalize();

        // 경로 조작으로 작업 폴더 밖 파일을 읽는 것을 막는다
        if (!layoutPath.startsWith(jobDirectory)) {
            throw new IllegalStateException(
                    "Invalid X-ray layout path: " + layoutPath
            );
        }

        if (!Files.isReadable(layoutPath)) {
            throw new IllegalStateException(
                    "X-ray layout file is not available: " + layoutPath
            );
        }

        try {
            return Files.readString(layoutPath, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new IllegalStateException(
                    "Failed to read X-ray layout: " + layoutPath, e
            );
        }
    }

    private Path resolveJobDirectory(String jobId) {
        final String canonicalJobId;
        try {
            canonicalJobId = UUID.fromString(jobId).toString();
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("Invalid jobId: " + jobId, e);
        }

        Path jobDirectory = storageRoot.resolve(canonicalJobId).normalize();
        if (!jobDirectory.getParent().equals(storageRoot)) {
            throw new IllegalArgumentException("Invalid jobId: " + jobId);
        }
        return jobDirectory;
    }

    private void saveFiles(
            List<MultipartFile> files,
            Path targetDirectory
    ) throws IOException {
        Set<String> usedFileNames = new HashSet<>();

        for (MultipartFile file : files) {
            if (file == null || file.isEmpty()) {
                throw new IllegalArgumentException(
                        "Empty upload file is not allowed."
                );
            }

            String originalFileName = file.getOriginalFilename();

            if (originalFileName == null || originalFileName.isBlank()) {
                throw new IllegalArgumentException(
                        "The upload file name is missing."
                );
            }

            String safeFileName = Path.of(originalFileName)
                    .getFileName()
                    .toString();

            if (!usedFileNames.add(safeFileName)) {
                throw new IllegalArgumentException(
                        "Duplicate upload file name: " + safeFileName
                );
            }

            Path targetFile = targetDirectory
                    .resolve(safeFileName)
                    .normalize();

            if (!targetFile.getParent().equals(targetDirectory.normalize())) {
                throw new IllegalArgumentException(
                        "Invalid upload file path: " + originalFileName
                );
            }

            try (InputStream inputStream = file.getInputStream()) {
                Files.copy(
                        inputStream,
                        targetFile,
                        StandardCopyOption.REPLACE_EXISTING
                );
            }
        }
    }

    private void writeJobJson(
            Path jobDirectory,
            XrayAiJobRequest aiRequest,
            XrayJobStatusResponse status
    ) throws IOException {
        Map<String, Object> jobData = new LinkedHashMap<>();

        jobData.put("jobId", aiRequest.jobId());
        jobData.put("artifactId", aiRequest.artifactId());
        jobData.put("status", status.status());
        jobData.put("message", status.message());
        jobData.put("errorMessage", status.errorMessage());
        jobData.put(
                "windowsJobDirectory",
                jobDirectory.toAbsolutePath().toString()
        );
        jobData.put("colorDirectory", aiRequest.colorDirectory());
        jobData.put("xrayDirectory", aiRequest.xrayDirectory());
        jobData.put("outputDirectory", aiRequest.outputDirectory());
        jobData.put("configName", aiRequest.configName());
        jobData.put("updatedAt", Instant.now().toString());

        objectMapper
                .writerWithDefaultPrettyPrinter()
                .writeValue(
                        jobDirectory.resolve("job.json").toFile(),
                        jobData
                );
    }

    private void validateRequest(
            String artifactId,
            List<MultipartFile> colorFiles,
            List<MultipartFile> xrayFiles
    ) {
        if (artifactId == null || !SAFE_ARTIFACT_ID.matcher(artifactId).matches()) {
            throw new IllegalArgumentException(
                    "artifactId must contain only letters, numbers, '_' or '-'."
            );
        }

        if (colorFiles == null || colorFiles.size() != 1) {
            throw new IllegalArgumentException(
                    "Exactly one color reference image is required."
            );
        }

        if (xrayFiles == null || xrayFiles.size() < 2) {
            throw new IllegalArgumentException(
                    "At least two X-ray fragment images are required."
            );
        }
    }

    private static String removeTrailingSlash(String value) {
        String result = value;

        while (result.endsWith("/")) {
            result = result.substring(0, result.length() - 1);
        }

        return result;
    }
}
