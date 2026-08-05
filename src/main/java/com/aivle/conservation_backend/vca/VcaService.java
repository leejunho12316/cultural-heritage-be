package com.aivle.conservation_backend.vca;

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
import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentStatus;
import com.aivle.conservation_backend.vca.gateway.VcaAiGateway;

import java.net.URI;
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

    private final Map<String, ArtifactState> artifacts = new LinkedHashMap<>();
    private final Map<String, ImageState> uploadedImagesBySha256 = new LinkedHashMap<>();
    private final Map<String, PdfJobState> pdfJobs = new LinkedHashMap<>();
    private final Map<String, String> pdfJobIdsByRun = new LinkedHashMap<>();
    private final boolean localDirectCompleteEnabled;
    private final Optional<VcaAiGateway> vcaAiGateway;
    private final Optional<VcaSharedStorage> sharedStorage;
    private final Optional<VcaIntermediateResultStorage> intermediateResultStorage;

    @Autowired
    public VcaService(
            @Value("${vca.local-direct-complete-enabled:false}") boolean localDirectCompleteEnabled,
            VcaAiGateway vcaAiGateway,
            VcaSharedStorage sharedStorage,
            VcaIntermediateResultStorage intermediateResultStorage
    ) {
        this(
                localDirectCompleteEnabled,
                Optional.of(vcaAiGateway),
                Optional.of(sharedStorage),
                Optional.of(intermediateResultStorage)
        );
    }

    VcaService(boolean localDirectCompleteEnabled) {
        this(localDirectCompleteEnabled, Optional.empty(), Optional.empty(), Optional.empty());
    }

    VcaService(boolean localDirectCompleteEnabled, VcaAiGateway vcaAiGateway) {
        this(
                localDirectCompleteEnabled,
                Optional.of(vcaAiGateway),
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
                Optional.empty()
        );
    }

    private VcaService(
            boolean localDirectCompleteEnabled,
            Optional<VcaAiGateway> vcaAiGateway,
            Optional<VcaSharedStorage> sharedStorage,
            Optional<VcaIntermediateResultStorage> intermediateResultStorage
    ) {
        this.localDirectCompleteEnabled = localDirectCompleteEnabled;
        this.vcaAiGateway = vcaAiGateway;
        this.sharedStorage = sharedStorage;
        this.intermediateResultStorage = intermediateResultStorage;
        seedDemoArtifact();
    }

    public synchronized VcaResponses.ArtifactCollection getArtifacts() {
        return new VcaResponses.ArtifactCollection(
                artifacts.values().stream().map(artifact -> {
                    advanceDemoRuns(artifact);
                    return toSummary(artifact);
                }).toList()
        );
    }

    public synchronized VcaResponses.ArtifactDetail getArtifact(String artifactId) {
        ArtifactState artifact = getOrCreateArtifact(artifactId);
        advanceDemoRuns(artifact);
        return toDetail(artifact);
    }

    public synchronized VcaResponses.PresignImage presignImage(
            String artifactId,
            VcaRequests.PresignImageRequest request
    ) {
        ArtifactState artifact = getOrCreateArtifact(artifactId);
        Instant now = Instant.now();
        String imageId = UUID.randomUUID().toString();
        String sha256 = request.sha256().toLowerCase(Locale.ROOT);
        ImageState image = new ImageState(
                imageId,
                request.fileName(),
                request.contentType(),
                request.sizeBytes(),
                sha256,
                uploadMode(),
                "PENDING",
                now,
                null,
                null
        );
        artifact.images.put(imageId, image);
        artifact.updatedAt = now;

        Instant expiresAt = now.plus(15, ChronoUnit.MINUTES);
        String signature = UUID.randomUUID().toString().replace("-", "");
        String uploadUrl = LOCAL_ORIGIN + "/uploads/" + imageId
                + "?expires=" + expiresAt.getEpochSecond()
                + "&signature=" + signature;
        Map<String, String> requiredHeaders = Map.of(
                "Content-Type", request.contentType(),
                "X-VCA-Content-SHA256", sha256
        );
        log.info("VCA image upload reserved artifactId={} imageId={}", artifactId, imageId);
        return new VcaResponses.PresignImage(
                imageId,
                "PENDING",
                image.uploadMode,
                uploadUrl,
                "PUT",
                requiredHeaders,
                expiresAt
        );
    }

    public synchronized VcaResponses.Image uploadImage(String artifactId, MultipartFile file) {
        ArtifactState artifact = getOrCreateArtifact(artifactId);
        String imageId = UUID.randomUUID().toString();
        VcaSharedStorage.StoredImage storedImage = requireSharedStorage().storeUpload(imageId, file);
        Instant now = Instant.now();
        ImageState image = new ImageState(
                imageId,
                storedImage.fileName(),
                storedImage.contentType(),
                storedImage.sizeBytes(),
                storedImage.sha256(),
                "DIRECT_UPLOAD",
                "UPLOADED",
                now,
                now,
                storedImage.localPath()
        );
        artifact.images.put(imageId, image);
        artifact.updatedAt = now;
        uploadedImagesBySha256.put(image.sha256, image);
        log.info("VCA image uploaded through Spring artifactId={} imageId={}", artifactId, imageId);
        return toImage(artifact, image);
    }

    public synchronized VcaResponses.Image completeImage(
            String artifactId,
            String imageId,
            VcaRequests.CompleteImageRequest request
    ) {
        ArtifactState artifact = requireArtifact(artifactId);
        validateUuid(imageId, "imageId");
        ImageState image = requireImage(artifact, imageId);
        if (!"DIRECT_COMPLETE".equals(image.uploadMode)) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "UPLOAD_NOT_VERIFIED",
                    "The upload object must be verified before completion."
            );
        }
        String suppliedSha256 = request.sha256().toLowerCase(Locale.ROOT);
        if (!image.sha256.equals(suppliedSha256)) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "SHA256_MISMATCH",
                    "The completed upload checksum does not match the reserved image."
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
        if (removed.localPath != null) {
            requireSharedStorage().deleteUpload(removed.imageId, removed.localPath);
        }
        artifact.updatedAt = Instant.now();
        log.info("VCA image metadata deleted artifactId={} imageId={}", artifactId, imageId);
    }

    public synchronized VcaResponses.Run createRun(String artifactId) {
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
        VcaAiAssessmentRun aiRun = createAiAssessmentRun(
                artifactId,
                assessmentRunId,
                uploadedImages
        );
        List<VcaResponses.ReportImage> reportImages = uploadedImages.stream()
                .map(image -> new VcaResponses.ReportImage(
                        image.imageId,
                        image.fileName,
                        fileGatewayUrl(artifactId, image.sha256)
                ))
                .toList();
        VcaResponses.Report report = VcaDemoReportFactory.create(
                artifactId,
                assessmentRunId,
                now,
                reportImages
        );
        RunState run = new RunState(
                assessmentRunId,
                aiRun.status(),
                uploadedImages.size(),
                now,
                "COMPLETED".equals(aiRun.status()) ? now : null,
                aiRun.runId(),
                report
        );
        artifact.runs.put(assessmentRunId, run);
        artifact.updatedAt = now;
        log.info("VCA assessment queued artifactId={} assessmentRunId={} imageCount={}",
                artifactId, assessmentRunId, uploadedImages.size());
        return toRun(artifact, run);
    }

    public synchronized VcaResponses.Report getReport(
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

    public synchronized VcaResponses.IntermediateResults getIntermediateResults(
            String artifactId,
            String assessmentRunId
    ) {
        ArtifactState artifact = requireArtifact(artifactId);
        RunState run = requireRun(artifact, assessmentRunId);
        String runProjectName = projectName(artifactId, run.assessmentRunId);
        return intermediateResultStorage
                .map(storage -> storage.read(artifactId, run.assessmentRunId, runProjectName))
                .orElseGet(() -> new VcaResponses.IntermediateResults(
                        artifactId,
                        run.assessmentRunId,
                        runProjectName,
                        List.of()
                ));
    }

    public synchronized URI getFileDownloadLocation(String artifactId, String sha256) {
        ArtifactState artifact = requireArtifact(artifactId);
        String normalizedSha256 = validateSha256(sha256);
        boolean fileBelongsToArtifact = artifact.images.values().stream()
                .anyMatch(image -> normalizedSha256.equals(image.sha256)
                        && "UPLOADED".equals(image.status));
        if (!fileBelongsToArtifact) {
            throw new VcaApiException(
                    HttpStatus.NOT_FOUND,
                    "FILE_NOT_FOUND",
                    "The requested VCA file was not found."
            );
        }
        Instant expiresAt = Instant.now().plus(5, ChronoUnit.MINUTES);
        return URI.create(LOCAL_ORIGIN + "/files/" + normalizedSha256
                + "?expires=" + expiresAt.getEpochSecond()
                + "&signature=" + UUID.randomUUID().toString().replace("-", ""));
    }

    public synchronized VcaResponses.PdfJob createPdfJob(
            String artifactId,
            String assessmentRunId
    ) {
        ArtifactState artifact = requireArtifact(artifactId);
        RunState run = requireRun(artifact, assessmentRunId);
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

    public synchronized VcaResponses.PdfJob getPdfJob(String artifactId, String jobId) {
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

    private VcaResponses.ArtifactSummary toSummary(ArtifactState artifact) {
        String latestRunId = new ArrayList<>(artifact.runs.keySet()).stream()
                .reduce((first, second) -> second)
                .orElse(null);
        return new VcaResponses.ArtifactSummary(
                artifact.artifactId,
                artifact.displayName,
                firstUploadedImageUrl(artifact),
                latestRunId == null ? null : toRun(artifact, artifact.runs.get(latestRunId)),
                artifact.runs.size(),
                artifact.updatedAt
        );
    }

    private VcaResponses.ArtifactDetail toDetail(ArtifactState artifact) {
        return new VcaResponses.ArtifactDetail(
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

    private VcaResponses.Image toImage(ArtifactState artifact, ImageState image) {
        String downloadUrl = "UPLOADED".equals(image.status)
                ? fileGatewayUrl(artifact.artifactId, image.sha256)
                : null;
        return new VcaResponses.Image(
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

    private VcaResponses.Run toRun(ArtifactState artifact, RunState run) {
        return new VcaResponses.Run(
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

    private VcaResponses.PdfJob toPdfJob(PdfJobState job) {
        String downloadUrl = "COMPLETED".equals(job.status)
                ? "/api/vca/" + job.artifactId + "/report-pdf-jobs/"
                        + job.jobId + "/download"
                : null;
        return new VcaResponses.PdfJob(
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
        VcaSharedStorage.RunInputDirectory inputDirectory = requireSharedStorage()
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

    private void syncRunWithAi(ArtifactState artifact, RunState run) {
        if (vcaAiGateway.isEmpty()) {
            return;
        }
        VcaAiAssessmentStatus aiStatus = vcaAiGateway.get().getAssessmentStatus(run.aiRunId);
        run.status = aiStatus.status();
        if ("COMPLETED".equals(run.status) && run.completedAt == null) {
            run.completedAt = Instant.now();
        }
        artifact.updatedAt = Instant.now();
    }

    private VcaResponses.Report toReport(
            ArtifactState artifact,
            RunState run,
            VcaAiAssessmentReport aiReport
    ) {
        String primaryImageId = artifact.images.values().stream()
                .filter(image -> "UPLOADED".equals(image.status))
                .findFirst()
                .map(image -> image.imageId)
                .orElse(null);
        List<VcaResponses.ReportImage> reportImages = artifact.images.values().stream()
                .filter(image -> "UPLOADED".equals(image.status))
                .map(image -> new VcaResponses.ReportImage(
                        image.imageId,
                        image.fileName,
                        fileGatewayUrl(artifact.artifactId, image.sha256)
                ))
                .toList();
        return new VcaResponses.Report(
                run.assessmentRunId,
                artifact.artifactId,
                aiReport.status(),
                Instant.now(),
                new VcaResponses.ReportSummary(
                        "FAIR",
                        "LOW",
                        "VCA adapter placeholder",
                        aiReport.summary()
                ),
                toFindings(aiReport.findings(), primaryImageId),
                List.of(new VcaResponses.Recommendation(
                        "recommendation-connect-vca-pipeline",
                        "MEDIUM",
                        "Connect production VCA pipeline",
                        "Replace the deterministic adapter with the vca_v2 orchestration pipeline."
                )),
                reportImages
        );
    }

    private List<VcaResponses.Finding> toFindings(
            List<VcaAiAssessmentFinding> aiFindings,
            String primaryImageId
    ) {
        List<VcaResponses.Finding> findings = new ArrayList<>();
        for (int index = 0; index < aiFindings.size(); index++) {
            VcaAiAssessmentFinding finding = aiFindings.get(index);
            findings.add(new VcaResponses.Finding(
                    "finding-vca-ai-" + (index + 1),
                    finding.category(),
                    finding.severity(),
                    "VCA adapter finding",
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
            this.localPath = localPath;
        }
    }

    private static final class RunState {
        private final String assessmentRunId;
        private final int imageCount;
        private final Instant createdAt;
        private final String aiRunId;
        private String status;
        private Instant completedAt;
        private VcaResponses.Report report;

        private RunState(
                String assessmentRunId,
                String status,
                int imageCount,
                Instant createdAt,
                Instant completedAt,
                String aiRunId,
                VcaResponses.Report report
        ) {
            this.assessmentRunId = assessmentRunId;
            this.status = status;
            this.imageCount = imageCount;
            this.createdAt = createdAt;
            this.completedAt = completedAt;
            this.aiRunId = aiRunId;
            this.report = report;
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
