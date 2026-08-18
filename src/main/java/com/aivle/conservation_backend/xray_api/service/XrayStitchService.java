package com.aivle.conservation_backend.xray_api.service;

import com.aivle.conservation_backend.xray_api.client.XrayStitchClient;
import com.aivle.conservation_backend.xray_api.domain.S3FileRecord;
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
import com.aivle.conservation_backend.xray_api.repository.S3FileRecordRepository;
import com.aivle.conservation_backend.xray_api.repository.XrayJobRepository;
import com.aivle.conservation_backend.xray_api.repository.XrayDefectRepository;
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
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
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
    private final XrayDefectRepository defectRepository;
    private final S3FileRecordRepository s3FileRepository;
    private final ObjectMapper objectMapper;
    private final String configName;
    private final String callbackUrl;
    private final String callbackToken;
    private final long finalizationWaitSeconds;

    public XrayStitchService(
            XrayStitchClient xrayStitchClient,
            XrayS3Service s3Service,
            XrayJobRepository jobRepository,
            XrayDefectRepository defectRepository,
            S3FileRecordRepository s3FileRepository,
            ObjectMapper objectMapper,
            @Value("${xray.ai.config-name}") String configName,
            @Value("${xray.stitch.callback-url:http://localhost:8080/api/xray/stitch/callback}") String callbackUrl,
            @Value("${xray.stitch.callback-token:}") String callbackToken,
            @Value("${xray.stitch.finalization-wait-seconds:300}") long finalizationWaitSeconds
    ) {
        this.xrayStitchClient = xrayStitchClient;
        this.s3Service = s3Service;
        this.jobRepository = jobRepository;
        this.defectRepository = defectRepository;
        this.s3FileRepository = s3FileRepository;
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

        List<String> xrayNames = request.xrayFileNames().stream()
                .map(this::safeName)
                .toList();
        String colorName = safeName(request.colorFileName());

        XrayJob job = jobRepository.findByArtifactId(artifactId)
                .map(existing -> {
                    if (existing.getStatus() == XrayJobStatus.STITCHING
                            || existing.getStatus().isDetectionInProgress()
                            || existing.getStatus().isReportInProgress()) {
                        throw new ResponseStatusException(
                                HttpStatus.CONFLICT,
                                "This artifact has an X-ray job currently running: "
                                        + existing.getStatus()
                        );
                    }

                    resetForRerun(existing);
                    existing.prepareAgain(1, xrayNames.size());
                    return existing;
                })
                .orElseGet(() -> XrayJob.create(
                        UUID.randomUUID(), artifactId, null, 1, xrayNames.size()
                ));
        jobRepository.save(job);

        String artifact = artifactId.toString();
        String colorKey = XrayS3Keys.colorInput(artifact, colorName);
        Map<String, String> colorMetadata = Map.of(
                "usage", "color_reference",
                "original_name", metadataFileName(colorName)
        );
        XrayS3Service.PresignedPut colorPut =
                s3Service.presignInputPut(colorKey, null, colorMetadata);
        UploadTarget color = new UploadTarget(
                request.colorFileName(),
                colorKey,
                colorPut.url(),
                colorPut.requiredHeaders()
        );

        List<UploadTarget> xrays = new ArrayList<>();
        for (int i = 0; i < xrayNames.size(); i++) {
            String name = xrayNames.get(i);
            String key = XrayS3Keys.xrayInput(artifact, name);
            Map<String, String> metadata = Map.of(
                    "usage", "xray_original",
                    "source_order", String.valueOf(i),
                    "original_name", metadataFileName(name)
            );
            XrayS3Service.PresignedPut put =
                    s3Service.presignInputPut(key, null, metadata);
            xrays.add(new UploadTarget(
                    request.xrayFileNames().get(i),
                    key,
                    put.url(),
                    put.requiredHeaders()
            ));
        }

        return new PrepareResponse(
                job.getId().toString(), artifact, job.getStatus().name(), color, xrays
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
                    blankToNull(colorFiles.get(0).getContentType()),
                    Map.of(
                            "usage", "color_reference",
                            "original_name", metadataFileName(colorName)
                    )
            );
            for (int i = 0; i < xrayFiles.size(); i++) {
                MultipartFile file = xrayFiles.get(i);
                String name = xrayNames.get(i);
                s3Service.putBytes(
                        prepared.xrays().get(i).s3Key(),
                        file.getBytes(),
                        blankToNull(file.getContentType()),
                        Map.of(
                                "usage", "xray_original",
                                "source_order", String.valueOf(i),
                                "original_name", metadataFileName(name)
                        )
                );
            }
        } catch (IOException e) {
            markFailed(UUID.fromString(prepared.jobId()), "S3 upload failed: " + e.getMessage());
            throw new IllegalStateException("Failed to upload X-ray inputs to S3.", e);
        }

        start(prepared.jobId(), colorName, xrayNames);
        return new XrayJobResponse(
                prepared.jobId(), prepared.artifactId(), "STITCHING", "X-ray stitching is running."
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
        if (job.getStatus() == XrayJobStatus.STITCHING) {
            return toStatusResponse(job);
        }
        if (job.getStatus() != XrayJobStatus.PREPARED
                && job.getStatus() != XrayJobStatus.UPLOADING
                && job.getStatus() != XrayJobStatus.FAILED) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "X-ray stitching cannot start from status: " + job.getStatus()
            );
        }

        String colorName = safeName(colorFileName);
        List<String> xrayNames = xrayFileNames == null
                ? List.of()
                : xrayFileNames.stream().map(this::safeName).toList();
        if (xrayNames.size() < 2) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "At least two X-ray file names are required.");
        }
        if (xrayNames.size() != job.getExpectedXrayCount()) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "Uploaded X-ray count does not match the prepared count. expected="
                            + job.getExpectedXrayCount() + ", actual=" + xrayNames.size()
            );
        }

        String artifactId = job.getArtifactId().toString();
        requireObject(XrayS3Keys.colorInput(artifactId, colorName), "color reference");
        for (String name : xrayNames) {
            requireObject(XrayS3Keys.xrayInput(artifactId, name), "X-ray fragment " + name);
        }
        // 결합 시작 여부는 실제 S3 객체 존재 검사를 정본으로 판단한다.
        // S3_FILE은 ObjectCreated -> Lambda -> RDS로 비동기 기록되므로
        // 대량 업로드 직후 레코드 반영이 늦더라도 /start를 차단하지 않는다.

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
                        s3Service.presignOutputPut(XrayS3Keys.layoutFragmentMasks(artifactId), "application/zip")
                ),
                callbackUrl,
                callbackToken
        );

        job.markStitching();
        jobRepository.save(job);

        try {
            xrayStitchClient.startStitch(request);
        } catch (RuntimeException e) {
            job.markFailed("FastAPI did not accept the X-ray job: " + e.getMessage());
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
            // FastAPI의 COMPLETED는 X-ray 전체 완료가 아니라 자동 결합 완료를 뜻한다.
            case "COMPLETED" -> {
                requireBaseOutputs(job.getArtifactId().toString());
                job.markStitched();
            }
            // Finalizer 완료 후에도 다음 단계는 결함 분석이므로 전체 상태는 STITCHED로 유지한다.
            case "FINALIZED" -> {
                requireFinalOutputs(job.getArtifactId().toString());
                job.markStitched();
            }
            case "FAILED" -> job.markFailed(
                    request.errorMessage() == null || request.errorMessage().isBlank()
                            ? request.message()
                            : request.errorMessage()
            );
            case "RUNNING" -> job.markStitching();
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

    @Transactional(readOnly = true)
    public XrayJobStatusResponse getLocalJobStatusByArtifactId(String artifactIdValue) {
        UUID artifactId = parseUuid(artifactIdValue, "artifactId");
        XrayJob job = jobRepository.findByArtifactId(artifactId).orElseThrow(() ->
                new ResponseStatusException(
                        HttpStatus.NOT_FOUND,
                        "X-ray job not found for artifact: " + artifactId
                )
        );
        return toStatusResponse(job);
    }

    @Transactional
    public ReconcileResponse reconcile(String jobIdValue) {
        XrayJob job = requireJob(parseUuid(jobIdValue, "jobId"));
        String previous = job.getStatus().name();
        String artifactId = job.getArtifactId().toString();

        if (hasFinalOutputs(artifactId) || hasBaseOutputs(artifactId)) {
            if (job.getStatus() != XrayJobStatus.REVIEW_READY
                    && job.getStatus() != XrayJobStatus.REPORT_READY
                    && job.getStatus() != XrayJobStatus.REPORTING
                    && job.getStatus() != XrayJobStatus.COMPLETED) {
                job.markStitched();
            }
        }
        jobRepository.save(job);
        String current = job.getStatus().name();
        return new ReconcileResponse(
                job.getId().toString(),
                previous,
                current,
                !previous.equals(current),
                statusMessage(job)
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
        XrayJob job = requireStitchedJobForFinalLayout(jobIdValue);
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

        List<String> sourceNames = getOrderedXraySourceFileNames(jobIdValue);
        XrayAiFinalizationRequest aiRequest = new XrayAiFinalizationRequest(
                job.getId().toString(),
                artifactId,
                sourceNames.stream()
                        .map(name -> new XrayAiFinalizationRequest.RemoteInput(
                                name,
                                s3Service.presignGet(XrayS3Keys.xrayInput(artifactId, name))
                        ))
                        .toList(),
                s3Service.presignGet(XrayS3Keys.layoutFragmentMasks(artifactId)),
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

        try {
            xrayStitchClient.startFinalization(aiRequest);
        } catch (RuntimeException e) {
            job.markFailed("FastAPI did not accept final rendering: " + e.getMessage());
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
        List<S3FileRecord> records = xrayInputRecords(job);
        if (!records.isEmpty()) {
            return records.stream()
                    .map(record -> record.getOriginalName() == null || record.getOriginalName().isBlank()
                            ? fileNameFromKey(record.getS3Key())
                            : safeName(record.getOriginalName()))
                    .toList();
        }

        String prefix = XrayS3Keys.xrayPrefix(job.getArtifactId().toString());
        List<String> names = s3Service.listKeys(prefix).stream()
                .map(key -> key.substring(prefix.length()))
                .filter(this::isSupportedXrayImage)
                .toList();
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

    public Resource getColorReferenceResource(String jobId) {
        XrayJob job = requireCompletedJob(jobId);
        List<S3FileRecord> records = colorInputRecords(job);
        if (records.size() == 1) {
            S3FileRecord record = records.get(0);
            String name = record.getOriginalName() == null || record.getOriginalName().isBlank()
                    ? fileNameFromKey(record.getS3Key())
                    : record.getOriginalName();
            return namedResource(s3Service.getBytes(record.getS3Key()), name);
        }

        String prefix = XrayS3Keys.colorPrefix(job.getArtifactId().toString());
        List<String> keys = s3Service.listKeys(prefix).stream()
                .filter(key -> !key.endsWith("/"))
                .toList();
        if (keys.size() != 1) {
            throw new IllegalStateException(
                    "Exactly one color reference is required. actual=" + keys.size()
            );
        }
        String key = keys.get(0);
        String name = key.substring(prefix.length());
        return namedResource(s3Service.getBytes(key), name);
    }

    @Transactional(readOnly = true)
    public XrayJob getJobEntity(String jobId) {
        return requireJob(parseUuid(jobId, "jobId"));
    }

    @Transactional
    public void markDetecting(String jobId) {
        XrayJob job = requireJob(parseUuid(jobId, "jobId"));
        requireFinalOutputs(job.getArtifactId().toString());
        job.markDetecting();
        jobRepository.save(job);
    }

    @Transactional
    public void markReviewReady(String jobId) {
        XrayJob job = requireJob(parseUuid(jobId, "jobId"));
        job.markReviewReady();
        jobRepository.save(job);
    }

    @Transactional
    public void markWorkflowFailed(String jobId, String errorMessage) {
        XrayJob job = requireJob(parseUuid(jobId, "jobId"));
        job.markFailed(errorMessage);
        jobRepository.save(job);
    }

    // ---------------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------------

    /**
     * 같은 artifact의 X-ray 작업을 다시 시작하기 전에 이전 분석 산출물을 정리한다.
     *
     * <p>artifact 기준으로 S3 key가 고정되어 있으므로 이전 output을 남겨두면
     * hasBaseOutputs/hasFinalOutputs가 과거 결과를 새 작업 결과로 오인할 수 있다.</p>
     */
    private void resetForRerun(XrayJob job) {
        String artifactId = job.getArtifactId().toString();

        // 이전 결함 검수 결과 제거
        defectRepository.deleteAllByXrayJob_Id(job.getId());

        // Lambda가 기록한 기존 input 감사 레코드 중 현재 workflow가 직접 참조하는 항목 제거
        List<S3FileRecord> previousInputRecords = new ArrayList<>();
        previousInputRecords.addAll(xrayInputRecords(job));
        previousInputRecords.addAll(colorInputRecords(job));
        if (!previousInputRecords.isEmpty()) {
            s3FileRepository.deleteAll(previousInputRecords);
            s3FileRepository.flush();
        }

        // inputs + outputs 전체를 비워 새 presigned upload부터 완전히 새 작업으로 시작
        s3Service.deletePrefix(XrayS3Keys.root(artifactId) + "/");
    }

    private XrayJob requireJob(UUID jobId) {
        return jobRepository.findById(jobId).orElseThrow(() ->
                new ResponseStatusException(HttpStatus.NOT_FOUND, "X-ray job not found: " + jobId)
        );
    }

    private XrayJob requireCompletedJob(String jobId) {
        XrayJob job = requireJob(parseUuid(jobId, "jobId"));
        if (job.getStatus() != XrayJobStatus.STITCHED
                && !job.getStatus().isDetectionInProgress()
                && job.getStatus() != XrayJobStatus.REVIEW_READY
                && job.getStatus() != XrayJobStatus.REPORT_READY
                && job.getStatus() != XrayJobStatus.REPORTING
                && job.getStatus() != XrayJobStatus.COMPLETED) {
            throw new IllegalStateException("X-ray stitching result is not ready: " + job.getStatus());
        }
        requireBaseOutputs(job.getArtifactId().toString());
        return job;
    }

    private XrayJob requireStitchedJobForFinalLayout(String jobId) {
        XrayJob job = requireJob(parseUuid(jobId, "jobId"));
        if (job.getStatus() != XrayJobStatus.STITCHED
                && job.getStatus() != XrayJobStatus.FAILED) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "Final layout cannot be saved from status: " + job.getStatus()
            );
        }
        requireBaseOutputs(job.getArtifactId().toString());
        return job;
    }

    private XrayJob requireFinalizedJobEntity(String jobId) {
        XrayJob job = requireCompletedJob(jobId);
        requireFinalOutputs(job.getArtifactId().toString());
        return job;
    }

    private XrayJobStatusResponse toStatusResponse(XrayJob job) {
        String artifactId = job.getArtifactId().toString();
        String resultUrl = null;
        if (hasFinalOutputs(artifactId)) {
            resultUrl = "/api/xray/stitch/jobs/" + job.getId() + "/result/final";
        } else if (hasBaseOutputs(artifactId)) {
            resultUrl = "/api/xray/stitch/jobs/" + job.getId() + "/result";
        }
        return new XrayJobStatusResponse(
                job.getId().toString(),
                artifactId,
                job.getStatus().name(),
                statusMessage(job),
                resultUrl,
                job.getErrorMessage()
        );
    }

    private String statusMessage(XrayJob job) {
        if (job.getStatus() == XrayJobStatus.FAILED) {
            return "X-ray processing failed.";
        }
        return switch (job.getStatus()) {
            case PREPARED -> "Waiting for S3 input uploads.";
            case UPLOADING -> "X-ray inputs are uploading.";
            case STITCHING -> "X-ray automatic stitching is running.";
            case STITCHED -> hasFinalOutputs(job.getArtifactId().toString())
                    ? "Final X-ray layout is ready for defect analysis."
                    : "Automatic X-ray stitching is complete.";
            case DETECTING -> "X-ray defect detection and mapping are running.";
            case DETECTING_FRAGMENTS -> "Original X-ray fragments are being analyzed.";
            case DETECTING_ASSEMBLED -> "The final assembled X-ray is being analyzed.";
            case MAPPING -> "Detected defects are being mapped and merged.";
            case REVIEW_READY -> "X-ray defects are ready for expert review.";
            case REPORT_READY -> "X-ray review is complete and report drafting is ready.";
            case REPORTING -> "AI X-ray report text is being generated.";
            case COMPLETED -> "X-ray inspection is complete.";
            case FAILED -> "X-ray processing failed.";
        };
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

    private List<S3FileRecord> xrayInputRecords(XrayJob job) {
        return s3FileRepository
                .findAllByArtifactIdAndModuleTypeAndUsageNameOrderBySourceOrderAsc(
                        job.getArtifactId(), "XRAY", "xray_original"
                );
    }

    private List<S3FileRecord> colorInputRecords(XrayJob job) {
        return s3FileRepository
                .findAllByArtifactIdAndModuleTypeAndUsageNameOrderBySourceOrderAsc(
                        job.getArtifactId(), "XRAY", "color_reference"
                );
    }

    private String fileNameFromKey(String key) {
        if (key == null || key.isBlank()) {
            throw new IllegalStateException("S3_FILE s3_key is missing.");
        }
        int slash = key.lastIndexOf('/');
        return safeName(slash >= 0 ? key.substring(slash + 1) : key);
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
                && s3Service.objectExists(XrayS3Keys.layoutFragmentMasks(artifactId));
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
                if (current.getStatus() != XrayJobStatus.REVIEW_READY
                        && current.getStatus() != XrayJobStatus.REPORT_READY
                        && current.getStatus() != XrayJobStatus.REPORTING
                        && current.getStatus() != XrayJobStatus.COMPLETED) {
                    current.markStitched();
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

    private void markFailed(UUID jobId, String error) {
        jobRepository.findById(jobId).ifPresent(job -> {
            job.markFailed(error);
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

    private String metadataFileName(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
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