package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.pottery_inspection_ai.client.PotteryInspectionAiClient;
import com.aivle.conservation_backend.pottery_inspection_ai.dto.PotteryInspectionResponseDto;
import com.aivle.conservation_backend.vca.dto.ArtifactCollectionResponse;
import com.aivle.conservation_backend.vca.dto.ArtifactCollectionResponse.ArtifactSummary;
import com.aivle.conservation_backend.vca.dto.ArtifactDetailResponse;
import com.aivle.conservation_backend.vca.dto.CompleteImageRequest;
import com.aivle.conservation_backend.vca.dto.CreateRunRequest;
import com.aivle.conservation_backend.vca.dto.ImageResponse;
import com.aivle.conservation_backend.vca.dto.IntermediateResultsResponse;
import com.aivle.conservation_backend.vca.dto.PdfJobResponse;
import com.aivle.conservation_backend.vca.dto.PotteryInspectionRequest;
import com.aivle.conservation_backend.vca.dto.PresignImageRequest;
import com.aivle.conservation_backend.vca.dto.PresignImageResponse;
import com.aivle.conservation_backend.vca.dto.ReportResponse;
import com.aivle.conservation_backend.vca.dto.RunResponse;
import com.aivle.conservation_backend.vca.dto.VcaCorpusPdfCollectionResponse;
import com.aivle.conservation_backend.vca.dto.VcaCorpusPdfResponse;
import com.aivle.conservation_backend.vca.exception.VcaApiException;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentFinding;
import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentReport;
import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentRun;
import com.aivle.conservation_backend.vca.gateway.VcaAiGateway;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.regex.Pattern;

@Service
public class VcaService {

    private static final Logger log = LoggerFactory.getLogger(VcaService.class);
    private static final Pattern ARTIFACT_ID =
            Pattern.compile("^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$");
    private static final Pattern SHA256 = Pattern.compile("^[A-Fa-f0-9]{64}$");
    private static final String LOCAL_ORIGIN = "https://vca-local.invalid";
    private static final String REPORT_HEADLINE = "VCA 육안 조사 결과";
    private static final String FINDING_TITLE = "VCA 이상 후보";
    private static final String RECOMMENDATION_TITLE = "전문가 검토 후 보존처리 계획에 반영";
    private static final String RECOMMENDATION_DESCRIPTION =
            "AI가 제안한 이상 후보와 중간 처리 결과를 전문가가 검토한 뒤 보존처리 계획에 반영하세요.";
    private static final int POTTERY_INSPECTION_CALLS = 1;
    private static final String POTTERY_STATUS_NOT_STARTED = "NOT_STARTED";
    private static final String POTTERY_STATUS_COMPLETED = "COMPLETED";
    private static final String POTTERY_STATUS_FAILED = "FAILED";

    private final Map<String, ArtifactState> artifacts = new LinkedHashMap<>();
    private final Map<String, ImageState> uploadedImagesBySha256 = new LinkedHashMap<>();
    private final Map<String, PdfJobState> pdfJobs = new LinkedHashMap<>();
    private final Map<String, String> pdfJobIdsByRun = new LinkedHashMap<>();
    private final boolean localDirectCompleteEnabled;
    private final Optional<VcaAiGateway> vcaAiGateway;
    private final Optional<VcaSharedStorage> sharedStorage;
    private final Optional<VcaImageStorage> imageStorage;
    private final Optional<VcaIntermediateResultStorage> intermediateResultStorage;
    private final Optional<PotteryInspectionAiClient> potteryInspectionAiClient;
    private final Optional<VcaCorpusStorage> corpusStorage;

    @Autowired
    public VcaService(
            @Value("${vca.local-direct-complete-enabled:false}") boolean localDirectCompleteEnabled,
            VcaAiGateway vcaAiGateway,
            VcaSharedStorage sharedStorage,
            VcaImageStorage imageStorage,
            VcaIntermediateResultStorage intermediateResultStorage,
            PotteryInspectionAiClient potteryInspectionAiClient,
            VcaCorpusStorage corpusStorage
    ) {
        this(
                localDirectCompleteEnabled,
                Optional.of(vcaAiGateway),
                Optional.of(sharedStorage),
                Optional.of(imageStorage),
                Optional.of(intermediateResultStorage),
                Optional.of(potteryInspectionAiClient),
                Optional.of(corpusStorage)
        );
    }

    VcaService(boolean localDirectCompleteEnabled) {
        this(
                localDirectCompleteEnabled,
                Optional.empty(),
                Optional.empty(),
                Optional.empty(),
                Optional.empty(),
                Optional.empty(),
                Optional.empty()
        );
    }

    VcaService(boolean localDirectCompleteEnabled, VcaAiGateway vcaAiGateway) {
        this(
                localDirectCompleteEnabled,
                Optional.of(vcaAiGateway),
                Optional.empty(),
                Optional.empty(),
                Optional.empty(),
                Optional.empty(),
                Optional.empty()
        );
    }

    VcaService(
            boolean localDirectCompleteEnabled,
            VcaAiGateway vcaAiGateway,
            VcaSharedStorage sharedStorage
    ) {
        this(
                localDirectCompleteEnabled,
                Optional.of(vcaAiGateway),
                Optional.of(sharedStorage),
                Optional.empty(),
                Optional.empty(),
                Optional.empty(),
                Optional.empty()
        );
    }

    VcaService(
            boolean localDirectCompleteEnabled,
            VcaAiGateway vcaAiGateway,
            VcaSharedStorage sharedStorage,
            VcaImageStorage imageStorage
    ) {
        this(
                localDirectCompleteEnabled,
                Optional.of(vcaAiGateway),
                Optional.of(sharedStorage),
                Optional.of(imageStorage),
                Optional.empty(),
                Optional.empty(),
                Optional.empty()
        );
    }

    VcaService(
            boolean localDirectCompleteEnabled,
            VcaAiGateway vcaAiGateway,
            VcaSharedStorage sharedStorage,
            VcaIntermediateResultStorage intermediateResultStorage
    ) {
        this(
                localDirectCompleteEnabled,
                Optional.of(vcaAiGateway),
                Optional.of(sharedStorage),
                Optional.empty(),
                Optional.of(intermediateResultStorage),
                Optional.empty(),
                Optional.empty()
        );
    }

    VcaService(
            boolean localDirectCompleteEnabled,
            VcaAiGateway vcaAiGateway,
            VcaSharedStorage sharedStorage,
            PotteryInspectionAiClient potteryInspectionAiClient
    ) {
        this(
                localDirectCompleteEnabled,
                Optional.of(vcaAiGateway),
                Optional.of(sharedStorage),
                Optional.empty(),
                Optional.empty(),
                Optional.of(potteryInspectionAiClient),
                Optional.empty()
        );
    }

    VcaService(boolean localDirectCompleteEnabled, VcaCorpusStorage corpusStorage) {
        this(
                localDirectCompleteEnabled,
                Optional.empty(),
                Optional.empty(),
                Optional.empty(),
                Optional.empty(),
                Optional.empty(),
                Optional.of(corpusStorage)
        );
    }

    private VcaService(
            boolean localDirectCompleteEnabled,
            Optional<VcaAiGateway> vcaAiGateway,
            Optional<VcaSharedStorage> sharedStorage,
            Optional<VcaImageStorage> imageStorage,
            Optional<VcaIntermediateResultStorage> intermediateResultStorage,
            Optional<PotteryInspectionAiClient> potteryInspectionAiClient,
            Optional<VcaCorpusStorage> corpusStorage
    ) {
        this.localDirectCompleteEnabled = localDirectCompleteEnabled;
        this.vcaAiGateway = vcaAiGateway;
        this.sharedStorage = sharedStorage;
        this.imageStorage = imageStorage;
        this.intermediateResultStorage = intermediateResultStorage;
        this.potteryInspectionAiClient = potteryInspectionAiClient;
        this.corpusStorage = corpusStorage;
        seedDemoArtifact();
    }

    public synchronized ArtifactCollectionResponse getArtifacts() {
        return new ArtifactCollectionResponse(
                artifacts.values().stream().map(artifact -> {
                    advanceDemoRuns(artifact);
                    return toSummary(artifact);
                }).toList()
        );
    }

    public VcaCorpusPdfCollectionResponse getCorpusPdfs() {
        return requireCorpusStorage().listPdfs();
    }

    public VcaCorpusPdfResponse uploadCorpusPdf(MultipartFile file) {
        return requireCorpusStorage().storePdf(file);
    }

    public void deleteCorpusPdf(String fileName) {
        requireCorpusStorage().deletePdf(fileName);
    }

    public synchronized ArtifactDetailResponse getArtifact(String artifactId) {
        ArtifactState artifact = getOrCreateArtifact(artifactId);
        advanceDemoRuns(artifact);
        return toDetail(artifact);
    }

    public synchronized PresignImageResponse presignImage(
            String artifactId,
            PresignImageRequest request
    ) {
        ArtifactState artifact = getOrCreateArtifact(artifactId);
        Instant now = Instant.now();
        String imageId = UUID.randomUUID().toString();
        String sha256 = request.sha256().toLowerCase(Locale.ROOT);
        String uploadMode = uploadMode();
        String uploadUrl;
        Map<String, String> requiredHeaders;
        String objectKey = null;
        if ("SIGNED_PUT".equals(uploadMode) && imageStorage.isPresent()) {
            VcaImageStorage.PresignedUpload upload = imageStorage.get()
                    .presignUpload(artifactId, imageId, request, now.plus(15, ChronoUnit.MINUTES));
            objectKey = upload.objectKey();
            uploadUrl = upload.uploadUrl().toString();
            requiredHeaders = upload.requiredHeaders();
        } else {
            Instant expiresAt = now.plus(15, ChronoUnit.MINUTES);
            String signature = UUID.randomUUID().toString().replace("-", "");
            uploadUrl = LOCAL_ORIGIN + "/uploads/" + imageId
                    + "?expires=" + expiresAt.getEpochSecond()
                    + "&signature=" + signature;
            requiredHeaders = Map.of(
                    "Content-Type", request.contentType(),
                    "X-VCA-Content-SHA256", sha256
            );
        }
        ImageState image = new ImageState(
                imageId,
                request.fileName(),
                request.contentType(),
                request.sizeBytes(),
                sha256,
                uploadMode,
                "PENDING",
                now,
                null,
                objectKey,
                null
        );
        artifact.images.put(imageId, image);
        artifact.updatedAt = now;

        Instant expiresAt = now.plus(15, ChronoUnit.MINUTES);
        log.info("VCA image upload reserved artifactId={} imageId={}", artifactId, imageId);
        return new PresignImageResponse(
                imageId,
                "PENDING",
                image.uploadMode,
                uploadUrl,
                "PUT",
                requiredHeaders,
                expiresAt
        );
    }

    public synchronized ImageResponse uploadImage(String artifactId, MultipartFile file) {
        ArtifactState artifact = getOrCreateArtifact(artifactId);
        String imageId = UUID.randomUUID().toString();
        Instant now = Instant.now();
        ImageState image;
        if (imageStorage.isPresent()) {
            VcaImageStorage.StoredImage storedImage = imageStorage.get()
                    .storeUpload(artifactId, imageId, file);
            image = new ImageState(
                    imageId,
                    storedImage.fileName(),
                    storedImage.contentType(),
                    storedImage.sizeBytes(),
                    storedImage.sha256(),
                    "DIRECT_UPLOAD",
                    "UPLOADED",
                    now,
                    now,
                    storedImage.objectKey(),
                    null
            );
        } else {
            VcaSharedStorage.StoredImage storedImage = requireSharedStorage().storeUpload(imageId, file);
            image = new ImageState(
                    imageId,
                    storedImage.fileName(),
                    storedImage.contentType(),
                    storedImage.sizeBytes(),
                    storedImage.sha256(),
                    "DIRECT_UPLOAD",
                    "UPLOADED",
                    now,
                    now,
                    null,
                    storedImage.localPath()
            );
        }
        artifact.images.put(imageId, image);
        artifact.updatedAt = now;
        uploadedImagesBySha256.put(image.sha256, image);
        log.info("VCA image uploaded through Spring artifactId={} imageId={}", artifactId, imageId);
        return toImage(artifact, image);
    }

    public synchronized ImageResponse completeImage(
            String artifactId,
            String imageId,
            CompleteImageRequest request
    ) {
        ArtifactState artifact = requireArtifact(artifactId);
        validateUuid(imageId, "imageId");
        ImageState image = requireImage(artifact, imageId);
        String suppliedSha256 = request.sha256().toLowerCase(Locale.ROOT);
        if (!image.sha256.equals(suppliedSha256)) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "SHA256_MISMATCH",
                    "The completed upload checksum does not match the reserved image."
            );
        }
        if ("SIGNED_PUT".equals(image.uploadMode) && image.objectKey != null && imageStorage.isPresent()) {
            imageStorage.get().verifyUpload(image.objectKey, image.sizeBytes, suppliedSha256);
        } else if (!"DIRECT_COMPLETE".equals(image.uploadMode)) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "UPLOAD_NOT_VERIFIED",
                    "The upload object must be verified before completion."
            );
        }
        if (!"UPLOADED".equals(image.status)) {
            Instant now = Instant.now();
            image.status = "UPLOADED";
            image.uploadedAt = now;
            artifact.updatedAt = now;
            uploadedImagesBySha256.put(image.sha256, image);
            log.info("VCA image upload completed artifactId={} imageId={}", artifactId, imageId);
        }
        return toImage(artifact, image);
    }

    public synchronized void deleteImage(String artifactId, String imageId) {
        ArtifactState artifact = requireArtifact(artifactId);
        validateUuid(imageId, "imageId");
        ImageState removed = artifact.images.remove(imageId);
        if (removed == null) {
            throw new VcaApiException(
                    HttpStatus.NOT_FOUND,
                    "IMAGE_NOT_FOUND",
                    "The requested VCA image was not found."
            );
        }
        uploadedImagesBySha256.remove(removed.sha256, removed);
        if (removed.objectKey != null && imageStorage.isPresent()) {
            imageStorage.get().delete(removed.objectKey);
        } else if (removed.localPath != null) {
            requireSharedStorage().deleteUpload(removed.imageId, removed.localPath);
        }
        artifact.updatedAt = Instant.now();
        log.info("VCA image metadata deleted artifactId={} imageId={}", artifactId, imageId);
    }

    public RunResponse createRun(String artifactId) {
        return createRun(artifactId, null);
    }

    public RunResponse createRun(String artifactId, CreateRunRequest request) {
        RunCreation reservation = reserveRun(artifactId);
        VcaAiAssessmentRun aiRun;
        try {
            aiRun = createAiAssessmentRun(
                    artifactId,
                    reservation.assessmentRunId,
                    reservation.uploadedImages
            );
        } catch (RuntimeException exception) {
            cancelRunReservation(artifactId, reservation.assessmentRunId);
            throw exception;
        }
        return completeRunReservation(artifactId, reservation, aiRun, request == null ? null : request.material());
    }

    private synchronized RunCreation reserveRun(String artifactId) {
        ArtifactState artifact = getOrCreateArtifact(artifactId);
        boolean activeRunExists = artifact.runs.values().stream()
                .anyMatch(run -> "QUEUED".equals(run.status) || "RUNNING".equals(run.status));
        if (activeRunExists) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "ACTIVE_RUN_EXISTS",
                    "An assessment run is already queued or running for this artifact."
            );
        }
        List<ImageState> uploadedImages = artifact.images.values().stream()
                .filter(image -> "UPLOADED".equals(image.status))
                .toList();
        if (uploadedImages.isEmpty()) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "NOT_READY",
                    "At least one uploaded image is required to create an assessment run."
            );
        }

        Instant now = Instant.now();
        String assessmentRunId = UUID.randomUUID().toString();
        RunState run = new RunState(
                assessmentRunId,
                "QUEUED",
                uploadedImages.size(),
                now,
                null,
                null,
                null,
                uploadedImages
        );
        artifact.runs.put(assessmentRunId, run);
        artifact.updatedAt = now;
        log.info("VCA assessment run reserved artifactId={} assessmentRunId={} imageCount={}",
                artifactId, assessmentRunId, uploadedImages.size());
        return new RunCreation(assessmentRunId, uploadedImages);
    }

    private synchronized RunResponse completeRunReservation(
            String artifactId,
            RunCreation reservation,
            VcaAiAssessmentRun aiRun,
            String material
    ) {
        ArtifactState artifact = requireArtifact(artifactId);
        RunState run = requireRun(artifact, reservation.assessmentRunId);
        ReportResponse report = null;
        if (vcaAiGateway.isEmpty()) {
            List<ReportResponse.Image> reportImages = reservation.uploadedImages.stream()
                    .map(image -> new ReportResponse.Image(
                            image.imageId,
                            image.fileName,
                            fileGatewayUrl(artifactId, image.sha256)
                    ))
                    .toList();
            report = VcaDemoReportFactory.create(
                    artifactId,
                    reservation.assessmentRunId,
                    run.createdAt,
                    reportImages
            );
        }
        Instant now = Instant.now();
        run.status = aiRun.status();
        run.aiRunId = aiRun.runId();
        run.completedAt = "COMPLETED".equals(aiRun.status()) ? now : null;
        run.report = report;
        run.material = material;
        run.potteryInspectionStatus = initialPotteryInspectionStatus(material);
        artifact.updatedAt = now;
        log.info("VCA assessment queued artifactId={} assessmentRunId={} imageCount={}",
                artifactId, reservation.assessmentRunId, reservation.uploadedImages.size());
        return toRun(artifact, run);
    }

    public ReportResponse runPotteryInspection(
            String artifactId,
            String assessmentRunId,
            PotteryInspectionRequest request
    ) {
        PotteryInspectionTarget target = preparePotteryInspection(artifactId, assessmentRunId, request);
        if (!target.applicable()) {
            return markPotteryInspectionNotApplicable(artifactId, assessmentRunId);
        }
        try {
            ReportResponse.PotteryInspection potteryInspection = inspectPottery(target.primaryImage());
            return completePotteryInspection(artifactId, assessmentRunId, potteryInspection);
        } catch (RuntimeException exception) {
            return failPotteryInspection(artifactId, assessmentRunId, exception);
        }
    }

    private synchronized PotteryInspectionTarget preparePotteryInspection(
            String artifactId,
            String assessmentRunId,
            PotteryInspectionRequest request
    ) {
        ArtifactState artifact = requireArtifact(artifactId);
        RunState run = requireRun(artifact, assessmentRunId);
        syncRunWithAi(artifact, run);
        if (!"COMPLETED".equals(run.status)) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "NOT_READY",
                    "The assessment report must be completed before pottery inspection."
            );
        }
        ensureReportReady(artifact, run);
        String material = potteryMaterial(request, run);
        boolean applicable = isPotteryMaterial(material) && potteryInspectionAiClient.isPresent();
        ImageState primaryImage = run.uploadedImages.get(0);
        return new PotteryInspectionTarget(applicable, primaryImage);
    }

    private synchronized ReportResponse markPotteryInspectionNotApplicable(
            String artifactId,
            String assessmentRunId
    ) {
        ArtifactState artifact = requireArtifact(artifactId);
        RunState run = requireRun(artifact, assessmentRunId);
        run.potteryInspectionStatus = new ReportResponse.PotteryInspectionStatus(
                false,
                POTTERY_STATUS_NOT_STARTED,
                false,
                null,
                null
        );
        run.report = withPotteryInspectionState(run.report, run);
        artifact.updatedAt = Instant.now();
        return run.report;
    }

    private synchronized ReportResponse completePotteryInspection(
            String artifactId,
            String assessmentRunId,
            ReportResponse.PotteryInspection potteryInspection
    ) {
        ArtifactState artifact = requireArtifact(artifactId);
        RunState run = requireRun(artifact, assessmentRunId);
        run.potteryInspection = potteryInspection;
        run.potteryInspectionStatus = new ReportResponse.PotteryInspectionStatus(
                true,
                POTTERY_STATUS_COMPLETED,
                true,
                null,
                Instant.now()
        );
        run.report = withPotteryInspectionState(run.report, run);
        artifact.updatedAt = Instant.now();
        return run.report;
    }

    private synchronized ReportResponse failPotteryInspection(
            String artifactId,
            String assessmentRunId,
            RuntimeException exception
    ) {
        ArtifactState artifact = requireArtifact(artifactId);
        RunState run = requireRun(artifact, assessmentRunId);
        run.potteryInspectionStatus = new ReportResponse.PotteryInspectionStatus(
                true,
                POTTERY_STATUS_FAILED,
                true,
                exception.getMessage(),
                Instant.now()
        );
        run.report = withPotteryInspectionState(run.report, run);
        artifact.updatedAt = Instant.now();
        log.warn("VCA pottery inspection failed artifactId={} assessmentRunId={}", artifactId, assessmentRunId, exception);
        return run.report;
    }

    private synchronized void cancelRunReservation(String artifactId, String assessmentRunId) {
        ArtifactState artifact = requireArtifact(artifactId);
        RunState run = artifact.runs.get(assessmentRunId);
        if (run != null && run.aiRunId == null) {
            artifact.runs.remove(assessmentRunId);
            artifact.updatedAt = Instant.now();
        }
    }

    public synchronized ReportResponse getReport(
            String artifactId,
            String assessmentRunId
    ) {
        ArtifactState artifact = requireArtifact(artifactId);
        RunState run = requireRun(artifact, assessmentRunId);
        syncRunWithAi(artifact, run);
        if (vcaAiGateway.isPresent()) {
            if (!"COMPLETED".equals(run.status)) {
                throw new VcaApiException(
                        HttpStatus.CONFLICT,
                        "NOT_READY",
                        "The assessment report is not ready."
                );
            }
            VcaAiAssessmentReport aiReport = vcaAiGateway.get().getAssessmentReport(run.aiRunId);
            run.report = toReport(artifact, run, aiReport);
            return run.report;
        }
        if (!"COMPLETED".equals(run.status)) {
            Instant now = Instant.now();
            run.status = "COMPLETED";
            run.completedAt = now;
            artifact.updatedAt = now;
            log.info("VCA demo assessment completed artifactId={} assessmentRunId={}",
                    artifactId, assessmentRunId);
        }
        return run.report;
    }

    public synchronized IntermediateResultsResponse getIntermediateResults(
            String artifactId,
            String assessmentRunId
    ) {
        ArtifactState artifact = requireArtifact(artifactId);
        RunState run = requireRun(artifact, assessmentRunId);
        String runProjectName = projectName(artifactId, run.assessmentRunId);
        return intermediateResultStorage
                .map(storage -> storage.read(artifactId, run.assessmentRunId, runProjectName))
                .orElseGet(() -> new IntermediateResultsResponse(
                        artifactId,
                        run.assessmentRunId,
                        runProjectName,
                        List.of()
                ));
    }

    public synchronized URI getFileDownloadLocation(String artifactId, String sha256) {
        ArtifactState artifact = requireArtifact(artifactId);
        String normalizedSha256 = validateSha256(sha256);
        ImageState image = artifact.images.values().stream()
                .filter(candidate -> normalizedSha256.equals(candidate.sha256)
                        && "UPLOADED".equals(candidate.status))
                .findFirst()
                .orElseThrow(() -> new VcaApiException(
                        HttpStatus.NOT_FOUND,
                        "FILE_NOT_FOUND",
                        "The requested VCA file was not found."
                ));
        if (image.objectKey != null && imageStorage.isPresent()) {
            return imageStorage.get().presignedDownload(image.objectKey);
        }
        Instant expiresAt = Instant.now().plus(5, ChronoUnit.MINUTES);
        return URI.create(LOCAL_ORIGIN + "/files/" + normalizedSha256
                + "?expires=" + expiresAt.getEpochSecond()
                + "&signature=" + UUID.randomUUID().toString().replace("-", ""));
    }

    public synchronized PdfJobResponse createPdfJob(
            String artifactId,
            String assessmentRunId
    ) {
        ArtifactState artifact = requireArtifact(artifactId);
        RunState run = requireRun(artifact, assessmentRunId);
        syncRunWithAi(artifact, run);
        if (!"COMPLETED".equals(run.status)) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "NOT_READY",
                    "The assessment report must be completed before creating a PDF."
            );
        }
        String runKey = artifactId + "/" + assessmentRunId;
        String existingJobId = pdfJobIdsByRun.get(runKey);
        if (existingJobId != null) {
            return toPdfJob(pdfJobs.get(existingJobId));
        }

        Instant now = Instant.now();
        String jobId = UUID.randomUUID().toString();
        PdfJobState job = new PdfJobState(jobId, artifactId, assessmentRunId, "QUEUED", now, now);
        pdfJobs.put(jobId, job);
        pdfJobIdsByRun.put(runKey, jobId);
        log.info("VCA report PDF queued assessmentRunId={} jobId={}", assessmentRunId, jobId);
        return toPdfJob(job);
    }

    public synchronized PdfJobResponse getPdfJob(String artifactId, String jobId) {
        PdfJobState job = requirePdfJob(artifactId, jobId);
        if ("QUEUED".equals(job.status) || "RUNNING".equals(job.status)) {
            job.status = "COMPLETED";
            job.updatedAt = Instant.now();
            log.info("VCA demo report PDF completed jobId={}", jobId);
        }
        return toPdfJob(job);
    }

    public synchronized URI getPdfDownloadLocation(String artifactId, String jobId) {
        PdfJobState job = requirePdfJob(artifactId, jobId);
        if (!"COMPLETED".equals(job.status)) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "PDF_NOT_READY",
                    "The report PDF is not ready for download."
            );
        }
        Instant expiresAt = Instant.now().plus(5, ChronoUnit.MINUTES);
        return URI.create(LOCAL_ORIGIN + "/downloads/" + job.jobId + ".pdf"
                + "?expires=" + expiresAt.getEpochSecond()
                + "&signature=" + UUID.randomUUID().toString().replace("-", ""));
    }

    private ArtifactSummary toSummary(ArtifactState artifact) {
        String latestRunId = new ArrayList<>(artifact.runs.keySet()).stream()
                .reduce((first, second) -> second)
                .orElse(null);
        return new ArtifactSummary(
                artifact.artifactId,
                artifact.displayName,
                firstUploadedImageUrl(artifact),
                latestRunId == null ? null : toRun(artifact, artifact.runs.get(latestRunId)),
                artifact.runs.size(),
                artifact.updatedAt
        );
    }

    private ArtifactDetailResponse toDetail(ArtifactState artifact) {
        return new ArtifactDetailResponse(
                artifact.artifactId,
                artifact.displayName,
                artifactStatus(artifact),
                artifact.images.values().stream()
                        .filter(image -> "UPLOADED".equals(image.status))
                        .map(image -> toImage(artifact, image))
                        .toList(),
                artifact.runs.values().stream().map(run -> toRun(artifact, run)).toList(),
                artifact.createdAt,
                artifact.updatedAt
        );
    }

    private ImageResponse toImage(ArtifactState artifact, ImageState image) {
        String downloadUrl = "UPLOADED".equals(image.status)
                ? fileGatewayUrl(artifact.artifactId, image.sha256)
                : null;
        return new ImageResponse(
                image.imageId,
                image.fileName,
                image.contentType,
                image.sizeBytes,
                image.status,
                downloadUrl,
                image.createdAt,
                image.uploadedAt
        );
    }

    private RunResponse toRun(ArtifactState artifact, RunState run) {
        return new RunResponse(
                run.assessmentRunId,
                artifact.artifactId,
                run.status,
                run.imageCount,
                run.createdAt,
                run.completedAt,
                "/api/vca/" + artifact.artifactId + "/runs/"
                        + run.assessmentRunId + "/report"
        );
    }

    private PdfJobResponse toPdfJob(PdfJobState job) {
        String downloadUrl = "COMPLETED".equals(job.status)
                ? "/api/vca/" + job.artifactId + "/report-pdf-jobs/"
                        + job.jobId + "/download"
                : null;
        return new PdfJobResponse(
                job.jobId,
                job.assessmentRunId,
                job.status,
                job.createdAt,
                job.updatedAt,
                downloadUrl
        );
    }

    private void advanceDemoRuns(ArtifactState artifact) {
        Instant now = Instant.now();
        for (RunState run : artifact.runs.values()) {
            if ("COMPLETED".equals(run.status) || "FAILED".equals(run.status)) {
                continue;
            }
            if (vcaAiGateway.isPresent()) {
                syncRunWithAi(artifact, run);
                continue;
            }
            long elapsedMillis = ChronoUnit.MILLIS.between(run.createdAt, now);
            if (elapsedMillis >= 3_000) {
                run.status = "COMPLETED";
                run.completedAt = now;
                artifact.updatedAt = now;
                log.info("VCA demo assessment completed by artifact polling artifactId={} assessmentRunId={}",
                        artifact.artifactId, run.assessmentRunId);
            } else if (elapsedMillis >= 900 && "QUEUED".equals(run.status)) {
                run.status = "RUNNING";
                artifact.updatedAt = now;
                log.info("VCA demo assessment running artifactId={} assessmentRunId={}",
                        artifact.artifactId, run.assessmentRunId);
            }
        }
    }

    private VcaAiAssessmentRun createAiAssessmentRun(
            String artifactId,
            String assessmentRunId,
            List<ImageState> uploadedImages
    ) {
        if (vcaAiGateway.isEmpty()) {
            return new VcaAiAssessmentRun(assessmentRunId, assessmentRunId, "QUEUED");
        }
        VcaSharedStorage.RunInputDirectory inputDirectory;
        if (imageStorage.isPresent() && uploadedImages.stream().anyMatch(image -> image.objectKey != null)) {
            inputDirectory = imageStorage.get().materializeRunInput(
                    assessmentRunId,
                    uploadedImages.stream()
                            .map(image -> new VcaImageStorage.StoredImageReference(
                                    image.imageId,
                                    image.fileName,
                                    image.objectKey
                            ))
                            .toList()
            );
        } else {
            inputDirectory = requireSharedStorage()
                    .materializeRunInput(
                            assessmentRunId,
                            uploadedImages.stream()
                                    .map(image -> new VcaSharedStorage.StoredImageReference(
                                            image.imageId,
                                            image.fileName,
                                            image.localPath
                                    ))
                                    .toList()
                    );
        }
        return vcaAiGateway.get().createAssessmentRun(
                assessmentRunId,
                projectName(artifactId, assessmentRunId),
                inputDirectory.containerPath()
        );
    }

    private VcaSharedStorage requireSharedStorage() {
        return sharedStorage.orElseThrow(() -> new VcaApiException(
                HttpStatus.CONFLICT,
                "VCA_STORAGE_UNAVAILABLE",
                "VCA shared storage is not configured."
        ));
    }

    private VcaCorpusStorage requireCorpusStorage() {
        return corpusStorage.orElseThrow(() -> new VcaApiException(
                HttpStatus.CONFLICT,
                "VCA_STORAGE_UNAVAILABLE",
                "VCA PDF corpus storage is not configured."
        ));
    }

    private void syncRunWithAi(ArtifactState artifact, RunState run) {
        if (vcaAiGateway.isEmpty() || run.aiRunId == null) {
            return;
        }
        VcaAiAssessmentRun aiStatus = vcaAiGateway.get().getAssessmentStatus(run.aiRunId);
        run.status = aiStatus.status();
        if ("COMPLETED".equals(run.status) && run.completedAt == null) {
            run.completedAt = Instant.now();
        }
        artifact.updatedAt = Instant.now();
    }

    private ReportResponse toReport(
            ArtifactState artifact,
            RunState run,
            VcaAiAssessmentReport aiReport
    ) {
        String primaryImageId = artifact.images.values().stream()
                .filter(image -> "UPLOADED".equals(image.status))
                .findFirst()
                .map(image -> image.imageId)
                .orElse(null);
        List<ReportResponse.Image> reportImages = artifact.images.values().stream()
                .filter(image -> "UPLOADED".equals(image.status))
                .map(image -> new ReportResponse.Image(
                        image.imageId,
                        image.fileName,
                        fileGatewayUrl(artifact.artifactId, image.sha256)
                ))
                .toList();
        return new ReportResponse(
                run.assessmentRunId,
                artifact.artifactId,
                aiReport.status(),
                Instant.now(),
                new ReportResponse.Summary(
                        "FAIR",
                        "LOW",
                        REPORT_HEADLINE,
                        aiReport.summary()
                ),
                toFindings(aiReport.findings(), primaryImageId),
                List.of(new ReportResponse.Recommendation(
                        "recommendation-review-vca-results",
                        "MEDIUM",
                        RECOMMENDATION_TITLE,
                        RECOMMENDATION_DESCRIPTION
                )),
                reportImages,
                run.potteryInspection,
                run.potteryInspectionStatus
        );
    }

    private ReportResponse.PotteryInspection inspectPottery(ImageState primaryImage) {
        PotteryInspectionResponseDto response = potteryInspectionAiClient.get().inspect(
                toMultipartFile(primaryImage),
                POTTERY_INSPECTION_CALLS,
                true
        );
        return toPotteryInspection(response);
    }

    private static boolean isPotteryMaterial(String material) {
        if (material == null) {
            return false;
        }
        String normalized = material.toLowerCase(Locale.ROOT);
        return normalized.contains("도자")
                || normalized.contains("pottery")
                || normalized.contains("ceramic");
    }

    private MultipartFile toMultipartFile(ImageState image) {
        try {
            byte[] bytes;
            if (image.objectKey != null && imageStorage.isPresent()) {
                bytes = imageStorage.get().read(image.objectKey, image.fileName, image.contentType).bytes();
            } else if (image.localPath != null) {
                bytes = Files.readAllBytes(image.localPath);
            } else {
                throw new VcaApiException(
                        HttpStatus.CONFLICT,
                        "NOT_READY",
                        "Uploaded image bytes are required before pottery inspection."
                );
            }
            return new StoredImageMultipartFile(image.fileName, image.contentType, bytes);
        } catch (IOException exception) {
            throw new VcaApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "UPLOAD_STORAGE_READ_FAILED",
                    "Failed to read the stored VCA image."
            );
        }
    }

    private static ReportResponse.PotteryInspection toPotteryInspection(
            PotteryInspectionResponseDto response
    ) {
        return new ReportResponse.PotteryInspection(
                response.moduleVersion(),
                response.inspectionText(),
                response.summary(),
                response.humanReviewRecommended(),
                response.detail()
        );
    }

    private static String potteryMaterial(PotteryInspectionRequest request, RunState run) {
        if (request != null && request.material() != null && !request.material().isBlank()) {
            run.material = request.material();
        }
        return run.material;
    }

    private static ReportResponse.PotteryInspectionStatus initialPotteryInspectionStatus(String material) {
        if (!isPotteryMaterial(material)) {
            return null;
        }
        return new ReportResponse.PotteryInspectionStatus(
                true,
                POTTERY_STATUS_NOT_STARTED,
                true,
                null,
                null
        );
    }

    private void ensureReportReady(ArtifactState artifact, RunState run) {
        if (run.report != null) {
            return;
        }
        if (vcaAiGateway.isPresent()) {
            VcaAiAssessmentReport aiReport = vcaAiGateway.get().getAssessmentReport(run.aiRunId);
            run.report = toReport(artifact, run, aiReport);
        } else {
            List<ReportResponse.Image> reportImages = run.uploadedImages.stream()
                    .map(image -> new ReportResponse.Image(
                            image.imageId,
                            image.fileName,
                            fileGatewayUrl(artifact.artifactId, image.sha256)
                    ))
                    .toList();
            run.report = VcaDemoReportFactory.create(
                    artifact.artifactId,
                    run.assessmentRunId,
                    run.createdAt,
                    reportImages
            );
        }
        run.report = withPotteryInspectionState(run.report, run);
    }

    private static ReportResponse withPotteryInspectionState(
            ReportResponse report,
            RunState run
    ) {
        return new ReportResponse(
                report.assessmentRunId(),
                report.artifactId(),
                report.status(),
                report.generatedAt(),
                report.summary(),
                report.findings(),
                report.recommendations(),
                report.images(),
                run.potteryInspection,
                run.potteryInspectionStatus
        );
    }

    private List<ReportResponse.Finding> toFindings(
            List<VcaAiAssessmentFinding> aiFindings,
            String primaryImageId
    ) {
        List<ReportResponse.Finding> findings = new ArrayList<>();
        for (int index = 0; index < aiFindings.size(); index++) {
            VcaAiAssessmentFinding finding = aiFindings.get(index);
            findings.add(new ReportResponse.Finding(
                    "finding-vca-ai-" + (index + 1),
                    finding.category(),
                    finding.severity(),
                    FINDING_TITLE,
                    finding.message(),
                    1.0,
                    primaryImageId
            ));
        }
        return findings;
    }

    private String artifactStatus(ArtifactState artifact) {
        if (artifact.runs.values().stream().anyMatch(run ->
                "QUEUED".equals(run.status) || "RUNNING".equals(run.status))) {
            return "ANALYZING";
        }
        if (artifact.runs.values().stream().anyMatch(run -> "COMPLETED".equals(run.status))) {
            return "ASSESSED";
        }
        if (artifact.images.values().stream().anyMatch(image -> "UPLOADED".equals(image.status))) {
            return "READY";
        }
        return "DRAFT";
    }

    private ArtifactState requireArtifact(String artifactId) {
        validateArtifactId(artifactId);
        ArtifactState artifact = artifacts.get(artifactId);
        if (artifact == null) {
            throw new VcaApiException(
                    HttpStatus.NOT_FOUND,
                    "ARTIFACT_NOT_FOUND",
                    "The requested VCA artifact was not found."
            );
        }
        return artifact;
    }

    private ArtifactState getOrCreateArtifact(String artifactId) {
        validateArtifactId(artifactId);
        return artifacts.computeIfAbsent(
                artifactId,
                id -> new ArtifactState(id, "Artifact " + id, Instant.now())
        );
    }

    private ImageState requireImage(ArtifactState artifact, String imageId) {
        ImageState image = artifact.images.get(imageId);
        if (image == null) {
            throw new VcaApiException(
                    HttpStatus.NOT_FOUND,
                    "IMAGE_NOT_FOUND",
                    "The requested VCA image was not found."
            );
        }
        return image;
    }

    private RunState requireRun(ArtifactState artifact, String assessmentRunId) {
        validateUuid(assessmentRunId, "assessmentRunId");
        RunState run = artifact.runs.get(assessmentRunId);
        if (run == null) {
            throw new VcaApiException(
                    HttpStatus.NOT_FOUND,
                    "RUN_NOT_FOUND",
                    "The requested VCA assessment run was not found."
            );
        }
        return run;
    }

    private PdfJobState requirePdfJob(String artifactId, String jobId) {
        validateArtifactId(artifactId);
        validateUuid(jobId, "jobId");
        PdfJobState job = pdfJobs.get(jobId);
        if (job == null || !artifactId.equals(job.artifactId)) {
            throw new VcaApiException(
                    HttpStatus.NOT_FOUND,
                    "PDF_JOB_NOT_FOUND",
                    "The requested VCA report PDF job was not found."
            );
        }
        return job;
    }

    private void validateArtifactId(String artifactId) {
        if (artifactId == null || !ARTIFACT_ID.matcher(artifactId).matches()) {
            throw new VcaApiException(
                    HttpStatus.BAD_REQUEST,
                    "VALIDATION_ERROR",
                    "artifactId must contain only letters, numbers, '_' or '-'."
            );
        }
    }

    private String validateSha256(String sha256) {
        if (sha256 == null || !SHA256.matcher(sha256).matches()) {
            throw new VcaApiException(
                    HttpStatus.BAD_REQUEST,
                    "VALIDATION_ERROR",
                    "sha256 must be a 64-character hexadecimal digest."
            );
        }
        return sha256.toLowerCase(Locale.ROOT);
    }

    private void validateUuid(String value, String fieldName) {
        try {
            UUID.fromString(value);
        } catch (IllegalArgumentException | NullPointerException exception) {
            throw new VcaApiException(
                    HttpStatus.BAD_REQUEST,
                    "VALIDATION_ERROR",
                    fieldName + " must be a UUID."
            );
        }
    }

    private String fileGatewayUrl(String artifactId, String sha256) {
        return "/api/vca/" + artifactId + "/files/sha256/" + sha256;
    }

    private String uploadMode() {
        if (localDirectCompleteEnabled) {
            return "DIRECT_COMPLETE";
        }
        return "SIGNED_PUT";
    }

    private String projectName(String artifactId, String assessmentRunId) {
        return artifactId + "-" + assessmentRunId;
    }

    private String firstUploadedImageUrl(ArtifactState artifact) {
        return artifact.images.values().stream()
                .filter(image -> "UPLOADED".equals(image.status))
                .findFirst()
                .map(image -> fileGatewayUrl(artifact.artifactId, image.sha256))
                .orElse(null);
    }

    private void seedDemoArtifact() {
        Instant now = Instant.now();
        ArtifactState artifact = new ArtifactState(
                "demo-artifact",
                "Demo Cultural Artifact",
                now
        );
        artifacts.put(artifact.artifactId, artifact);
    }

    private static final class ArtifactState {
        private final String artifactId;
        private final String displayName;
        private final Instant createdAt;
        private final Map<String, ImageState> images = new LinkedHashMap<>();
        private final Map<String, RunState> runs = new LinkedHashMap<>();
        private Instant updatedAt;

        private ArtifactState(String artifactId, String displayName, Instant now) {
            this.artifactId = artifactId;
            this.displayName = displayName;
            this.createdAt = now;
            this.updatedAt = now;
        }
    }

    private static final class ImageState {
        private final String imageId;
        private final String fileName;
        private final String contentType;
        private final long sizeBytes;
        private final String sha256;
        private final String uploadMode;
        private final Instant createdAt;
        private final String objectKey;
        private final Path localPath;
        private String status;
        private Instant uploadedAt;

        private ImageState(
                String imageId,
                String fileName,
                String contentType,
                long sizeBytes,
                String sha256,
                String uploadMode,
                String status,
                Instant createdAt,
                Instant uploadedAt,
                String objectKey,
                Path localPath
        ) {
            this.imageId = imageId;
            this.fileName = fileName;
            this.contentType = contentType;
            this.sizeBytes = sizeBytes;
            this.sha256 = sha256;
            this.uploadMode = uploadMode;
            this.status = status;
            this.createdAt = createdAt;
            this.uploadedAt = uploadedAt;
            this.objectKey = objectKey;
            this.localPath = localPath;
        }
    }

    private static final class RunCreation {
        private final String assessmentRunId;
        private final List<ImageState> uploadedImages;

        private RunCreation(String assessmentRunId, List<ImageState> uploadedImages) {
            this.assessmentRunId = assessmentRunId;
            this.uploadedImages = uploadedImages;
        }
    }

    private static final class RunState {
        private final String assessmentRunId;
        private final int imageCount;
        private final Instant createdAt;
        private final List<ImageState> uploadedImages;
        private String aiRunId;
        private String status;
        private Instant completedAt;
        private String material;
        private ReportResponse report;
        private ReportResponse.PotteryInspection potteryInspection;
        private ReportResponse.PotteryInspectionStatus potteryInspectionStatus;

        private RunState(
                String assessmentRunId,
                String status,
                int imageCount,
                Instant createdAt,
                Instant completedAt,
                String aiRunId,
                ReportResponse report,
                List<ImageState> uploadedImages
        ) {
            this.assessmentRunId = assessmentRunId;
            this.status = status;
            this.imageCount = imageCount;
            this.createdAt = createdAt;
            this.completedAt = completedAt;
            this.aiRunId = aiRunId;
            this.report = report;
            this.uploadedImages = List.copyOf(uploadedImages);
        }
    }

    private record PotteryInspectionTarget(
            boolean applicable,
            ImageState primaryImage
    ) {
    }

    private record StoredImageMultipartFile(
            String originalFilename,
            String contentType,
            byte[] bytes
    ) implements MultipartFile {

        @Override
        public String getName() {
            return "image";
        }

        @Override
        public String getOriginalFilename() {
            return originalFilename;
        }

        @Override
        public String getContentType() {
            return contentType;
        }

        @Override
        public boolean isEmpty() {
            return bytes.length == 0;
        }

        @Override
        public long getSize() {
            return bytes.length;
        }

        @Override
        public byte[] getBytes() {
            return bytes.clone();
        }

        @Override
        public InputStream getInputStream() {
            return new java.io.ByteArrayInputStream(bytes);
        }

        @Override
        public void transferTo(java.io.File dest) throws IOException {
            Files.write(dest.toPath(), bytes);
        }
    }

    private static final class PdfJobState {
        private final String jobId;
        private final String artifactId;
        private final String assessmentRunId;
        private final Instant createdAt;
        private String status;
        private Instant updatedAt;

        private PdfJobState(
                String jobId,
                String artifactId,
                String assessmentRunId,
                String status,
                Instant createdAt,
                Instant updatedAt
        ) {
            this.jobId = jobId;
            this.artifactId = artifactId;
            this.assessmentRunId = assessmentRunId;
            this.status = status;
            this.createdAt = createdAt;
            this.updatedAt = updatedAt;
        }
    }
}
