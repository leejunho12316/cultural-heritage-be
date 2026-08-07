package com.aivle.conservation_backend.xray_api.service;

import com.aivle.conservation_backend.xray_api.client.XrayStitchClient;
import com.aivle.conservation_backend.xray_api.domain.XrayJob;
import com.aivle.conservation_backend.xray_api.domain.XrayJobStatus;
import com.aivle.conservation_backend.xray_api.dto.XrayAiFinalizationRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayAiStitchRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayFinalLayoutRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayJobResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayJobStatusResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchCallbackRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchDtos.PrepareRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchDtos.PrepareResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchDtos.ReconcileResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchDtos.UploadTarget;
import com.aivle.conservation_backend.xray_api.repository.XrayJobRepository;
import com.aivle.conservation_backend.xray_api.storage.XrayS3Keys;
import com.aivle.conservation_backend.xray_api.storage.XrayS3Service;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class XrayStitchService {

    private static final Pattern NATURAL_PART = Pattern.compile("\\d+|\\D+");
    private static final Set<String> XRAY_IMAGE_EXTENSIONS = Set.of(
            ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"
    );

    private final XrayStitchClient xrayStitchClient;
    private final XrayS3Service s3Service;
    private final XrayJobRepository jobRepository;
    private final ObjectMapper objectMapper;
    private final String configName;
    private final String callbackUrl;
    private final String callbackToken;
    private final long finalizationWaitSeconds;

    public XrayStitchService(
            XrayStitchClient xrayStitchClient,
            XrayS3Service s3Service,
            XrayJobRepository jobRepository,
            ObjectMapper objectMapper,
            @Value("${xray.ai.config-name}") String configName,
            @Value("${xray.stitch.callback-url:http://localhost:8080/api/xray/stitch/callback}") String callbackUrl,
            @Value("${xray.stitch.callback-token:}") String callbackToken,
            @Value("${xray.stitch.finalization-wait-seconds:300}") long finalizationWaitSeconds
    ) {
        this.xrayStitchClient = xrayStitchClient;
        this.s3Service = s3Service;
        this.jobRepository = jobRepository;
        this.objectMapper = objectMapper;
        this.configName = configName;
        this.callbackUrl = callbackUrl;
        this.callbackToken = callbackToken;
        this.finalizationWaitSeconds = finalizationWaitSeconds;
    }

    // ---------------------------------------------------------------------
    // S3 direct-upload flow
    // ---------------------------------------------------------------------

    @Transactional
    public PrepareResponse prepare(PrepareRequest request) {
        validatePrepare(request);
        UUID artifactId = parseUuid(request.artifactId(), "artifactId");
        UUID jobId = UUID.randomUUID();

        List<String> xrayNames = request.xrayFileNames().stream()
                .map(this::safeName)
                .toList();
        String colorName = safeName(request.colorFileName());

        XrayJob job = XrayJob.create(jobId, artifactId);
        job.rememberInputs(colorName, xrayNames);
        jobRepository.save(job);

        String artifact = artifactId.toString();
        String colorKey = XrayS3Keys.colorInput(artifact, colorName);
        UploadTarget color = new UploadTarget(
                request.colorFileName(),
                colorKey,
                // No Content-Type is signed. Browser-selected MIME values then cannot
                // cause a signature mismatch (the prior 403 issue).
                s3Service.presignInputPut(colorKey, null)
        );

        List<UploadTarget> xrays = new ArrayList<>();
        for (int i = 0; i < xrayNames.size(); i++) {
            String key = XrayS3Keys.xrayInput(artifact, xrayNames.get(i));
            xrays.add(new UploadTarget(
                    request.xrayFileNames().get(i),
                    key,
                    s3Service.presignInputPut(key, null)
            ));
        }

        return new PrepareResponse(
                jobId.toString(), artifact, job.getStatus().name(), color, xrays
        );
    }

    /**
     * Transitional endpoint for the old multipart FE. It no longer writes a
     * shared folder: Spring uploads to the same S3 keys and starts the URL job.
     */
    public XrayJobResponse createJob(
            String artifactId,
            List<MultipartFile> colorFiles,
            List<MultipartFile> xrayFiles
    ) {
        if (colorFiles == null || colorFiles.size() != 1 || colorFiles.get(0).isEmpty()) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "Exactly one non-empty color reference image is required."
            );
        }
        if (xrayFiles == null || xrayFiles.size() < 2 || xrayFiles.stream().anyMatch(MultipartFile::isEmpty)) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "At least two non-empty X-ray fragment images are required."
            );
        }

        String colorName = originalName(colorFiles.get(0));
        List<String> xrayNames = xrayFiles.stream().map(this::originalName).toList();
        PrepareResponse prepared = prepare(new PrepareRequest(artifactId, colorName, xrayNames));

        try {
            s3Service.putBytes(
                    prepared.color().s3Key(),
                    colorFiles.get(0).getBytes(),
                    blankToNull(colorFiles.get(0).getContentType())
            );
            for (int i = 0; i < xrayFiles.size(); i++) {
                MultipartFile file = xrayFiles.get(i);
                s3Service.putBytes(
                        prepared.xrays().get(i).s3Key(),
                        file.getBytes(),
                        blankToNull(file.getContentType())
                );
            }
        } catch (IOException e) {
            markFailed(UUID.fromString(prepared.jobId()), "S3 upload failed.", e.getMessage());
            throw new IllegalStateException("Failed to upload X-ray inputs to S3.", e);
        }

        start(prepared.jobId(), colorName, xrayNames);
        return new XrayJobResponse(
                prepared.jobId(), prepared.artifactId(), "RUNNING", "X-ray stitching is running."
        );
    }

    // Do not keep a database transaction open while calling FastAPI.
    // Repository operations commit independently, so a rejected AI request can
    // still persist the FAILED state instead of rolling the entire method back.
    public XrayJobStatusResponse start(
            String jobIdValue,
            String colorFileName,
            List<String> xrayFileNames
    ) {
        UUID jobId = parseUuid(jobIdValue, "jobId");
        XrayJob job = requireJob(jobId);
        if (job.getStatus() == XrayJobStatus.RUNNING) {
            return toStatusResponse(job);
        }
        if (job.getStatus() == XrayJobStatus.FINALIZING) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Final rendering is already running.");
        }

        String colorName = safeName(
                colorFileName == null || colorFileName.isBlank()
                        ? job.getColorFileName()
                        : colorFileName
        );
        List<String> xrayNames = xrayFileNames == null || xrayFileNames.isEmpty()
                ? safeStoredNames(job.getXrayFileNames())
                : xrayFileNames.stream().map(this::safeName).toList();
        if (xrayNames.size() < 2) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "At least two X-ray file names are required.");
        }

        String artifactId = job.getArtifactId().toString();
        requireObject(XrayS3Keys.colorInput(artifactId, colorName), "color reference");
        for (String name : xrayNames) {
            requireObject(XrayS3Keys.xrayInput(artifactId, name), "X-ray fragment " + name);
        }

        XrayAiStitchRequest request = new XrayAiStitchRequest(
                jobId.toString(),
                artifactId,
                configName,
                new XrayAiStitchRequest.RemoteInput(
                        colorName,
                        s3Service.presignGet(XrayS3Keys.colorInput(artifactId, colorName))
                ),
                xrayNames.stream()
                        .map(name -> new XrayAiStitchRequest.RemoteInput(
                                name,
                                s3Service.presignGet(XrayS3Keys.xrayInput(artifactId, name))
                        ))
                        .toList(),
                new XrayAiStitchRequest.OutputPutUrls(
                        s3Service.presignOutputPut(XrayS3Keys.assembled(artifactId), "image/png"),
                        s3Service.presignOutputPut(XrayS3Keys.layout(artifactId), "application/json"),
                        s3Service.presignOutputPut(XrayS3Keys.report(artifactId), "application/json"),
                        s3Service.presignOutputPut(XrayS3Keys.finalizationBundle(artifactId), "application/zip")
                ),
                callbackUrl,
                callbackToken
        );

        job.rememberInputs(colorName, xrayNames);
        job.markRunning();
        jobRepository.save(job);

        try {
            xrayStitchClient.startStitch(request);
        } catch (RuntimeException e) {
            job.markFailed("FastAPI did not accept the X-ray job.", e.getMessage());
            jobRepository.save(job);
            throw e;
        }
        return toStatusResponse(job);
    }

    // ---------------------------------------------------------------------
    // Callback, status and reconciliation
    // ---------------------------------------------------------------------

    @Transactional
    public void handleCallback(XrayStitchCallbackRequest request, String suppliedToken) {
        validateCallbackToken(suppliedToken);
        if (request == null || request.jobId() == null || request.status() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "jobId and status are required.");
        }
        XrayJob job = requireJob(parseUuid(request.jobId(), "jobId"));
        if (request.artifactId() != null
                && !request.artifactId().equals(job.getArtifactId().toString())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Callback artifactId does not match the job.");
        }

        String status = request.status().trim().toUpperCase(Locale.ROOT);
        switch (status) {
            case "COMPLETED" -> {
                requireBaseOutputs(job.getArtifactId().toString());
                job.markCompleted(request.message());
            }
            case "FINALIZED" -> {
                requireFinalOutputs(job.getArtifactId().toString());
                job.markFinalized(request.message());
            }
            case "FAILED" -> job.markFailed(request.message(), request.errorMessage());
            case "RUNNING" -> job.markRunning();
            default -> throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "Unsupported callback status: " + status
            );
        }
        jobRepository.save(job);
    }

    @Transactional(readOnly = true)
    public XrayJobStatusResponse getLocalJobStatus(String jobId) {
        return toStatusResponse(requireJob(parseUuid(jobId, "jobId")));
    }

    @Transactional
    public ReconcileResponse reconcile(String jobIdValue) {
        XrayJob job = requireJob(parseUuid(jobIdValue, "jobId"));
        String previous = job.getStatus().name();
        String artifactId = job.getArtifactId().toString();

        if (hasFinalOutputs(artifactId)) {
            job.markFinalized("Recovered FINALIZED state from S3 outputs.");
        } else if (hasBaseOutputs(artifactId)) {
            job.markCompleted("Recovered COMPLETED state from S3 outputs.");
        }
        jobRepository.save(job);
        String current = job.getStatus().name();
        return new ReconcileResponse(
                job.getId().toString(), previous, current, !previous.equals(current), job.getMessage()
        );
    }

    // ---------------------------------------------------------------------
    // S3 result accessors (URL and compatibility byte resources)
    // ---------------------------------------------------------------------

    @Transactional(readOnly = true)
    public String getAssembledUrl(String jobId) {
        XrayJob job = requireCompletedJob(jobId);
        return s3Service.presignGet(XrayS3Keys.assembled(job.getArtifactId().toString()));
    }

    @Transactional(readOnly = true)
    public String getLayoutUrl(String jobId) {
        XrayJob job = requireCompletedJob(jobId);
        return s3Service.presignGet(XrayS3Keys.layout(job.getArtifactId().toString()));
    }

    @Transactional(readOnly = true)
    public String getReportUrl(String jobId) {
        XrayJob job = requireCompletedJob(jobId);
        return s3Service.presignGet(XrayS3Keys.report(job.getArtifactId().toString()));
    }

    @Transactional(readOnly = true)
    public String getFinalAssembledUrl(String jobId) {
        XrayJob job = requireFinalizedJobEntity(jobId);
        return s3Service.presignGet(XrayS3Keys.finalAssembled(job.getArtifactId().toString()));
    }

    public Resource getResult(String jobId) {
        XrayJob job = requireCompletedJob(jobId);
        return namedResource(
                s3Service.getBytes(XrayS3Keys.assembled(job.getArtifactId().toString())),
                "assembled-xray.png"
        );
    }

    public Resource getFinalResult(String jobId) {
        XrayJob job = requireFinalizedJobEntity(jobId);
        return namedResource(
                s3Service.getBytes(XrayS3Keys.finalAssembled(job.getArtifactId().toString())),
                "assembled-xray-final.png"
        );
    }

    public String getLayout(String jobId) {
        XrayJob job = requireCompletedJob(jobId);
        return s3Service.getString(XrayS3Keys.layout(job.getArtifactId().toString()));
    }

    public byte[] getOutputBytes(String jobId, String outputName) {
        XrayJob job = requireFinalizedJobEntity(jobId);
        String artifactId = job.getArtifactId().toString();
        String key = switch (outputName) {
            case "source-owner" -> XrayS3Keys.sourceOwner(artifactId);
            case "fragment-owner" -> XrayS3Keys.fragmentOwner(artifactId);
            case "seam-zone" -> XrayS3Keys.seamZone(artifactId);
            case "overlap-mask" -> XrayS3Keys.overlapMask(artifactId);
            case "provenance" -> XrayS3Keys.provenance(artifactId);
            default -> throw new IllegalArgumentException("Unknown final output: " + outputName);
        };
        return s3Service.getBytes(key);
    }

    // ---------------------------------------------------------------------
    // Manual final layout + remote final rendering
    // ---------------------------------------------------------------------

    public String saveFinalLayout(String jobIdValue, XrayFinalLayoutRequest request) {
        XrayJob job = requireCompletedJob(jobIdValue);
        validateFinalLayoutRequest(request);
        String artifactId = job.getArtifactId().toString();

            @SuppressWarnings("unchecked")
            Map<String, Object> layout = objectMapper.readValue(
                    s3Service.getString(XrayS3Keys.layout(artifactId)),
                    Map.class
            );
            List<Map<String, Object>> layoutFragments = getLayoutFragments(layout);
            Map<Integer, XrayFinalLayoutRequest.FragmentTransform> transforms =
                    indexFinalTransforms(request.fragments());

            if (transforms.size() != layoutFragments.size()) {
                throw new IllegalArgumentException(
                        "Final layout must contain every fragment. expected="
                                + layoutFragments.size() + ", actual=" + transforms.size()
                );
            }
            for (Map<String, Object> fragment : layoutFragments) {
                int index = requireInt(fragment, "index");
                XrayFinalLayoutRequest.FragmentTransform transform = transforms.get(index);
                if (transform == null) {
                    throw new IllegalArgumentException("Missing final transform for fragment index: " + index);
                }
                validateFragmentIdentity(fragment, transform);
                applyFinalTransform(fragment, transform);
            }
            layout.put("layoutStage", "FINAL");
            layout.put("baseLayoutFile", "layout.json");
            layout.put("finalizedAt", Instant.now().toString());

            String finalLayoutJson = objectMapper
                    .writerWithDefaultPrettyPrinter()
                    .writeValueAsString(layout);
            s3Service.putString(
                    XrayS3Keys.finalLayout(artifactId),
                    finalLayoutJson,
                    "application/json"
            );

            XrayAiFinalizationRequest aiRequest = new XrayAiFinalizationRequest(
                    job.getId().toString(),
                    artifactId,
                    s3Service.presignGet(XrayS3Keys.finalizationBundle(artifactId)),
                    s3Service.presignGet(XrayS3Keys.finalLayout(artifactId)),
                    new XrayAiFinalizationRequest.OutputPutUrls(
                            s3Service.presignOutputPut(XrayS3Keys.finalAssembled(artifactId), "image/png"),
                            s3Service.presignOutputPut(XrayS3Keys.sourceOwner(artifactId), "image/png"),
                            s3Service.presignOutputPut(XrayS3Keys.fragmentOwner(artifactId), "image/png"),
                            s3Service.presignOutputPut(XrayS3Keys.seamZone(artifactId), "image/png"),
                            s3Service.presignOutputPut(XrayS3Keys.overlapMask(artifactId), "image/png"),
                            s3Service.presignOutputPut(XrayS3Keys.provenance(artifactId), "application/json")
                    ),
                    callbackUrl,
                    callbackToken
            );

            job.markFinalizing();
            jobRepository.save(job);
            try {
                xrayStitchClient.startFinalization(aiRequest);
            } catch (RuntimeException e) {
                job.markFailed("FastAPI did not accept final rendering.", e.getMessage());
                jobRepository.save(job);
                throw e;
            }

            // Current main waited for server-side re-rendering before returning
            // from PUT layout/final. Preserve that FE contract while the actual
            // renderer runs asynchronously and stores its outputs in S3.
        waitForFinalization(job.getId(), artifactId);
        return finalLayoutJson;
    }

    public String getFinalLayout(String jobId) {
        XrayJob job = requireCompletedJob(jobId);
        String key = XrayS3Keys.finalLayout(job.getArtifactId().toString());
        if (!s3Service.objectExists(key)) {
            throw new IllegalStateException("Final X-ray layout is not available.");
        }
        return s3Service.getString(key);
    }

    public XrayJobStatusResponse requireFinalizedJob(String jobId) {
        return toStatusResponse(requireFinalizedJobEntity(jobId));
    }

    // ---------------------------------------------------------------------
    // Source ordering and S3 URL access for defect detection
    // ---------------------------------------------------------------------

    public List<String> getOrderedXraySourceFileNames(String jobId) {
        XrayJob job = requireCompletedJob(jobId);
        List<String> names = safeStoredNames(job.getXrayFileNames());
        if (names.isEmpty()) {
            String prefix = XrayS3Keys.xrayPrefix(job.getArtifactId().toString());
            names = s3Service.listKeys(prefix).stream()
                    .map(key -> key.substring(prefix.length()))
                    .filter(this::isSupportedXrayImage)
                    .toList();
        }
        return names.stream().sorted(this::compareNaturalFileNames).toList();
    }

    public List<String> getOrderedXraySourceUrls(String jobId) {
        XrayJob job = requireCompletedJob(jobId);
        String artifactId = job.getArtifactId().toString();
        return getOrderedXraySourceFileNames(jobId).stream()
                .map(name -> s3Service.presignGet(XrayS3Keys.xrayInput(artifactId, name)))
                .toList();
    }

    public List<Resource> getOrderedXraySourceResources(String jobId) {
        XrayJob job = requireCompletedJob(jobId);
        String artifactId = job.getArtifactId().toString();
        return getOrderedXraySourceFileNames(jobId).stream()
                .map(name -> (Resource) namedResource(
                        s3Service.getBytes(XrayS3Keys.xrayInput(artifactId, name)),
                        name
                ))
                .toList();
    }

    // ---------------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------------

    private XrayJob requireJob(UUID jobId) {
        return jobRepository.findById(jobId).orElseThrow(() ->
                new ResponseStatusException(HttpStatus.NOT_FOUND, "X-ray job not found: " + jobId)
        );
    }

    private XrayJob requireCompletedJob(String jobId) {
        XrayJob job = requireJob(parseUuid(jobId, "jobId"));
        if (job.getStatus() != XrayJobStatus.COMPLETED
                && job.getStatus() != XrayJobStatus.FINALIZING
                && job.getStatus() != XrayJobStatus.FINALIZED) {
            throw new IllegalStateException("X-ray stitching result is not ready: " + job.getStatus());
        }
        return job;
    }

    private XrayJob requireFinalizedJobEntity(String jobId) {
        XrayJob job = requireJob(parseUuid(jobId, "jobId"));
        if (job.getStatus() != XrayJobStatus.FINALIZED) {
            throw new IllegalStateException("Final X-ray result is not ready: " + job.getStatus());
        }
        return job;
    }

    private XrayJobStatusResponse toStatusResponse(XrayJob job) {
        String resultUrl = switch (job.getStatus()) {
            case COMPLETED, FINALIZING -> "/api/xray/stitch/jobs/" + job.getId() + "/result";
            case FINALIZED -> "/api/xray/stitch/jobs/" + job.getId() + "/result/final";
            default -> null;
        };
        return new XrayJobStatusResponse(
                job.getId().toString(),
                job.getArtifactId().toString(),
                job.getStatus().name(),
                job.getMessage(),
                resultUrl,
                job.getErrorMessage()
        );
    }

    private void validatePrepare(PrepareRequest request) {
        if (request == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Request body is required.");
        }
        parseUuid(request.artifactId(), "artifactId");
        if (request.colorFileName() == null || request.colorFileName().isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "colorFileName is required.");
        }
        if (request.xrayFileNames() == null || request.xrayFileNames().size() < 2) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "At least two xrayFileNames are required.");
        }
        if (request.xrayFileNames().stream().anyMatch(name -> name == null || name.isBlank())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "xrayFileNames must not contain blanks.");
        }
        List<String> safe = request.xrayFileNames().stream().map(this::safeName).toList();
        if (safe.stream().distinct().count() != safe.size()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "X-ray file names must be unique after sanitization.");
        }
    }

    private UUID parseUuid(String value, String field) {
        try {
            return UUID.fromString(value);
        } catch (Exception e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, field + " must be a UUID.");
        }
    }

    private String safeName(String fileName) {
        if (fileName == null || fileName.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "File name is missing.");
        }
        String normalized = fileName.replace('\\', '/');
        String result = normalized.substring(normalized.lastIndexOf('/') + 1).trim();
        if (result.isBlank() || ".".equals(result) || "..".equals(result)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Invalid file name: " + fileName);
        }
        return result;
    }

    private String originalName(MultipartFile file) {
        String name = file.getOriginalFilename();
        return safeName(name == null || name.isBlank() ? "upload.bin" : name);
    }

    private List<String> safeStoredNames(List<String> names) {
        if (names == null) {
            return List.of();
        }
        return names.stream().filter(name -> name != null && !name.isBlank()).map(this::safeName).toList();
    }

    private void requireObject(String key, String label) {
        if (!s3Service.objectExists(key)) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "S3 " + label + " has not been uploaded: " + key
            );
        }
    }

    private void requireBaseOutputs(String artifactId) {
        if (!hasBaseOutputs(artifactId)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "FastAPI callback arrived before required S3 outputs.");
        }
    }

    private boolean hasBaseOutputs(String artifactId) {
        return s3Service.objectExists(XrayS3Keys.assembled(artifactId))
                && s3Service.objectExists(XrayS3Keys.layout(artifactId))
                && s3Service.objectExists(XrayS3Keys.report(artifactId))
                && s3Service.objectExists(XrayS3Keys.finalizationBundle(artifactId));
    }

    private void requireFinalOutputs(String artifactId) {
        if (!hasFinalOutputs(artifactId)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "Finalization callback arrived before required S3 outputs.");
        }
    }

    private boolean hasFinalOutputs(String artifactId) {
        return s3Service.objectExists(XrayS3Keys.finalLayout(artifactId))
                && s3Service.objectExists(XrayS3Keys.finalAssembled(artifactId))
                && s3Service.objectExists(XrayS3Keys.sourceOwner(artifactId))
                && s3Service.objectExists(XrayS3Keys.fragmentOwner(artifactId))
                && s3Service.objectExists(XrayS3Keys.seamZone(artifactId))
                && s3Service.objectExists(XrayS3Keys.overlapMask(artifactId))
                && s3Service.objectExists(XrayS3Keys.provenance(artifactId));
    }


    private void waitForFinalization(UUID jobId, String artifactId) {
        long deadline = System.nanoTime()
                + java.util.concurrent.TimeUnit.SECONDS.toNanos(
                        Math.max(1L, finalizationWaitSeconds)
                );
        while (System.nanoTime() < deadline) {
            if (hasFinalOutputs(artifactId)) {
                XrayJob current = requireJob(jobId);
                if (current.getStatus() != XrayJobStatus.FINALIZED) {
                    current.markFinalized("Final X-ray rendering completed.");
                    jobRepository.save(current);
                }
                return;
            }

            XrayJob current = requireJob(jobId);
            if (current.getStatus() == XrayJobStatus.FAILED) {
                throw new IllegalStateException(
                        "Final X-ray rendering failed: " + current.getErrorMessage()
                );
            }
            try {
                Thread.sleep(1000L);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException("Interrupted while waiting for final X-ray rendering.", e);
            }
        }
        throw new ResponseStatusException(
                HttpStatus.GATEWAY_TIMEOUT,
                "Final X-ray rendering did not finish within "
                        + finalizationWaitSeconds + " seconds. The job may still complete; poll status or call /reconcile."
        );
    }

    private void validateCallbackToken(String suppliedToken) {
        if (callbackToken != null && !callbackToken.isBlank()
                && !callbackToken.equals(suppliedToken)) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "Invalid X-ray callback token.");
        }
    }

    private void markFailed(UUID jobId, String message, String error) {
        jobRepository.findById(jobId).ifPresent(job -> {
            job.markFailed(message, error);
            jobRepository.save(job);
        });
    }

    private ByteArrayResource namedResource(byte[] bytes, String fileName) {
        return new ByteArrayResource(bytes) {
            @Override
            public String getFilename() {
                return fileName;
            }
        };
    }

    private String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }

    private void validateFinalLayoutRequest(XrayFinalLayoutRequest request) {
        if (request == null || request.fragments() == null || request.fragments().isEmpty()) {
            throw new IllegalArgumentException("Final layout fragments are required.");
        }
    }

    private Map<Integer, XrayFinalLayoutRequest.FragmentTransform> indexFinalTransforms(
            List<XrayFinalLayoutRequest.FragmentTransform> fragments
    ) {
        Map<Integer, XrayFinalLayoutRequest.FragmentTransform> result = new LinkedHashMap<>();
        for (XrayFinalLayoutRequest.FragmentTransform fragment : fragments) {
            if (fragment == null) {
                throw new IllegalArgumentException("Final layout fragment must not be null.");
            }
            if (!Double.isFinite(fragment.centerX())
                    || !Double.isFinite(fragment.centerY())
                    || !Double.isFinite(fragment.rotationDeg())) {
                throw new IllegalArgumentException(
                        "Final layout transform contains a non-finite value: index=" + fragment.index()
                );
            }
            if (result.put(fragment.index(), fragment) != null) {
                throw new IllegalArgumentException("Duplicate final layout fragment index: " + fragment.index());
            }
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> getLayoutFragments(Map<String, Object> layout) {
        Object fragmentsValue = layout.get("fragments");
        if (!(fragmentsValue instanceof List<?> rawFragments)) {
            throw new IllegalStateException("layout.json does not contain a valid fragments array.");
        }
        List<Map<String, Object>> fragments = new ArrayList<>();
        for (Object value : rawFragments) {
            if (!(value instanceof Map<?, ?> rawFragment)) {
                throw new IllegalStateException("layout.json contains an invalid fragment entry.");
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
                    "Final layout fragment identity mismatch: index=" + transform.index()
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
            throw new IllegalStateException("Invalid original crop size for fragment index: " + transform.index());
        }

        double rotationDeg = normalizeAngle(transform.rotationDeg());
        double radians = Math.toRadians(rotationDeg);
        double cos = Math.cos(radians);
        double sin = Math.sin(radians);
        double localCenterX = (width - 1.0) / 2.0;
        double localCenterY = (height - 1.0) / 2.0;
        double tx = transform.centerX() - (cos * localCenterX - sin * localCenterY);
        double ty = transform.centerY() - (sin * localCenterX + cos * localCenterY);

        fragment.put("centerX", transform.centerX());
        fragment.put("centerY", transform.centerY());
        fragment.put("rotationDeg", rotationDeg);
        fragment.put("scale", 1.0);
        fragment.put("affineMatrix", List.of(
                List.of(cos, -sin, tx),
                List.of(sin, cos, ty),
                List.of(0.0, 0.0, 1.0)
        ));
    }

    private int requireInt(Map<String, Object> object, String key) {
        Object value = object.get(key);
        if (!(value instanceof Number number)) {
            throw new IllegalStateException("layout.json field is missing or invalid: " + key);
        }
        return number.intValue();
    }

    private String requireString(Map<String, Object> object, String key) {
        Object value = object.get(key);
        if (!(value instanceof String text) || text.isBlank()) {
            throw new IllegalStateException("layout.json field is missing or invalid: " + key);
        }
        return text;
    }

    private List<?> requireList(Map<String, Object> object, String key, int expectedSize) {
        Object value = object.get(key);
        if (!(value instanceof List<?> list) || list.size() != expectedSize) {
            throw new IllegalStateException("layout.json field is missing or invalid: " + key);
        }
        return list;
    }

    private double requireNumber(Object value, String fieldName) {
        if (!(value instanceof Number number)) {
            throw new IllegalStateException("layout.json field is missing or invalid: " + fieldName);
        }
        double result = number.doubleValue();
        if (!Double.isFinite(result)) {
            throw new IllegalStateException("layout.json field is not finite: " + fieldName);
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

    private boolean isSupportedXrayImage(String fileName) {
        String lower = fileName.toLowerCase(Locale.ROOT);
        return XRAY_IMAGE_EXTENSIONS.stream().anyMatch(lower::endsWith);
    }

    private int compareNaturalFileNames(String left, String right) {
        List<String> leftParts = naturalParts(left);
        List<String> rightParts = naturalParts(right);
        int common = Math.min(leftParts.size(), rightParts.size());
        for (int i = 0; i < common; i++) {
            String a = leftParts.get(i);
            String b = rightParts.get(i);
            boolean aNumber = Character.isDigit(a.charAt(0));
            boolean bNumber = Character.isDigit(b.charAt(0));
            int comparison;
            if (aNumber && bNumber) {
                comparison = new java.math.BigInteger(a).compareTo(new java.math.BigInteger(b));
            } else {
                comparison = a.compareToIgnoreCase(b);
            }
            if (comparison != 0) {
                return comparison;
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
}
