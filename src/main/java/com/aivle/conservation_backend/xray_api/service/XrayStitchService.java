package com.aivle.conservation_backend.xray_api.service;

import com.aivle.conservation_backend.xray_api.client.XrayStitchClient;
import com.aivle.conservation_backend.xray_api.dto.XrayAiJobRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayJobResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayJobStatusResponse;
import tools.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

@Service
public class XrayStitchService {

    private final XrayStitchClient xrayStitchClient;
    private final ObjectMapper objectMapper;

    private final Path windowsRoot;
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
        this.windowsRoot = Path.of(localRoot).toAbsolutePath().normalize();
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

        Path jobDirectory = windowsRoot.resolve(jobId);
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
        return xrayStitchClient.getJobStatus(jobId);
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

            /*
             * ../../ 등의 경로 문자열을 제거하고 파일명만 사용한다.
             */
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
        if (artifactId == null || artifactId.isBlank()) {
            throw new IllegalArgumentException(
                    "artifactId is required."
            );
        }

        if (colorFiles == null || colorFiles.isEmpty()) {
            throw new IllegalArgumentException(
                    "At least one color image is required."
            );
        }

        if (xrayFiles == null || xrayFiles.isEmpty()) {
            throw new IllegalArgumentException(
                    "At least one X-ray image is required."
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