package com.aivle.conservation_backend.xray_api.service;

import com.aivle.conservation_backend.xray_api.client.XrayStitchClient;
import com.aivle.conservation_backend.xray_api.dto.XrayAiJobRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayFinalLayoutRequest;
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
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.math.BigInteger;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class XrayStitchService {

    private static final Pattern SAFE_ARTIFACT_ID =
            Pattern.compile("^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$");
    private static final Pattern NATURAL_PART = Pattern.compile("\\d+|\\D+");
    private static final Set<String> XRAY_IMAGE_EXTENSIONS = Set.of(
            ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"
    );

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

    /**
     * 결합 엔진과 동일하게 파일명을 natural sort한 원본 X-ray 파일 순서를 반환한다.
     * layout.json의 originalSourceIndex 검증에 사용한다.
     */
    public List<String> getOrderedXraySourceFileNames(String jobId) {
        requireCompletedJob(jobId);

        Path jobDirectory = resolveJobDirectory(jobId);
        Path xrayDirectory = jobDirectory
                .resolve("inputs")
                .resolve("xray")
                .normalize();

        if (!xrayDirectory.startsWith(jobDirectory)
                || !Files.isDirectory(xrayDirectory)) {
            throw new IllegalStateException(
                    "X-ray source directory is not available: " + xrayDirectory
            );
        }

        try (var stream = Files.list(xrayDirectory)) {
            List<String> fileNames = stream
                    .filter(Files::isRegularFile)
                    .map(path -> path.getFileName().toString())
                    .filter(this::isSupportedXrayImage)
                    .sorted(this::compareNaturalFileNames)
                    .toList();

            if (fileNames.isEmpty()) {
                throw new IllegalStateException(
                        "No X-ray source images were found: " + xrayDirectory
                );
            }
            return fileNames;
        } catch (IOException e) {
            throw new IllegalStateException(
                    "Failed to list X-ray source images: " + xrayDirectory,
                    e
            );
        }
    }

    /**
     * Konva에서 확정한 이동/회전 값을 자동 결합 layout에 반영하여
     * layout.final.json으로 저장한다.
     *
     * 원본 layout.json은 자동 결합 결과의 audit trail로 그대로 보존한다.
     */
    public String saveFinalLayout(
            String jobId,
            XrayFinalLayoutRequest request
    ) {
        XrayJobStatusResponse status = requireCompletedJob(jobId);
        validateFinalLayoutRequest(request);

        Path outputDirectory = resolveArtifactOutputDirectory(
                jobId,
                status.artifactId()
        );
        Path baseLayoutPath = outputDirectory.resolve("layout.json").normalize();
        Path finalLayoutPath = outputDirectory.resolve("layout.final.json").normalize();

        validatePathInside(outputDirectory, baseLayoutPath, "layout");
        validatePathInside(outputDirectory, finalLayoutPath, "final layout");

        if (!Files.isReadable(baseLayoutPath)) {
            throw new IllegalStateException(
                    "X-ray layout file is not available: " + baseLayoutPath
            );
        }

        try {
            @SuppressWarnings("unchecked")
            Map<String, Object> layout = objectMapper.readValue(
                    baseLayoutPath.toFile(),
                    Map.class
            );

            List<Map<String, Object>> layoutFragments = getLayoutFragments(layout);
            Map<Integer, XrayFinalLayoutRequest.FragmentTransform> transforms =
                    indexFinalTransforms(request.fragments());

            if (transforms.size() != layoutFragments.size()) {
                throw new IllegalArgumentException(
                        "Final layout must contain every fragment. expected="
                                + layoutFragments.size()
                                + ", actual="
                                + transforms.size()
                );
            }

            for (Map<String, Object> fragment : layoutFragments) {
                int index = requireInt(fragment, "index");
                XrayFinalLayoutRequest.FragmentTransform transform =
                        transforms.get(index);

                if (transform == null) {
                    throw new IllegalArgumentException(
                            "Missing final transform for fragment index: " + index
                    );
                }

                validateFragmentIdentity(fragment, transform);
                applyFinalTransform(fragment, transform);
            }

            layout.put("layoutStage", "FINAL");
            layout.put("baseLayoutFile", "layout.json");
            layout.put("finalizedAt", Instant.now().toString());

            writeJsonAtomically(finalLayoutPath, layout);
            return Files.readString(finalLayoutPath, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new IllegalStateException(
                    "Failed to save final X-ray layout: " + finalLayoutPath,
                    e
            );
        }
    }

    /**
     * Konva 보정까지 반영된 layout.final.json을 반환한다.
     */
    public String getFinalLayout(String jobId) {
        XrayJobStatusResponse status = requireCompletedJob(jobId);
        Path outputDirectory = resolveArtifactOutputDirectory(
                jobId,
                status.artifactId()
        );
        Path finalLayoutPath = outputDirectory.resolve("layout.final.json").normalize();

        validatePathInside(outputDirectory, finalLayoutPath, "final layout");

        if (!Files.isReadable(finalLayoutPath)) {
            throw new IllegalStateException(
                    "Final X-ray layout is not available: " + finalLayoutPath
            );
        }

        try {
            return Files.readString(finalLayoutPath, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new IllegalStateException(
                    "Failed to read final X-ray layout: " + finalLayoutPath,
                    e
            );
        }
    }

    private XrayJobStatusResponse requireCompletedJob(String jobId) {
        XrayJobStatusResponse status = getLocalJobStatus(jobId);
        if (!"COMPLETED".equalsIgnoreCase(status.status())) {
            throw new IllegalStateException(
                    "X-ray stitching result is not ready: " + status.status()
            );
        }
        return status;
    }

    private Path resolveArtifactOutputDirectory(
            String jobId,
            String artifactId
    ) {
        Path jobDirectory = resolveJobDirectory(jobId);
        Path outputDirectory = jobDirectory
                .resolve("outputs")
                .resolve("assembly")
                .resolve("artifacts")
                .resolve(artifactId)
                .normalize();

        if (!outputDirectory.startsWith(jobDirectory)) {
            throw new IllegalStateException(
                    "Invalid X-ray artifact output path: " + outputDirectory
            );
        }
        return outputDirectory;
    }

    private void validateFinalLayoutRequest(XrayFinalLayoutRequest request) {
        if (request == null
                || request.fragments() == null
                || request.fragments().isEmpty()) {
            throw new IllegalArgumentException(
                    "Final layout fragments are required."
            );
        }
    }

    private Map<Integer, XrayFinalLayoutRequest.FragmentTransform> indexFinalTransforms(
            List<XrayFinalLayoutRequest.FragmentTransform> fragments
    ) {
        Map<Integer, XrayFinalLayoutRequest.FragmentTransform> result =
                new LinkedHashMap<>();

        for (XrayFinalLayoutRequest.FragmentTransform fragment : fragments) {
            if (fragment == null) {
                throw new IllegalArgumentException(
                        "Final layout fragment must not be null."
                );
            }
            if (!Double.isFinite(fragment.centerX())
                    || !Double.isFinite(fragment.centerY())
                    || !Double.isFinite(fragment.rotationDeg())) {
                throw new IllegalArgumentException(
                        "Final layout transform contains a non-finite value: index="
                                + fragment.index()
                );
            }

            XrayFinalLayoutRequest.FragmentTransform previous = result.put(
                    fragment.index(),
                    fragment
            );
            if (previous != null) {
                throw new IllegalArgumentException(
                        "Duplicate final layout fragment index: " + fragment.index()
                );
            }
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> getLayoutFragments(
            Map<String, Object> layout
    ) {
        Object fragmentsValue = layout.get("fragments");
        if (!(fragmentsValue instanceof List<?> rawFragments)) {
            throw new IllegalStateException(
                    "layout.json does not contain a valid fragments array."
            );
        }

        List<Map<String, Object>> fragments = new ArrayList<>();
        for (Object value : rawFragments) {
            if (!(value instanceof Map<?, ?> rawFragment)) {
                throw new IllegalStateException(
                        "layout.json contains an invalid fragment entry."
                );
            }
            fragments.add((Map<String, Object>) rawFragment);
        }
        return fragments;
    }

    private void validateFragmentIdentity(
            Map<String, Object> fragment,
            XrayFinalLayoutRequest.FragmentTransform transform
    ) {
        int sourceIndex = requireInt(fragment, "originalSourceIndex");
        int subfragmentIndex = requireInt(fragment, "subfragmentIndex");
        String sourceName = requireString(fragment, "originalSourceName");

        if (sourceIndex != transform.originalSourceIndex()
                || subfragmentIndex != transform.subfragmentIndex()
                || !sourceName.equals(transform.originalSourceName())) {
            throw new IllegalArgumentException(
                    "Final layout fragment identity mismatch: index="
                            + transform.index()
            );
        }
    }

    private void applyFinalTransform(
            Map<String, Object> fragment,
            XrayFinalLayoutRequest.FragmentTransform transform
    ) {
        List<?> crop = requireList(fragment, "originalCropBBoxXYWH", 4);
        double width = requireNumber(crop.get(2), "originalCropBBoxXYWH[2]");
        double height = requireNumber(crop.get(3), "originalCropBBoxXYWH[3]");

        if (width <= 0 || height <= 0) {
            throw new IllegalStateException(
                    "Invalid original crop size for fragment index: "
                            + transform.index()
            );
        }

        double rotationDeg = normalizeAngle(transform.rotationDeg());
        double radians = Math.toRadians(rotationDeg);
        double cos = Math.cos(radians);
        double sin = Math.sin(radians);

        double localCenterX = (width - 1.0) / 2.0;
        double localCenterY = (height - 1.0) / 2.0;

        double tx = transform.centerX()
                - (cos * localCenterX - sin * localCenterY);
        double ty = transform.centerY()
                - (sin * localCenterX + cos * localCenterY);

        List<List<Double>> affineMatrix = List.of(
                List.of(cos, -sin, tx),
                List.of(sin, cos, ty),
                List.of(0.0, 0.0, 1.0)
        );

        fragment.put("centerX", transform.centerX());
        fragment.put("centerY", transform.centerY());
        fragment.put("rotationDeg", rotationDeg);
        fragment.put("scale", 1.0);
        fragment.put("affineMatrix", affineMatrix);
    }

    private int requireInt(Map<String, Object> object, String key) {
        Object value = object.get(key);
        if (!(value instanceof Number number)) {
            throw new IllegalStateException(
                    "layout.json field is missing or invalid: " + key
            );
        }
        return number.intValue();
    }

    private String requireString(Map<String, Object> object, String key) {
        Object value = object.get(key);
        if (!(value instanceof String text) || text.isBlank()) {
            throw new IllegalStateException(
                    "layout.json field is missing or invalid: " + key
            );
        }
        return text;
    }

    private List<?> requireList(
            Map<String, Object> object,
            String key,
            int expectedSize
    ) {
        Object value = object.get(key);
        if (!(value instanceof List<?> list) || list.size() != expectedSize) {
            throw new IllegalStateException(
                    "layout.json field is missing or invalid: " + key
            );
        }
        return list;
    }

    private double requireNumber(Object value, String fieldName) {
        if (!(value instanceof Number number)) {
            throw new IllegalStateException(
                    "layout.json field is missing or invalid: " + fieldName
            );
        }
        double result = number.doubleValue();
        if (!Double.isFinite(result)) {
            throw new IllegalStateException(
                    "layout.json field is not finite: " + fieldName
            );
        }
        return result;
    }

    private double normalizeAngle(double angle) {
        double normalized = angle % 360.0;
        if (normalized >= 180.0) {
            normalized -= 360.0;
        } else if (normalized < -180.0) {
            normalized += 360.0;
        }
        return normalized;
    }

    private void validatePathInside(
            Path parent,
            Path child,
            String description
    ) {
        if (!child.startsWith(parent)) {
            throw new IllegalStateException(
                    "Invalid X-ray " + description + " path: " + child
            );
        }
    }

    private void writeJsonAtomically(
            Path target,
            Map<String, Object> value
    ) throws IOException {
        Path temporary = target.resolveSibling(target.getFileName() + ".tmp");
        objectMapper
                .writerWithDefaultPrettyPrinter()
                .writeValue(temporary.toFile(), value);

        try {
            Files.move(
                    temporary,
                    target,
                    StandardCopyOption.ATOMIC_MOVE,
                    StandardCopyOption.REPLACE_EXISTING
            );
        } catch (AtomicMoveNotSupportedException e) {
            Files.move(
                    temporary,
                    target,
                    StandardCopyOption.REPLACE_EXISTING
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

    private boolean isSupportedXrayImage(String fileName) {
        String lower = fileName.toLowerCase(Locale.ROOT);
        int dot = lower.lastIndexOf('.');
        if (dot < 0) {
            return false;
        }
        return XRAY_IMAGE_EXTENSIONS.contains(lower.substring(dot));
    }

    private int compareNaturalFileNames(String left, String right) {
        List<String> leftParts = naturalParts(left);
        List<String> rightParts = naturalParts(right);
        int count = Math.min(leftParts.size(), rightParts.size());

        for (int i = 0; i < count; i++) {
            String a = leftParts.get(i);
            String b = rightParts.get(i);
            boolean aNumber = Character.isDigit(a.charAt(0));
            boolean bNumber = Character.isDigit(b.charAt(0));

            int compared;
            if (aNumber && bNumber) {
                compared = new BigInteger(a).compareTo(new BigInteger(b));
                if (compared == 0) {
                    compared = Integer.compare(a.length(), b.length());
                }
            } else {
                compared = a.toLowerCase(Locale.ROOT)
                        .compareTo(b.toLowerCase(Locale.ROOT));
            }

            if (compared != 0) {
                return compared;
            }
        }

        return Integer.compare(leftParts.size(), rightParts.size());
    }

    private List<String> naturalParts(String fileName) {
        List<String> parts = new ArrayList<>();
        Matcher matcher = NATURAL_PART.matcher(fileName);
        while (matcher.find()) {
            parts.add(matcher.group());
        }
        return parts;
    }

    private static String removeTrailingSlash(String value) {
        String result = value;

        while (result.endsWith("/")) {
            result = result.substring(0, result.length() - 1);
        }

        return result;
    }
}
