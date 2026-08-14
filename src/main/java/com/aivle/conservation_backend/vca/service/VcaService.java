package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.pottery_inspection_ai.client.PotteryInspectionAiClient;
import com.aivle.conservation_backend.pottery_inspection_ai.dto.PotteryInspectionResponseDto;
import com.aivle.conservation_backend.vca.domain.InspectionResultPottery;
import com.aivle.conservation_backend.vca.domain.VcaArtifactEntity;
import com.aivle.conservation_backend.vca.domain.AssessmentReport;
import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import com.aivle.conservation_backend.vca.domain.ReportPdfJob;
import com.aivle.conservation_backend.vca.domain.UploadedImage;
import com.aivle.conservation_backend.vca.dto.ArtifactCollectionResponse;
import com.aivle.conservation_backend.vca.dto.ArtifactCollectionResponse.ArtifactSummary;
import com.aivle.conservation_backend.vca.dto.ArtifactDetailResponse;
import com.aivle.conservation_backend.vca.dto.CompleteImageRequest;
import com.aivle.conservation_backend.vca.dto.CreateArtifactRequest;
import com.aivle.conservation_backend.vca.dto.CreateRunRequest;
import com.aivle.conservation_backend.vca.dto.ImageResponse;
import com.aivle.conservation_backend.vca.dto.IntermediateResultsResponse;
import com.aivle.conservation_backend.vca.dto.PdfJobResponse;
import com.aivle.conservation_backend.vca.dto.PotteryInspectionRequest;
import com.aivle.conservation_backend.vca.dto.PresignImageRequest;
import com.aivle.conservation_backend.vca.dto.PresignImageResponse;
import com.aivle.conservation_backend.vca.dto.ReportResponse;
import com.aivle.conservation_backend.vca.dto.RunResponse;
import com.aivle.conservation_backend.vca.dto.SystemInfoResponse;
import com.aivle.conservation_backend.vca.dto.VcaCorpusPdfCollectionResponse;
import com.aivle.conservation_backend.vca.dto.VcaCorpusPdfResponse;
import com.aivle.conservation_backend.vca.exception.VcaApiException;
import com.aivle.conservation_backend.vca.repository.VcaArtifactStore;
import com.aivle.conservation_backend.vca.repository.AssessmentReportStore;
import com.aivle.conservation_backend.vca.repository.AssessmentRunStore;
import com.aivle.conservation_backend.vca.repository.InspectionResultPotteryStore;
import com.aivle.conservation_backend.vca.repository.ReportPdfJobStore;
import com.aivle.conservation_backend.vca.repository.UploadedImageStore;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentFinding;
import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentReport;
import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentRun;
import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentStage;
import com.aivle.conservation_backend.vca.gateway.VcaAiAssessmentStageProgress;
import com.aivle.conservation_backend.vca.gateway.VcaAiGateway;
import com.aivle.conservation_backend.vca.gateway.VcaAiSystemInfo;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

@Service
public class VcaService {

    private static final Logger log = LoggerFactory.getLogger(VcaService.class);
    private static final Pattern SHA256 = Pattern.compile("^[A-Fa-f0-9]{64}$");
    private static final String LOCAL_ORIGIN = "https://vca-local.invalid";
    private static final String REPORT_HEADLINE = "VCA 육안 조사 결과";
    private static final String RECOMMENDATION_DESCRIPTION_FORMAT =
            "AI가 제안한 \"%s\" 유형 이상 후보를 전문가가 검토한 뒤 보존처리 계획에 반영하세요.";
    private static final int POTTERY_INSPECTION_CALLS = 1;
    private static final String POTTERY_STATUS_NOT_STARTED = "NOT_STARTED";
    private static final String POTTERY_STATUS_COMPLETED = "COMPLETED";
    private static final String POTTERY_STATUS_FAILED = "FAILED";

    private final VcaArtifactStore artifactStore;
    private final UploadedImageStore imageStore;
    private final AssessmentRunStore runStore;
    private final AssessmentReportStore reportStore;
    private final ReportPdfJobStore pdfJobStore;
    private final InspectionResultPotteryStore potteryResultStore;
    // 의존성 없는 순수 렌더러라 다른 협력 빈들과 달리 생성자 주입 없이 바로
    // 만든다 - 테스트용 생성자 오버로드 체인(VcaControllerTest 등)을 전부
    // 건드리지 않아도 되기 때문이다.
    private final VcaReportPdfRenderer pdfRenderer = new VcaReportPdfRenderer();
    private final boolean localDirectCompleteEnabled;
    private final Optional<VcaAiGateway> vcaAiGateway;
    private final Optional<VcaSharedStorage> sharedStorage;
    private final Optional<VcaImageStorage> imageStorage;
    private final Optional<VcaIntermediateResultStorage> intermediateResultStorage;
    private final Optional<PotteryInspectionAiClient> potteryInspectionAiClient;
    private final Optional<VcaCorpusStorage> corpusStorage;

    // Spring이 운영 환경에서 실제로 사용하는 생성자 - 모든 협력 빈을 필요로 한다.
    // 이하의 다른 생성자들은 전부 테스트에서 필요한 의존성만 부분적으로 주입하기 위한 것.
    @Autowired
    public VcaService(
            @Value("${vca.local-direct-complete-enabled:false}") boolean localDirectCompleteEnabled,
            VcaAiGateway vcaAiGateway,
            VcaSharedStorage sharedStorage,
            VcaImageStorage imageStorage,
            VcaIntermediateResultStorage intermediateResultStorage,
            PotteryInspectionAiClient potteryInspectionAiClient,
            VcaCorpusStorage corpusStorage,
            VcaArtifactStore artifactStore,
            UploadedImageStore imageStore,
            AssessmentRunStore runStore,
            AssessmentReportStore reportStore,
            ReportPdfJobStore pdfJobStore,
            InspectionResultPotteryStore potteryResultStore
    ) {
        this(
                localDirectCompleteEnabled,
                Optional.of(vcaAiGateway),
                Optional.of(sharedStorage),
                Optional.of(imageStorage),
                Optional.of(intermediateResultStorage),
                Optional.of(potteryInspectionAiClient),
                Optional.of(corpusStorage),
                artifactStore,
                imageStore,
                runStore,
                reportStore,
                pdfJobStore,
                potteryResultStore
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
            VcaImageStorage imageStorage,
            PotteryInspectionAiClient potteryInspectionAiClient
    ) {
        this(
                localDirectCompleteEnabled,
                Optional.of(vcaAiGateway),
                Optional.of(sharedStorage),
                Optional.of(imageStorage),
                Optional.empty(),
                Optional.of(potteryInspectionAiClient),
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
        this(
                localDirectCompleteEnabled,
                vcaAiGateway,
                sharedStorage,
                imageStorage,
                intermediateResultStorage,
                potteryInspectionAiClient,
                corpusStorage,
                new InMemoryVcaArtifactStore(),
                new InMemoryUploadedImageStore(),
                new InMemoryAssessmentRunStore(),
                new InMemoryAssessmentReportStore(),
                new InMemoryReportPdfJobStore(),
                new InMemoryInspectionResultPotteryStore()
        );
    }

    private VcaService(
            boolean localDirectCompleteEnabled,
            Optional<VcaAiGateway> vcaAiGateway,
            Optional<VcaSharedStorage> sharedStorage,
            Optional<VcaImageStorage> imageStorage,
            Optional<VcaIntermediateResultStorage> intermediateResultStorage,
            Optional<PotteryInspectionAiClient> potteryInspectionAiClient,
            Optional<VcaCorpusStorage> corpusStorage,
            VcaArtifactStore artifactStore,
            UploadedImageStore imageStore,
            AssessmentRunStore runStore,
            AssessmentReportStore reportStore,
            ReportPdfJobStore pdfJobStore,
            InspectionResultPotteryStore potteryResultStore
    ) {
        this.localDirectCompleteEnabled = localDirectCompleteEnabled;
        this.vcaAiGateway = vcaAiGateway;
        this.sharedStorage = sharedStorage;
        this.imageStorage = imageStorage;
        this.intermediateResultStorage = intermediateResultStorage;
        this.potteryInspectionAiClient = potteryInspectionAiClient;
        this.corpusStorage = corpusStorage;
        this.artifactStore = artifactStore;
        this.imageStore = imageStore;
        this.runStore = runStore;
        this.reportStore = reportStore;
        this.pdfJobStore = pdfJobStore;
        this.potteryResultStore = potteryResultStore;
    }

    // 새 유물을 만들고 서버가 생성한 id(UUID)를 응답에 담아 돌려준다. artifactId가 이제
    // 서버 생성 UUID라 클라이언트가 URL에 미리 넣을 값을 알 수 없으므로, 하위 경로
    // (/{artifactId}/...)에 접근하려면 먼저 이 메서드를 호출해야 한다.
    public synchronized ArtifactDetailResponse createArtifact(CreateArtifactRequest request) {
        // id는 일부러 비워둔다 - 엔티티의 @GeneratedValue(UUID)가 실제 저장 시점에 채운다.
        // 여기서 미리 UUID를 채워 넣으면 Hibernate가 "생성 컬럼인데 클라이언트가 값을
        // 줬다"고 보고 merge를 시도하다 없는 행을 찾지 못해 실패한다
        // (StaleObjectStateException). InMemoryVcaArtifactStore는 데모/테스트 전용
        // 폴백이라 id가 비어 있으면 스스로 채워 같은 계약을 유지한다.
        LocalDateTime now = LocalDateTime.now(ZoneOffset.UTC);
        VcaArtifactEntity artifact = VcaArtifactEntity.builder()
                .name(request.name())
                .createdAt(now)
                .updatedAt(now)
                .build();
        artifact = artifactStore.save(artifact);
        log.info("VCA artifact created artifactId={}", artifact.getId());
        return toDetail(artifact, List.of());
    }

    // GET /api/vca - 아티팩트 목록 조회. 조회할 때마다 진행 중인 데모 run들의 진행 상태를 갱신한다.
    public synchronized ArtifactCollectionResponse getArtifacts() {
        return new ArtifactCollectionResponse(
                artifactStore.findAll().stream()
                        .map(artifact -> toSummary(artifact, advanceDemoRuns(artifact)))
                        .toList()
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

    // GET /api/vca/{artifactId} - artifactId는 createArtifact가 미리 반환한 UUID여야 한다,
    // 없으면 404(requireArtifact).
    public synchronized ArtifactDetailResponse getArtifact(String artifactId) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        return toDetail(artifact, advanceDemoRuns(artifact));
    }

    // 업로드 예약: uploadMode()에 따라 S3 presigned PUT 또는 로컬 폴백 URL을 발급하고
    // 이미지를 PENDING 상태로 저장한다. 실제 완료 처리는 completeImage가 담당.
    public synchronized PresignImageResponse presignImage(
            String artifactId,
            PresignImageRequest request
    ) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        Instant now = Instant.now();
        UUID imageId = UUID.randomUUID();
        String sha256 = request.sha256().toLowerCase(Locale.ROOT);
        String uploadMode = uploadMode();
        String uploadUrl;
        Map<String, String> requiredHeaders;
        String objectKey = null;
        if ("SIGNED_PUT".equals(uploadMode) && imageStorage.isPresent()) {
            VcaImageStorage.PresignedUpload upload = imageStorage.get()
                    .presignUpload(artifactId, imageId.toString(), request, now.plus(15, ChronoUnit.MINUTES));
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
        int displayOrder = imageStore.findByArtifactId(artifact.getId()).size();
        UploadedImage image = UploadedImage.builder()
                .id(imageId)
                .artifactId(artifact.getId())
                .filename(request.fileName())
                .objectKey(objectKey)
                .mediaType(request.contentType())
                .contentSha256(sha256)
                .sizeBytes(request.sizeBytes())
                .status("PENDING")
                .displayOrder(displayOrder)
                .createdAt(now)
                .uploadMode(uploadMode)
                .build();
        imageStore.save(image);
        artifact.setUpdatedAt(toEntityTimestamp(now));
        artifactStore.save(artifact);

        Instant expiresAt = now.plus(15, ChronoUnit.MINUTES);
        log.info("VCA image upload reserved artifactId={} imageId={}", artifactId, imageId);
        return new PresignImageResponse(
                imageId.toString(),
                "PENDING",
                image.getUploadMode(),
                uploadUrl,
                "PUT",
                requiredHeaders,
                expiresAt
        );
    }

    // presign 없이 바로 저장하는 업로드 경로. imageStorage(S3)가 있으면 그쪽을, 없으면
    // sharedStorage(로컬)를 사용해 즉시 UPLOADED 상태로 저장한다.
    public synchronized ImageResponse uploadImage(String artifactId, MultipartFile file) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        UUID imageId = UUID.randomUUID();
        Instant now = Instant.now();
        int displayOrder = imageStore.findByArtifactId(artifact.getId()).size();
        UploadedImage image;
        if (imageStorage.isPresent()) {
            VcaImageStorage.StoredImage storedImage = imageStorage.get()
                    .storeUpload(artifactId, imageId.toString(), file);
            image = UploadedImage.builder()
                    .id(imageId)
                    .artifactId(artifact.getId())
                    .filename(storedImage.fileName())
                    .objectKey(storedImage.objectKey())
                    .mediaType(storedImage.contentType())
                    .contentSha256(storedImage.sha256())
                    .sizeBytes(storedImage.sizeBytes())
                    .status("UPLOADED")
                    .displayOrder(displayOrder)
                    .createdAt(now)
                    .uploadedAt(now)
                    .uploadMode("DIRECT_UPLOAD")
                    .build();
        } else {
            VcaSharedStorage.StoredImage storedImage = requireSharedStorage().storeUpload(imageId.toString(), file);
            image = UploadedImage.builder()
                    .id(imageId)
                    .artifactId(artifact.getId())
                    .filename(storedImage.fileName())
                    .localPath(storedImage.localPath().toString())
                    .mediaType(storedImage.contentType())
                    .contentSha256(storedImage.sha256())
                    .sizeBytes(storedImage.sizeBytes())
                    .status("UPLOADED")
                    .displayOrder(displayOrder)
                    .createdAt(now)
                    .uploadedAt(now)
                    .uploadMode("DIRECT_UPLOAD")
                    .build();
        }
        imageStore.save(image);
        artifact.setUpdatedAt(toEntityTimestamp(now));
        artifactStore.save(artifact);
        log.info("VCA image uploaded through Spring artifactId={} imageId={}", artifactId, imageId);
        return toImage(artifact, image);
    }

    // presignImage로 예약된 업로드를 UPLOADED로 확정. sha256이 일치하고, SIGNED_PUT이면
    // S3 쪽 크기/체크섬까지 재검증된 경우에만 완료 처리한다.
    public synchronized ImageResponse completeImage(
            String artifactId,
            String imageId,
            CompleteImageRequest request
    ) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        UUID id = validateUuid(imageId, "imageId");
        UploadedImage image = requireImage(artifact, id);
        String suppliedSha256 = request.sha256().toLowerCase(Locale.ROOT);
        if (!image.getContentSha256().equals(suppliedSha256)) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "SHA256_MISMATCH",
                    "The completed upload checksum does not match the reserved image."
            );
        }
        if ("SIGNED_PUT".equals(image.getUploadMode()) && image.getObjectKey() != null && imageStorage.isPresent()) {
            imageStorage.get().verifyUpload(image.getObjectKey(), image.getSizeBytes(), suppliedSha256);
        } else if (!"DIRECT_COMPLETE".equals(image.getUploadMode())) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "UPLOAD_NOT_VERIFIED",
                    "The upload object must be verified before completion."
            );
        }
        if (!"UPLOADED".equals(image.getStatus())) {
            Instant now = Instant.now();
            image.setStatus("UPLOADED");
            image.setUploadedAt(now);
            imageStore.save(image);
            artifact.setUpdatedAt(toEntityTimestamp(now));
            artifactStore.save(artifact);
            log.info("VCA image upload completed artifactId={} imageId={}", artifactId, imageId);
        }
        return toImage(artifact, image);
    }

    // 이미지 메타데이터와 실제 저장된 바이트(S3 객체 또는 로컬 파일)를 함께 삭제한다.
    public synchronized void deleteImage(String artifactId, String imageId) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        UUID id = validateUuid(imageId, "imageId");
        UploadedImage removed = imageStore.findById(id)
                .filter(candidate -> candidate.getArtifactId().equals(artifact.getId()))
                .orElseThrow(() -> new VcaApiException(
                        HttpStatus.NOT_FOUND,
                        "IMAGE_NOT_FOUND",
                        "The requested VCA image was not found."
                ));
        requireImageNotReferencedByAnyRun(artifact, id);
        imageStore.deleteById(id);
        if (removed.getObjectKey() != null && imageStorage.isPresent()) {
            imageStorage.get().delete(removed.getObjectKey());
        } else if (removed.getLocalPath() != null) {
            requireSharedStorage().deleteUpload(removed.getId().toString(), Path.of(removed.getLocalPath()));
        }
        artifact.setUpdatedAt(toEntityTimestamp(Instant.now()));
        artifactStore.save(artifact);
        log.info("VCA image metadata deleted artifactId={} imageId={}", artifactId, imageId);
    }

    // deleteImage에서만 호출된다. Postgres 이전에는 RunState.uploadedImages가
    // run 생성 시점에 캡처한 객체 참조를 그대로 들고 있어 이미지 삭제와 무관했지만,
    // 지금은 run.uploadedImageIds가 UUID 문자열만 들고 있고 preparePotteryInspection/
    // ensureReportReady가 그 UUID로 imageStore를 다시 조회한다 - 삭제 시점에
    // 참조 여부를 막지 않으면 이미 생성된 run의 리포트/도자기 검사 생성이
    // 나중에 IMAGE_NOT_FOUND로 깨진다.
    private void requireImageNotReferencedByAnyRun(VcaArtifactEntity artifact, UUID imageId) {
        String imageIdText = imageId.toString();
        boolean referenced = runStore.findByArtifactId(artifact.getId()).stream()
                .anyMatch(run -> run.getUploadedImageIds().contains(imageIdText));
        if (referenced) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "IMAGE_REFERENCED_BY_RUN",
                    "The requested VCA image is referenced by an existing assessment run and cannot be deleted."
            );
        }
    }

    public RunResponse createRun(String artifactId) {
        return createRun(artifactId, null);
    }

    // 새 assessment run 생성. reserveRun(예약)과 AI 호출을 분리해두어, AI 호출이 실패하면
    // 예약을 롤백(cancelRunReservation)할 수 있게 한 2단계 흐름이다. request.resume()이
    // true일 때만 이어가기를 시도한다("이어서 분석 시작"/"새로 분석 시작" 두 버튼 중
    // 사용자가 명시적으로 고른 쪽 - 자동으로 결정하지 않는다).
    public RunResponse createRun(String artifactId, CreateRunRequest request) {
        boolean resumeRequested = request != null && Boolean.TRUE.equals(request.resume());
        RunReservation reservation = reserveRun(artifactId, resumeRequested);
        VcaAiAssessmentRun aiRun;
        try {
            aiRun = createAiAssessmentRun(
                    artifactId,
                    reservation.run().getId().toString(),
                    reservation.uploadedImages(),
                    reservation.resumeFromProjectName()
            );
        } catch (RuntimeException exception) {
            cancelRunReservation(reservation.run().getId());
            throw exception;
        }
        return completeRunReservation(artifactId, reservation, aiRun, request == null ? null : request.material());
    }

    // 활성 run 중복 여부와 업로드 완료된 이미지 존재 여부를 확인한 뒤 QUEUED 상태로 run을 만든다.
    // createRun의 첫 단계 - 아직 vca-ai에는 아무 것도 요청하지 않은 상태.
    private synchronized RunReservation reserveRun(String artifactId, boolean resumeRequested) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        List<AssessmentRun> existingRuns = runStore.findByArtifactId(artifact.getId());
        boolean activeRunExists = existingRuns.stream()
                .anyMatch(run -> "QUEUED".equals(run.getStatus()) || "RUNNING".equals(run.getStatus()));
        if (activeRunExists) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "ACTIVE_RUN_EXISTS",
                    "An assessment run is already queued or running for this artifact."
            );
        }
        List<UploadedImage> uploadedImages = imageStore.findByArtifactId(artifact.getId()).stream()
                .filter(image -> "UPLOADED".equals(image.getStatus()))
                .toList();
        if (uploadedImages.isEmpty()) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "NOT_READY",
                    "At least one uploaded image is required to create an assessment run."
            );
        }
        String resumeFromProjectName = null;
        if (resumeRequested) {
            AssessmentRun resumable = resumableFailedRun(existingRuns, uploadedImages);
            resumeFromProjectName = resumable == null ? null : resumable.getLegacyProjectName();
        }

        Instant now = Instant.now();
        UUID assessmentRunId = UUID.randomUUID();
        int runNumber = existingRuns.size() + 1;
        AssessmentRun run = AssessmentRun.builder()
                .id(assessmentRunId)
                .artifactId(artifact.getId())
                .runNumber(runNumber)
                .legacyProjectName(projectName(artifactId, assessmentRunId.toString()))
                .status("QUEUED")
                .dryRun(false)
                .imageCount(uploadedImages.size())
                .startedAt(now)
                .uploadedImageIds(uploadedImages.stream().map(image -> image.getId().toString()).toList())
                .stages(List.of())
                .build();
        // runNumber is only serialized by this JVM's synchronized lock, which
        // doesn't span multiple app instances - the (artifact_id, run_number)
        // DB unique constraint is the real backstop under concurrent instances.
        // Without this catch, losing that race surfaced as an uncaught 500
        // instead of the same graceful conflict a losing activeRunExists
        // check above already returns.
        try {
            runStore.save(run);
        } catch (DataIntegrityViolationException exception) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "ACTIVE_RUN_EXISTS",
                    "An assessment run is already queued or running for this artifact."
            );
        }
        artifact.setUpdatedAt(toEntityTimestamp(now));
        artifactStore.save(artifact);
        log.info("VCA assessment run reserved artifactId={} assessmentRunId={} imageCount={}",
                artifactId, assessmentRunId, uploadedImages.size());
        return new RunReservation(run, uploadedImages, resumeFromProjectName);
    }

    // 같은 artifact의 가장 최근 run이 FAILED이고, 이번에 쓸 이미지 집합과 정확히 같은
    // 이미지로 실행됐다면 그 run을 돌려준다 - reserveRun(resume=true일 때 실제로
    // 이어가기 적용)과 toDetail(FE에 "이어서 시작" 버튼을 보여줄지 여부)이 공유한다.
    // 조건이 하나라도 안 맞으면(가장 최근 run이 없음/실패 아님/이미지 구성이 다름)
    // null을 돌려준다.
    private AssessmentRun resumableFailedRun(
            List<AssessmentRun> existingRuns,
            List<UploadedImage> uploadedImages
    ) {
        AssessmentRun mostRecentRun = existingRuns.stream()
                .max(Comparator.comparingInt(AssessmentRun::getRunNumber))
                .orElse(null);
        if (mostRecentRun == null || !"FAILED".equals(mostRecentRun.getStatus())) {
            return null;
        }
        Set<String> currentImageIds = uploadedImages.stream()
                .map(image -> image.getId().toString())
                .collect(Collectors.toSet());
        Set<String> priorImageIds = Set.copyOf(mostRecentRun.getUploadedImageIds());
        if (!currentImageIds.equals(priorImageIds)) {
            return null;
        }
        return mostRecentRun;
    }

    // createRun의 두 번째 단계: vca-ai 호출 결과(또는 데모 모드 가짜 리포트)를 run에 반영하고
    // material/pottery 초기 상태를 설정한 뒤 저장한다.
    private synchronized RunResponse completeRunReservation(
            String artifactId,
            RunReservation reservation,
            VcaAiAssessmentRun aiRun,
            String material
    ) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        AssessmentRun run = requireRun(artifact, reservation.run().getId().toString());
        ReportResponse report = null;
        if (vcaAiGateway.isEmpty()) {
            List<ReportResponse.Image> reportImages = reservation.uploadedImages().stream()
                    .map(image -> new ReportResponse.Image(
                            image.getId().toString(),
                            image.getFilename(),
                            fileGatewayUrl(artifactId, image.getContentSha256())
                    ))
                    .toList();
            report = VcaDemoReportFactory.create(
                    artifactId,
                    run.getId().toString(),
                    run.getStartedAt(),
                    reportImages
            );
        }
        Instant now = Instant.now();
        run.setAiRunId(aiRun.runId());
        applyAiStatus(run, aiRun);
        run.setMaterial(material);
        run.setPotteryInspectionStatus(initialPotteryInspectionStatus(material));
        runStore.save(run);
        if (report != null) {
            saveReport(run, report);
        }
        artifact.setUpdatedAt(toEntityTimestamp(now));
        artifactStore.save(artifact);
        log.info("VCA assessment queued artifactId={} assessmentRunId={} imageCount={}",
                artifactId, run.getId(), reservation.uploadedImages().size());
        return toRun(artifact, run);
    }

    // FE "분석 중지" 버튼에서 호출. 진행 중인 run만 실제로 취소하고, 이미 끝난 run은 아래에서 no-op 처리.
    public synchronized RunResponse cancelRun(String artifactId, String assessmentRunId) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        AssessmentRun run = requireRun(artifact, assessmentRunId);
        if (!"QUEUED".equals(run.getStatus()) && !"RUNNING".equals(run.getStatus())) {
            // 이미 종료된 상태(또는 중복/뒤늦은 중지 클릭)라면 에러가 아니라
            // 현재 상태를 그대로 보여주는 no-op으로 처리한다.
            return toRun(artifact, run);
        }
        Instant now = Instant.now();
        if (vcaAiGateway.isPresent() && run.getAiRunId() != null) {
            VcaAiAssessmentRun aiStatus = vcaAiGateway.get().cancelAssessmentRun(run.getAiRunId());
            applyAiStatus(run, aiStatus);
        } else {
            run.setStatus("FAILED");
            run.setFailureReason("사용자가 분석을 중지했습니다.");
            run.setCompletedAt(now);
        }
        runStore.save(run);
        artifact.setUpdatedAt(toEntityTimestamp(now));
        artifactStore.save(artifact);
        log.info("VCA assessment run cancelled artifactId={} assessmentRunId={}", artifactId, assessmentRunId);
        return toRun(artifact, run);
    }

    // createRun은 reserveRun(QUEUED row 커밋)과 createAiAssessmentRun(vca-ai 호출)을
    // 분리한 2단계 흐름이다 - 정상적인 RuntimeException은 createRun의 catch가
    // cancelRunReservation으로 이미 정리하지만, 그 사이에 프로세스가 통째로
    // 죽으면(강제 종료, OOM kill 등) catch가 실행될 기회 자체가 없어 QUEUED row가
    // 영원히 남는다. completeRunReservation이 aiRunId를 채우기 전까지는 상태가
    // 확정되지 않으므로, "QUEUED/RUNNING인데 aiRunId가 null"인 조합은 이
    // 크래시 상황에서만 나올 수 있는 신호다 - 오탐 없이 앱 기동 시점에 자동
    // 정리한다. 방치하면 activeRunExists 검사에 걸려 해당 아티팩트의 모든
    // 후속 run 생성이 영구히 막힌다.
    @EventListener(ApplicationReadyEvent.class)
    public synchronized void reconcileStuckRunsOnStartup() {
        Instant now = Instant.now();
        List<AssessmentRun> stuckRuns = runStore.findAll().stream()
                .filter(run -> ("QUEUED".equals(run.getStatus()) || "RUNNING".equals(run.getStatus()))
                        && run.getAiRunId() == null)
                .toList();
        for (AssessmentRun run : stuckRuns) {
            run.setStatus("FAILED");
            run.setFailureReason("서버 재시작으로 run 예약이 완료되지 못해 자동 종료되었습니다.");
            run.setCompletedAt(now);
            runStore.save(run);
            artifactStore.findById(run.getArtifactId()).ifPresent(artifact -> {
                artifact.setUpdatedAt(toEntityTimestamp(now));
                artifactStore.save(artifact);
            });
            log.info("VCA stuck run reconciled on startup artifactId={} assessmentRunId={}",
                    run.getArtifactId(), run.getId());
        }
    }

    // "도자기 검사" 수동 트리거 엔드포인트의 실제 로직. material 게이팅 결과에 따라
    // 도자기 AI 호출/미적용/실패 세 갈래로 나뉜다(아래 3개의 private 메서드가 각각 담당).
    public ReportResponse runPotteryInspection(
            String artifactId,
            String assessmentRunId,
            PotteryInspectionRequest request
    ) {
        PotteryInspectionTarget target = preparePotteryInspection(artifactId, assessmentRunId, request);
        if (target.alreadyHandled()) {
            // VCA 리포트가 이번 호출에서 막 COMPLETED로 전환되면서 도자기
            // 검사가 이미 자동으로 실행됐다 - 방금 반영된 리포트를 그대로
            // 돌려주고, 실제 검사를 한 번 더 중복 실행하지 않는다.
            return findReport(UUID.fromString(assessmentRunId));
        }
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

    // 도자기 검사 실행 전제조건 확인: 리포트가 완료되어 있어야 하고, material이 도자기 계열이면서
    // potteryInspectionAiClient 빈이 존재할 때만 applicable=true로 판단한다.
    private synchronized PotteryInspectionTarget preparePotteryInspection(
            String artifactId,
            String assessmentRunId,
            PotteryInspectionRequest request
    ) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        AssessmentRun run = requireRun(artifact, assessmentRunId);
        // 이 동기화가 방금 COMPLETED로 전환시킨 거라면, 그 안에서 이미 도자기
        // 검사를 자동으로 실행해뒀다(syncRunWithAi 참고) - 아래에서 또
        // 중복 실행하지 않도록 alreadyHandled로 표시해 돌려준다.
        boolean alreadyHandled = syncRunWithAi(artifact, run);
        if (!"COMPLETED".equals(run.getStatus())) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "NOT_READY",
                    "The assessment report must be completed before pottery inspection."
            );
        }
        ensureReportReady(artifact, run);
        String material = potteryMaterial(request, run);
        boolean applicable = isPotteryMaterial(material) && potteryInspectionAiClient.isPresent();
        UploadedImage primaryImage = requireImage(artifact, UUID.fromString(run.getUploadedImageIds().get(0)));
        runStore.save(run);
        return new PotteryInspectionTarget(applicable, primaryImage, alreadyHandled);
    }

    // runPotteryInspection의 세 가지 결과 처리 중 하나: 도자기 재질이 아니거나 클라이언트가
    // 없어 검사 자체를 건너뛴 경우, 리포트에 "미적용" 상태만 반영한다.
    private synchronized ReportResponse markPotteryInspectionNotApplicable(
            String artifactId,
            String assessmentRunId
    ) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        AssessmentRun run = requireRun(artifact, assessmentRunId);
        run.setPotteryInspectionStatus(new ReportResponse.PotteryInspectionStatus(
                false,
                POTTERY_STATUS_NOT_STARTED,
                false,
                null,
                null
        ));
        runStore.save(run);
        ReportResponse report = withPotteryInspectionState(findReport(run.getId()), run);
        saveReport(run, report);
        artifact.setUpdatedAt(toEntityTimestamp(Instant.now()));
        artifactStore.save(artifact);
        return report;
    }

    // 도자기 검사가 성공적으로 끝났을 때 결과와 상태를 run/리포트에 반영.
    private synchronized ReportResponse completePotteryInspection(
            String artifactId,
            String assessmentRunId,
            ReportResponse.PotteryInspection potteryInspection
    ) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        AssessmentRun run = requireRun(artifact, assessmentRunId);
        savePotteryResult(run.getId(), potteryInspection);
        run.setPotteryInspectionStatus(new ReportResponse.PotteryInspectionStatus(
                true,
                POTTERY_STATUS_COMPLETED,
                true,
                null,
                Instant.now()
        ));
        runStore.save(run);
        ReportResponse report = withPotteryInspectionState(findReport(run.getId()), run);
        saveReport(run, report);
        artifact.setUpdatedAt(toEntityTimestamp(Instant.now()));
        artifactStore.save(artifact);
        return report;
    }

    // 도자기 AI 호출이 예외를 던졌을 때 실패 사유를 상태에 남기고, 재시도 가능(retryable=true)함을 알린다.
    private synchronized ReportResponse failPotteryInspection(
            String artifactId,
            String assessmentRunId,
            RuntimeException exception
    ) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        AssessmentRun run = requireRun(artifact, assessmentRunId);
        run.setPotteryInspectionStatus(new ReportResponse.PotteryInspectionStatus(
                true,
                POTTERY_STATUS_FAILED,
                true,
                exception.getMessage(),
                Instant.now()
        ));
        runStore.save(run);
        ReportResponse report = withPotteryInspectionState(findReport(run.getId()), run);
        saveReport(run, report);
        artifact.setUpdatedAt(toEntityTimestamp(Instant.now()));
        artifactStore.save(artifact);
        log.warn("VCA pottery inspection failed artifactId={} assessmentRunId={}", artifactId, assessmentRunId, exception);
        return report;
    }

    // createRun에서 vca-ai 호출이 실패했을 때 reserveRun이 만든 run을 되돌린다.
    // aiRunId가 이미 설정된 뒤라면(즉 vca-ai가 실제로 run을 받아들인 뒤라면) 삭제하지 않는다.
    private synchronized void cancelRunReservation(UUID assessmentRunId) {
        runStore.findById(assessmentRunId).ifPresent(run -> {
            if (run.getAiRunId() == null) {
                runStore.deleteById(assessmentRunId);
            }
        });
    }

    // 리포트 조회. AI 모드에서는 vca-ai에서 매번 최신 리포트를 받아와 저장하고,
    // 데모 모드에서는 최초 조회 시점에 COMPLETED로 전환하며 캐시된 리포트를 반환한다.
    public synchronized ReportResponse getReport(
            String artifactId,
            String assessmentRunId
    ) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        AssessmentRun run = requireRun(artifact, assessmentRunId);
        syncRunWithAi(artifact, run);
        if (vcaAiGateway.isPresent()) {
            if (!"COMPLETED".equals(run.getStatus())) {
                throw new VcaApiException(
                        HttpStatus.CONFLICT,
                        "NOT_READY",
                        "The assessment report is not ready."
                );
            }
            VcaAiAssessmentReport aiReport = vcaAiGateway.get().getAssessmentReport(run.getAiRunId());
            ReportResponse report = toReport(artifact, run, aiReport);
            saveReport(run, report);
            return report;
        }
        if (!"COMPLETED".equals(run.getStatus())) {
            Instant now = Instant.now();
            run.setStatus("COMPLETED");
            run.setCompletedAt(now);
            runStore.save(run);
            artifact.setUpdatedAt(toEntityTimestamp(now));
            artifactStore.save(artifact);
            log.info("VCA demo assessment completed artifactId={} assessmentRunId={}",
                    artifactId, assessmentRunId);
        }
        return findReport(run.getId());
    }

    // 조사 보고서 하단 "시스템 환경 정보" 조회 - 특정 run이 아니라 vca-ai
    // 엔진이 지금 도는 환경 자체를 설명하는 표시 전용 정보라, 게이트웨이가
    // 없거나(데모 모드) 호출이 실패해도 예외 대신 알 수 없음 값으로 채운
    // 응답을 돌려준다(리포트 페이지 전체를 깨뜨릴 이유가 없다).
    public SystemInfoResponse getSystemInfo() {
        if (vcaAiGateway.isEmpty()) {
            return new SystemInfoResponse(
                    "데모 모드 (VCA AI 게이트웨이 미설정)", "-", "-", Map.of(), List.of());
        }
        try {
            VcaAiSystemInfo info = vcaAiGateway.get().getSystemInfo();
            return new SystemInfoResponse(
                    info.os(),
                    info.pythonVersion(),
                    info.device(),
                    info.libraries() == null ? Map.of() : info.libraries(),
                    info.models() == null
                            ? List.of()
                            : info.models().stream()
                                    .map(model -> new SystemInfoResponse.Model(
                                            model.key(), model.repoId(), model.revision()))
                                    .toList()
            );
        } catch (RuntimeException exception) {
            log.warn("VCA system info lookup failed", exception);
            return new SystemInfoResponse("알 수 없음", "알 수 없음", "알 수 없음", Map.of(), List.of());
        }
    }

    // 파이프라인 단계별 중간 산출물 조회(디버깅용). intermediateResultStorage가 없으면 빈 목록.
    public synchronized IntermediateResultsResponse getIntermediateResults(
            String artifactId,
            String assessmentRunId
    ) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        AssessmentRun run = requireRun(artifact, assessmentRunId);
        String runProjectName = projectName(artifactId, run.getId().toString());
        return intermediateResultStorage
                .map(storage -> storage.read(artifactId, run.getId().toString(), runProjectName))
                .orElseGet(() -> new IntermediateResultsResponse(
                        artifactId,
                        run.getId().toString(),
                        runProjectName,
                        List.of()
                ));
    }

    // sha256으로 업로드된 이미지를 찾아 실제 다운로드 위치(S3 presigned 또는 로컬 서명 URL)를 반환.
    // VcaController.getFile이 이 URI로 303 리다이렉트한다.
    public synchronized URI getFileDownloadLocation(String artifactId, String sha256) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        String normalizedSha256 = validateSha256(sha256);
        UploadedImage image = imageStore.findByArtifactId(artifact.getId()).stream()
                .filter(candidate -> normalizedSha256.equals(candidate.getContentSha256())
                        && "UPLOADED".equals(candidate.getStatus()))
                .findFirst()
                .orElseThrow(() -> new VcaApiException(
                        HttpStatus.NOT_FOUND,
                        "FILE_NOT_FOUND",
                        "The requested VCA file was not found."
                ));
        if (image.getObjectKey() != null && imageStorage.isPresent()) {
            return imageStorage.get().presignedDownload(image.getObjectKey());
        }
        Instant expiresAt = Instant.now().plus(5, ChronoUnit.MINUTES);
        return URI.create(LOCAL_ORIGIN + "/files/" + normalizedSha256
                + "?expires=" + expiresAt.getEpochSecond()
                + "&signature=" + UUID.randomUUID().toString().replace("-", ""));
    }

    // 리포트 PDF 생성 job 큐잉 + 렌더링. 같은 run에 이미 job이 있으면(성공/실패 무관)
    // 기존 것을 그대로 재사용한다 - 재생성 경로는 아직 없다. object storage가
    // 설정돼 있으면(실제 배포/로컬 MinIO 환경) 렌더링을 큐잉 즉시 동기적으로
    // 수행한다(GPU 추론 없이 몇 초면 끝나는 가벼운 작업이라 폴링을 별도로 둘
    // 필요가 없다). storage가 없는 데모 모드는 이미지 다운로드(getFileDownloadLocation)
    // 처럼 실제 렌더링 없이 QUEUED로 남기고 getPdfJob 폴링에서 COMPLETED로
    // 전환되는 예전 스텁 동작을 그대로 유지한다.
    public synchronized PdfJobResponse createPdfJob(
            String artifactId,
            String assessmentRunId
    ) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        AssessmentRun run = requireRun(artifact, assessmentRunId);
        syncRunWithAi(artifact, run);
        if (!"COMPLETED".equals(run.getStatus())) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "NOT_READY",
                    "The assessment report must be completed before creating a PDF."
            );
        }
        Optional<ReportPdfJob> existing = pdfJobStore.findByAssessmentRunId(run.getId());
        if (existing.isPresent()) {
            return toPdfJob(artifact, existing.get());
        }

        ReportPdfJob job = ReportPdfJob.builder()
                .id(UUID.randomUUID())
                .assessmentRunId(run.getId())
                .status(imageStorage.isPresent() ? "RUNNING" : "QUEUED")
                .requestedAt(Instant.now())
                .build();
        if (imageStorage.isPresent()) {
            renderAndStorePdf(artifact, run, job);
        }
        pdfJobStore.save(job);
        log.info("VCA report PDF {} assessmentRunId={} jobId={}", job.getStatus(), assessmentRunId, job.getId());
        return toPdfJob(artifact, job);
    }

    // createPdfJob에서 호출: 리포트+업로드 이미지 원본 바이트를 모아
    // VcaReportPdfRenderer로 실제 PDF를 그리고, object storage에 올린 뒤
    // job을 COMPLETED(pdfObjectKey 채움)로 만든다. 렌더링/저장 중 무엇이든
    // 실패하면 FAILED로 남기고 로그에 원인을 남긴다 - job 엔티티(팀 공유 ERD)에
    // 실패 사유 컬럼이 없어 응답에는 상태만 실린다.
    private void renderAndStorePdf(VcaArtifactEntity artifact, AssessmentRun run, ReportPdfJob job) {
        try {
            ensureReportReady(artifact, run);
            ReportResponse report = findReport(run.getId());
            if (report == null) {
                throw new IllegalStateException("Report is unexpectedly missing after ensureReportReady.");
            }
            List<VcaReportPdfRenderer.PdfImage> images = run.getUploadedImageIds().stream()
                    .map(id -> requireImage(artifact, UUID.fromString(id)))
                    .map(image -> new VcaReportPdfRenderer.PdfImage(
                            image.getId().toString(), image.getFilename(), imageBytes(image)
                    ))
                    .toList();
            byte[] pdfBytes = pdfRenderer.render(new VcaReportPdfRenderer.PdfRenderInput(
                    artifact.getName(),
                    artifact.getId().toString(),
                    run.getMaterial(),
                    run.getImageCount(),
                    run.getCompletedAt(),
                    images,
                    report.summary(),
                    report.findings(),
                    report.potteryInspection()
            ));
            String objectKey = "report-pdfs/" + job.getId() + ".pdf";
            imageStorage.orElseThrow(() -> new VcaApiException(
                    HttpStatus.CONFLICT,
                    "VCA_STORAGE_UNAVAILABLE",
                    "VCA object storage is not configured for report PDFs."
            )).storeBytes(objectKey, pdfBytes, "application/pdf");
            job.setPdfObjectKey(objectKey);
            job.setStatus("COMPLETED");
        } catch (RuntimeException exception) {
            log.warn("VCA report PDF rendering failed assessmentRunId={} jobId={}", run.getId(), job.getId(), exception);
            job.setStatus("FAILED");
        } finally {
            job.setCompletedAt(Instant.now());
        }
    }

    // job 상태 조회. object storage가 있는 실제 환경은 렌더링이 createPdfJob에서
    // 이미 동기적으로 끝나 있으므로 저장된 상태를 그대로 돌려준다. storage가 없는
    // 데모 모드 job(QUEUED로 남겨둔 것)만 예전처럼 폴링 시점에 COMPLETED로 전환한다.
    public synchronized PdfJobResponse getPdfJob(String artifactId, String jobId) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        ReportPdfJob job = requirePdfJob(artifact, jobId);
        if (imageStorage.isEmpty() && ("QUEUED".equals(job.getStatus()) || "RUNNING".equals(job.getStatus()))) {
            job.setStatus("COMPLETED");
            job.setCompletedAt(Instant.now());
            pdfJobStore.save(job);
            log.info("VCA demo report PDF completed jobId={}", jobId);
        }
        return toPdfJob(artifact, job);
    }

    // 완료된 PDF job의 다운로드 위치를 발급한다. pdfObjectKey가 있으면(실제 렌더링된
    // 경우) object storage의 진짜 presigned URL을, 없으면(storage 없는 데모 모드
    // 스텁 job) 예전처럼 서명된 로컬 URL만 흉내낸다.
    public synchronized URI getPdfDownloadLocation(String artifactId, String jobId) {
        VcaArtifactEntity artifact = requireArtifact(artifactId);
        ReportPdfJob job = requirePdfJob(artifact, jobId);
        if (!"COMPLETED".equals(job.getStatus())) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "PDF_NOT_READY",
                    "The report PDF is not ready for download."
            );
        }
        if (job.getPdfObjectKey() != null && imageStorage.isPresent()) {
            return imageStorage.get().presignedDownload(job.getPdfObjectKey());
        }
        Instant expiresAt = Instant.now().plus(5, ChronoUnit.MINUTES);
        return URI.create(LOCAL_ORIGIN + "/downloads/" + job.getId() + ".pdf"
                + "?expires=" + expiresAt.getEpochSecond()
                + "&signature=" + UUID.randomUUID().toString().replace("-", ""));
    }

    // 엔티티 -> ArtifactSummary(목록 카드용 DTO) 변환. getArtifacts에서 사용. runs는
    // 호출자가 advanceDemoRuns로 이미 조회/갱신해둔 목록을 그대로 받는다 - 같은
    // 아티팩트의 run을 두 번 조회하지 않기 위함이다.
    private ArtifactSummary toSummary(VcaArtifactEntity artifact, List<AssessmentRun> runs) {
        AssessmentRun latestRun = runs.stream()
                .max(Comparator.comparing(AssessmentRun::getStartedAt))
                .orElse(null);
        return new ArtifactSummary(
                artifact.getId().toString(),
                artifact.getName(),
                firstUploadedImageUrl(artifact),
                latestRun == null ? null : toRun(artifact, latestRun),
                runs.size(),
                toApiTimestamp(artifact.getUpdatedAt())
        );
    }

    // 엔티티 -> ArtifactDetailResponse 변환. artifactStatus로 종합 상태를 계산해 함께 담는다.
    // runs는 toSummary와 같은 이유로 호출자가 넘겨준다.
    private ArtifactDetailResponse toDetail(VcaArtifactEntity artifact, List<AssessmentRun> runs) {
        List<UploadedImage> images = imageStore.findByArtifactId(artifact.getId());
        List<UploadedImage> uploadedImages = images.stream()
                .filter(image -> "UPLOADED".equals(image.getStatus()))
                .toList();
        AssessmentRun resumable = resumableFailedRun(runs, uploadedImages);
        return new ArtifactDetailResponse(
                artifact.getId().toString(),
                artifact.getName(),
                artifactStatus(runs, images),
                uploadedImages.stream()
                        .sorted(Comparator.comparingInt(UploadedImage::getDisplayOrder))
                        .map(image -> toImage(artifact, image))
                        .toList(),
                runs.stream()
                        .sorted(Comparator.comparing(AssessmentRun::getStartedAt))
                        .map(run -> toRun(artifact, run))
                        .toList(),
                resumable == null ? null : resumable.getId().toString(),
                toApiTimestamp(artifact.getCreatedAt()),
                toApiTimestamp(artifact.getUpdatedAt())
        );
    }

    // 엔티티 -> ImageResponse 변환. UPLOADED 상태일 때만 downloadUrl을 채운다.
    private ImageResponse toImage(VcaArtifactEntity artifact, UploadedImage image) {
        String downloadUrl = "UPLOADED".equals(image.getStatus())
                ? fileGatewayUrl(artifact.getId().toString(), image.getContentSha256())
                : null;
        return new ImageResponse(
                image.getId().toString(),
                image.getFilename(),
                image.getMediaType(),
                image.getSizeBytes(),
                image.getStatus(),
                downloadUrl,
                image.getCreatedAt(),
                image.getUploadedAt()
        );
    }

    // 엔티티 -> RunResponse 변환. reportUrl은 항상 이 run의 report 엔드포인트로 고정 구성된다.
    private RunResponse toRun(VcaArtifactEntity artifact, AssessmentRun run) {
        return new RunResponse(
                run.getId().toString(),
                artifact.getId().toString(),
                run.getStatus(),
                run.getImageCount(),
                run.getStartedAt(),
                run.getCompletedAt(),
                "/api/vca/" + artifact.getId().toString() + "/runs/"
                        + run.getId() + "/report",
                run.getCurrentStage(),
                run.getProgressPercent(),
                run.getStages() == null ? List.of() : run.getStages(),
                run.getFailureReason()
        );
    }

    // 엔티티 -> PdfJobResponse 변환. COMPLETED일 때만 downloadUrl을 채운다.
    private PdfJobResponse toPdfJob(VcaArtifactEntity artifact, ReportPdfJob job) {
        String downloadUrl = "COMPLETED".equals(job.getStatus())
                ? "/api/vca/" + artifact.getId().toString() + "/report-pdf-jobs/"
                        + job.getId() + "/download"
                : null;
        return new PdfJobResponse(
                job.getId().toString(),
                job.getAssessmentRunId().toString(),
                job.getStatus(),
                job.getRequestedAt(),
                job.getCompletedAt() == null ? job.getRequestedAt() : job.getCompletedAt(),
                downloadUrl
        );
    }

    // getArtifacts/getArtifact가 매번 호출해 진행 중인 run들의 상태를 갱신하는 폴링성 메서드.
    // AI 게이트웨이가 있으면 syncRunWithAi로 위임하고, 없으면 데모 타이머로 진행을 흉내낸다.
    // 조회한 run 목록을 그대로 반환해(엔티티는 이 메서드 안에서 이미 최신 상태로
    // mutate됨) 호출자가 toSummary/toDetail에 넘길 때 같은 아티팩트의 run을
    // 다시 조회하지 않게 한다.
    private List<AssessmentRun> advanceDemoRuns(VcaArtifactEntity artifact) {
        Instant now = Instant.now();
        boolean artifactTouched = false;
        List<AssessmentRun> runs = runStore.findByArtifactId(artifact.getId());
        for (AssessmentRun run : runs) {
            if ("COMPLETED".equals(run.getStatus()) || "FAILED".equals(run.getStatus())) {
                continue;
            }
            if (vcaAiGateway.isPresent()) {
                syncRunWithAi(artifact, run);
                continue;
            }
            // 실제 vca-ai 게이트웨이가 설정되지 않은 데모 모드에서는 경과 시간만으로
            // 단계 진행을 흉내 낸다. 아래 임계값들은 실제 단계 소요 시간과 무관한,
            // UX상 임의로 정한 값이다.
            long elapsedMillis = ChronoUnit.MILLIS.between(run.getStartedAt(), now);
            if (elapsedMillis >= 3_000) {
                run.setStatus("COMPLETED");
                run.setCompletedAt(now);
                runStore.save(run);
                artifactTouched = true;
                log.info("VCA demo assessment completed by artifact polling artifactId={} assessmentRunId={}",
                        artifact.getId().toString(), run.getId());
            } else if (elapsedMillis >= 900 && "QUEUED".equals(run.getStatus())) {
                run.setStatus("RUNNING");
                runStore.save(run);
                artifactTouched = true;
                log.info("VCA demo assessment running artifactId={} assessmentRunId={}",
                        artifact.getId().toString(), run.getId());
            }
        }
        if (artifactTouched) {
            artifact.setUpdatedAt(toEntityTimestamp(now));
            artifactStore.save(artifact);
        }
        return runs;
    }

    // vca-ai 호출 직전, 업로드된 이미지를 엔진이 읽을 입력 디렉터리로 구체화(materializeRunInput)한
    // 뒤 실제 run 생성을 요청한다. AI 게이트웨이가 없으면(데모 모드) 호출 없이 가짜 run만 만든다.
    private VcaAiAssessmentRun createAiAssessmentRun(
            String artifactId,
            String assessmentRunId,
            List<UploadedImage> uploadedImages,
            String resumeFromProjectName
    ) {
        if (vcaAiGateway.isEmpty()) {
            return new VcaAiAssessmentRun(assessmentRunId, assessmentRunId, "QUEUED");
        }
        VcaSharedStorage.RunInputDirectory inputDirectory;
        if (imageStorage.isPresent() && uploadedImages.stream().anyMatch(image -> image.getObjectKey() != null)) {
            inputDirectory = imageStorage.get().materializeRunInput(
                    assessmentRunId,
                    uploadedImages.stream()
                            .map(image -> new VcaImageStorage.StoredImageReference(
                                    image.getId().toString(),
                                    image.getFilename(),
                                    image.getObjectKey()
                            ))
                            .toList()
            );
        } else {
            inputDirectory = requireSharedStorage()
                    .materializeRunInput(
                            assessmentRunId,
                            uploadedImages.stream()
                                    .map(image -> new VcaSharedStorage.StoredImageReference(
                                            image.getId().toString(),
                                            image.getFilename(),
                                            image.getLocalPath() == null ? null : Path.of(image.getLocalPath())
                                    ))
                                    .toList()
                    );
        }
        return vcaAiGateway.get().createAssessmentRun(
                assessmentRunId,
                projectName(artifactId, assessmentRunId),
                inputDirectory.containerPath(),
                inputDirectory.remoteImages().stream()
                        .map(image -> new VcaAiGateway.InputImageUrl(image.fileName(), image.downloadUrl()))
                        .toList(),
                resumeFromProjectName
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

    // vca-ai에서 run의 최신 상태를 가져와 로컬 엔티티에 반영. getReport/createPdfJob/
    // preparePotteryInspection/advanceDemoRuns 등 여러 곳에서 공통으로 호출되는 동기화 지점.
    // 이 호출로 run이 방금 처음 COMPLETED로 전환됐다면, 도자기 재질에 한해 도자기 검사를
    // 사용자 조작 없이 곧바로 이어서 실행한다(아래 autoTriggerPotteryInspectionIfApplicable).
    // 반환값은 "이 호출 안에서 도자기 검사를 자동으로 이미 실행했는가"이다 -
    // preparePotteryInspection이 이 값을 보고, 방금 자동으로 막 끝낸 검사를
    // 수동 트리거 경로가 곧바로 또 한 번 중복 실행하지 않게 막는다.
    private boolean syncRunWithAi(VcaArtifactEntity artifact, AssessmentRun run) {
        if (vcaAiGateway.isEmpty() || run.getAiRunId() == null) {
            return false;
        }
        boolean wasCompleted = "COMPLETED".equals(run.getStatus());
        VcaAiAssessmentRun aiStatus = vcaAiGateway.get().getAssessmentStatus(run.getAiRunId());
        applyAiStatus(run, aiStatus);
        runStore.save(run);
        artifact.setUpdatedAt(toEntityTimestamp(Instant.now()));
        artifactStore.save(artifact);
        boolean justCompleted = !wasCompleted && "COMPLETED".equals(run.getStatus());
        if (justCompleted) {
            autoTriggerPotteryInspectionIfApplicable(artifact, run);
        }
        return justCompleted;
    }

    // VCA 리포트가 방금 COMPLETED로 전환된 시점에 한 번, material이 도자기 계열이면
    // 도자기 검사를 자동으로 실행한다 - "분석 시작"만 누르면 VCA가 끝나는 즉시 이어서
    // 돌아가길 원한다는 요청에 따른 것으로, FE가 별도 버튼을 누르거나 리포트 페이지를
    // 열어둘 필요가 없다(이 메서드는 폴링/조회가 들어올 때마다 공통으로 거치는
    // syncRunWithAi 안에서 호출되므로, 어느 화면이 폴링하든 트리거된다). runPotteryInspection
    // 처럼 syncRunWithAi를 다시 부르지 않는다(방금 그 호출 안에 있으므로) - 대신
    // completePotteryInspection/failPotteryInspection을 그대로 재사용해 저장 로직을
    // 수동 트리거와 하나로 유지한다. COMPLETED는 한 번만 전환되므로(재전환 없음)
    // 이 메서드도 run당 정확히 한 번만 실행된다.
    private void autoTriggerPotteryInspectionIfApplicable(
            VcaArtifactEntity artifact,
            AssessmentRun run
    ) {
        if (!isPotteryMaterial(run.getMaterial()) || potteryInspectionAiClient.isEmpty()) {
            return;
        }
        if (run.getUploadedImageIds() == null || run.getUploadedImageIds().isEmpty()) {
            return;
        }
        String artifactId = artifact.getId().toString();
        String assessmentRunId = run.getId().toString();
        ensureReportReady(artifact, run);
        UploadedImage primaryImage = requireImage(artifact, UUID.fromString(run.getUploadedImageIds().get(0)));
        try {
            ReportResponse.PotteryInspection potteryInspection = inspectPottery(primaryImage);
            completePotteryInspection(artifactId, assessmentRunId, potteryInspection);
        } catch (RuntimeException exception) {
            failPotteryInspection(artifactId, assessmentRunId, exception);
        }
    }

    // vca-ai 응답을 run 엔티티 필드에 반영하는 공통 매핑 로직(syncRunWithAi/completeRunReservation/
    // cancelRun에서 재사용). COMPLETED로 처음 전환되는 순간에만 completedAt을 채운다.
    private static void applyAiStatus(AssessmentRun run, VcaAiAssessmentRun aiStatus) {
        run.setStatus(aiStatus.status());
        run.setCurrentStage(aiStatus.currentStage());
        run.setStages(toRunStages(aiStatus.stages()));
        run.setFailureReason(aiStatus.failureReason());
        run.setProgressPercent(computeProgressPercent(aiStatus.currentStageProgress(), run.getStatus()));
        if ("COMPLETED".equals(run.getStatus()) && run.getCompletedAt() == null) {
            run.setCompletedAt(Instant.now());
        }
    }

    // 지금 실행 중인 스테이지 자체의 진행률(%)을 계산한다 - 전체 8단계 대비
    // 비율이 아니라, vca-ai가 무거운 스테이지(rough_masking, mask_refining)
    // 안에서 보고하는 completed/total만 쓴다. 그 외 스테이지는 이 값이 없으니
    // null을 돌려줘 FE가 상태 기반 대략값으로 폴백하게 한다. FE 진행바 표시용.
    private static Integer computeProgressPercent(
            VcaAiAssessmentStageProgress stageProgress, String status
    ) {
        if ("COMPLETED".equals(status)) {
            return 100;
        }
        if (stageProgress == null || stageProgress.total() == null || stageProgress.completed() == null
                || stageProgress.total() <= 0) {
            return null;
        }
        return (int) Math.round(100.0 * stageProgress.completed() / stageProgress.total());
    }

    private static List<RunResponse.Stage> toRunStages(List<VcaAiAssessmentStage> stages) {
        if (stages == null) {
            return List.of();
        }
        return stages.stream()
                .map(stage -> new RunResponse.Stage(
                        stage.name(),
                        stage.status(),
                        stage.exitCode(),
                        stage.reason()
                ))
                .toList();
    }

    // vca-ai 리포트 -> ReportResponse 변환의 메인 조립부. 이미지 목록은 AI 응답이 아니라
    // Spring이 관리하는 imageStore에서 다시 구성하고, recommendation도 findings로부터 파생시킨다.
    private ReportResponse toReport(
            VcaArtifactEntity artifact,
            AssessmentRun run,
            VcaAiAssessmentReport aiReport
    ) {
        List<ReportResponse.Image> reportImages = imageStore.findByArtifactId(artifact.getId()).stream()
                .filter(image -> "UPLOADED".equals(image.getStatus()))
                .sorted(Comparator.comparingInt(UploadedImage::getDisplayOrder))
                .map(image -> new ReportResponse.Image(
                        image.getId().toString(),
                        image.getFilename(),
                        fileGatewayUrl(artifact.getId().toString(), image.getContentSha256())
                ))
                .toList();
        List<ReportResponse.Finding> findings = toFindings(artifact, aiReport.findings());
        return new ReportResponse(
                run.getId().toString(),
                artifact.getId().toString(),
                aiReport.status(),
                Instant.now(),
                new ReportResponse.Summary(
                        REPORT_HEADLINE,
                        aiReport.summary(),
                        null,
                        null
                ),
                findings,
                toRecommendations(findings),
                reportImages,
                toRagArtifacts(aiReport.ragArtifacts()),
                findPotteryInspection(run.getId()).orElse(null),
                run.getPotteryInspectionStatus()
        );
    }

    // vca-ai 보고서에는 recommendation이 따로 없어서, Spring이 finding의
    // conceptFamily별로 정형화된 recommendation을 하나씩 만들어낸다.
    private static List<ReportResponse.Recommendation> toRecommendations(
            List<ReportResponse.Finding> findings
    ) {
        return findings.stream()
                .map(ReportResponse.Finding::conceptFamily)
                .filter(conceptFamily -> conceptFamily != null && !conceptFamily.isBlank())
                .distinct()
                .map(conceptFamily -> new ReportResponse.Recommendation(
                        "recommendation-" + conceptFamily,
                        "MEDIUM",
                        conceptFamily,
                        RECOMMENDATION_DESCRIPTION_FORMAT.formatted(conceptFamily)
                ))
                .toList();
    }

    // gateway RagArtifacts -> dto RagArtifacts 필드 단위 변환(null이면 리포트에 RAG 근거 없이 반환).
    private static ReportResponse.RagArtifacts toRagArtifacts(
            VcaAiAssessmentReport.RagArtifacts ragArtifacts
    ) {
        if (ragArtifacts == null) {
            return null;
        }
        return new ReportResponse.RagArtifacts(
                ragArtifacts.schema(),
                ragArtifacts.queryCount(),
                ragArtifacts.retrievalResultCount(),
                ragArtifacts.evidenceRowCount(),
                ragArtifacts.visualConceptCardCount(),
                ragArtifacts.queries().stream()
                        .map(query -> new ReportResponse.RagQuery(
                                query.lane(),
                                query.promptText(),
                                query.queryId()
                        ))
                        .toList(),
                ragArtifacts.retrievalResults().stream()
                        .map(result -> new ReportResponse.RagRetrievalResult(
                                result.chunkId(),
                                result.citationId(),
                                result.lane(),
                                result.matchedTerms(),
                                result.pageNumber(),
                                result.promptText(),
                                result.queryId(),
                                result.rank(),
                                result.score(),
                                result.snippetText(),
                                result.sourceCitation()
                        ))
                        .toList(),
                ragArtifacts.evidenceRows().stream()
                        .map(row -> new ReportResponse.RagEvidenceRow(
                                row.evidenceState(),
                                row.lane(),
                                row.matchedCitationIds(),
                                row.promptText(),
                                row.queryId(),
                                row.ragParentCandidateId(),
                                row.topCitationId(),
                                row.topRetrievalScore()
                        ))
                        .toList(),
                ragArtifacts.visualConceptCards().stream()
                        .map(card -> new ReportResponse.RagVisualConceptCard(
                                card.conceptCardId(),
                                card.conceptFamily(),
                                card.contextTerms(),
                                card.descriptorTerms(),
                                card.materialTerms(),
                                card.provenanceStrength(),
                                card.ragParentCandidateId(),
                                card.rawRetrievedSentence(),
                                card.retrievalScore(),
                                card.sourceCitationIds()
                        ))
                        .toList()
        );
    }

    // 도자기 검사 AI(별도 서비스, pottery_inspection_ai)를 대표 이미지 1장으로 호출.
    private ReportResponse.PotteryInspection inspectPottery(UploadedImage primaryImage) {
        PotteryInspectionResponseDto response = potteryInspectionAiClient.get().inspect(
                toMultipartFile(primaryImage),
                POTTERY_INSPECTION_CALLS,
                true
        );
        return toPotteryInspection(response);
    }

    // material 문자열에 도자기 관련 키워드가 포함되는지로 판별하는 단순 휴리스틱이다
    // (정식 재질 분류 체계가 아님 - "도자"/"pottery"/"ceramic" 부분 문자열 매칭).
    private static boolean isPotteryMaterial(String material) {
        if (material == null) {
            return false;
        }
        String normalized = material.toLowerCase(Locale.ROOT);
        return normalized.contains("도자")
                || normalized.contains("pottery")
                || normalized.contains("ceramic");
    }

    // 저장된 이미지 바이트(S3 또는 로컬)를 도자기 검사 클라이언트가 요구하는 MultipartFile
    // 형태로 재구성한다(StoredImageMultipartFile 어댑터 사용).
    private MultipartFile toMultipartFile(UploadedImage image) {
        return new StoredImageMultipartFile(image.getFilename(), image.getMediaType(), imageBytes(image));
    }

    // 저장된 이미지의 원본 바이트를 읽는다(S3 objectKey 또는 로컬 폴백 경로).
    // toMultipartFile(도자기 검사)과 renderAndStorePdf(리포트 PDF)가 공유한다.
    private byte[] imageBytes(UploadedImage image) {
        try {
            if (image.getObjectKey() != null && imageStorage.isPresent()) {
                return imageStorage.get().read(image.getObjectKey(), image.getFilename(), image.getMediaType()).bytes();
            }
            if (image.getLocalPath() != null) {
                return Files.readAllBytes(Path.of(image.getLocalPath()));
            }
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "NOT_READY",
                    "Uploaded image bytes are required."
            );
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

    // 요청 본문에 material이 지정되어 있으면 run에 저장된 기존 값을 덮어쓴다(부수효과 있음).
    // 지정하지 않으면 run 생성 시 저장했던 material을 그대로 사용.
    private static String potteryMaterial(PotteryInspectionRequest request, AssessmentRun run) {
        if (request != null && request.material() != null && !request.material().isBlank()) {
            run.setMaterial(request.material());
        }
        return run.getMaterial();
    }

    // run 생성 직후의 초기 도자기 검사 상태. 도자기 재질이 아니면 아예 null(해당 없음).
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

    // 리포트가 아직 저장되지 않았다면(reportStore에 없다면) 그 시점에 한 번 만들어 저장한다.
    // preparePotteryInspection에서 도자기 검사 전에 리포트 존재를 보장하기 위해 호출됨(멱등).
    private void ensureReportReady(VcaArtifactEntity artifact, AssessmentRun run) {
        if (reportStore.findById(run.getId()).isPresent()) {
            return;
        }
        ReportResponse report;
        if (vcaAiGateway.isPresent()) {
            VcaAiAssessmentReport aiReport = vcaAiGateway.get().getAssessmentReport(run.getAiRunId());
            report = toReport(artifact, run, aiReport);
        } else {
            List<ReportResponse.Image> reportImages = run.getUploadedImageIds().stream()
                    .map(id -> requireImage(artifact, UUID.fromString(id)))
                    .map(image -> new ReportResponse.Image(
                            image.getId().toString(),
                            image.getFilename(),
                            fileGatewayUrl(artifact.getId().toString(), image.getContentSha256())
                    ))
                    .toList();
            report = VcaDemoReportFactory.create(
                    artifact.getId().toString(),
                    run.getId().toString(),
                    run.getStartedAt(),
                    reportImages
            );
        }
        report = withPotteryInspectionState(report, run);
        saveReport(run, report);
    }

    // 저장된 리포트에 run이 들고 있는 최신 도자기 검사 결과/상태를 덮어씌워 반환.
    private ReportResponse withPotteryInspectionState(
            ReportResponse report,
            AssessmentRun run
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
                report.ragArtifacts(),
                findPotteryInspection(run.getId()).orElse(null),
                run.getPotteryInspectionStatus()
        );
    }

    // inspection_result_pottery에 컬럼을 추가하지 않기로 해서, detail jsonb에
    // 안 겹칠 예약 키로 moduleVersion/summary를 같이 실어 보낸다 - 저장 시
    // savePotteryResult가 여기 담고, 읽을 때 findPotteryInspection이 다시 뺀다.
    // API 응답 모양(ReportResponse.PotteryInspection)은 이 저장 방식과 무관하게
    // 그대로 유지된다.
    private static final String DETAIL_MODULE_VERSION_KEY = "__module_version";
    private static final String DETAIL_SUMMARY_KEY = "__summary";

    // 도자기 검사 결과를 저장(신규 생성 또는 재시도 시 같은 run의 기존 row 덮어쓰기).
    private void savePotteryResult(UUID assessmentRunId, ReportResponse.PotteryInspection potteryInspection) {
        UUID id = potteryResultStore.findByAssessmentRunId(assessmentRunId)
                .map(InspectionResultPottery::getId)
                .orElseGet(UUID::randomUUID);
        Map<String, Object> storedDetail = new LinkedHashMap<>();
        if (potteryInspection.detail() != null) {
            storedDetail.putAll(potteryInspection.detail());
        }
        storedDetail.put(DETAIL_MODULE_VERSION_KEY, potteryInspection.moduleVersion());
        storedDetail.put(DETAIL_SUMMARY_KEY, potteryInspection.summary());
        potteryResultStore.save(InspectionResultPottery.builder()
                .id(id)
                .assessmentRunId(assessmentRunId)
                .inspectionText(potteryInspection.inspectionText())
                .humanReviewRecommended(potteryInspection.humanReviewRecommended())
                .detail(storedDetail)
                .createdAt(Instant.now())
                .build());
    }

    // assessment_run_id로 저장된 도자기 검사 결과를 리포트 응답 모양으로 복원.
    private Optional<ReportResponse.PotteryInspection> findPotteryInspection(UUID assessmentRunId) {
        return potteryResultStore.findByAssessmentRunId(assessmentRunId)
                .map(entity -> {
                    Map<String, Object> storedDetail = entity.getDetail() != null
                            ? new LinkedHashMap<>(entity.getDetail()) : new LinkedHashMap<>();
                    Object moduleVersion = storedDetail.remove(DETAIL_MODULE_VERSION_KEY);
                    Object summary = storedDetail.remove(DETAIL_SUMMARY_KEY);
                    return new ReportResponse.PotteryInspection(
                            moduleVersion == null ? null : String.valueOf(moduleVersion),
                            entity.getInspectionText(),
                            summary == null ? null : String.valueOf(summary),
                            entity.isHumanReviewRecommended(),
                            storedDetail
                    );
                });
    }

    // 리포트를 저장/갱신. 기존 리포트가 있으면 최초 createdAt은 보존하고 updatedAt만 새로 찍는다
    // (재저장 시 생성 시각이 덮어써지지 않도록 하는 부분이 핵심).
    private void saveReport(AssessmentRun run, ReportResponse report) {
        Instant now = Instant.now();
        Instant createdAt = reportStore.findById(run.getId())
                .map(AssessmentReport::getCreatedAt)
                .orElse(now);
        reportStore.save(AssessmentReport.builder()
                .assessmentRunId(run.getId())
                .reportJson(report)
                .status(report.status())
                .overallCondition(report.summary() == null ? null : report.summary().overallCondition())
                .riskLevel(report.summary() == null ? null : report.summary().riskLevel())
                .generatedAt(report.generatedAt())
                .createdAt(createdAt)
                .updatedAt(now)
                .build());
    }

    private ReportResponse findReport(UUID assessmentRunId) {
        return reportStore.findById(assessmentRunId)
                .map(AssessmentReport::getReportJson)
                .orElse(null);
    }

    private List<ReportResponse.Finding> toFindings(
            VcaArtifactEntity artifact,
            List<VcaAiAssessmentFinding> aiFindings
    ) {
        // vca-ai/vca_v2는 이미지를 content sha256으로만 안다(vca_artifacts.py의
        // engine image_id -> sha256 변환 참고) - FE는 다른 모든 VCA 엔드포인트
        // (이미지 삭제/완료 등)와 동일하게 Spring의 업로드 uuid만 봐야 하므로,
        // Spring 경계에서 sha256을 uuid로 다시 변환해준다.
        Map<String, String> imageIdBySha256 = imageStore.findByArtifactId(artifact.getId()).stream()
                .filter(image -> image.getContentSha256() != null)
                .collect(Collectors.toMap(
                        UploadedImage::getContentSha256,
                        image -> image.getId().toString(),
                        (first, second) -> first
                ));
        List<ReportResponse.Finding> findings = new ArrayList<>();
        for (int index = 0; index < aiFindings.size(); index++) {
            VcaAiAssessmentFinding finding = aiFindings.get(index);
            findings.add(new ReportResponse.Finding(
                    "finding-vca-ai-" + (index + 1),
                    finding.category(),
                    finding.severity(),
                    finding.message(),
                    finding.conceptFamily(),
                    finding.descriptor(),
                    imageIdBySha256.getOrDefault(finding.imageId(), finding.imageId()),
                    toFindingCitations(finding.citations()),
                    toFindingBbox(finding.bbox()),
                    toFindingPolygons(finding.polygons())
            ));
        }
        return findings;
    }

    private static List<ReportResponse.Citation> toFindingCitations(
            List<VcaAiAssessmentFinding.Citation> citations
    ) {
        if (citations == null) {
            return List.of();
        }
        return citations.stream()
                .filter(citation -> citation != null)
                .map(citation -> new ReportResponse.Citation(
                        citation.citationId(),
                        citation.sourceCitation(),
                        citation.pageNumber()
                ))
                .toList();
    }

    private static ReportResponse.Bbox toFindingBbox(VcaAiAssessmentFinding.Bbox bbox) {
        if (bbox == null) {
            return null;
        }
        return new ReportResponse.Bbox(
                bbox.xMin(),
                bbox.yMin(),
                bbox.xMax(),
                bbox.yMax()
        );
    }

    private static List<List<ReportResponse.Point>> toFindingPolygons(
            List<List<VcaAiAssessmentFinding.Point>> polygons
    ) {
        if (polygons == null) {
            return null;
        }
        return polygons.stream()
                .filter(polygon -> polygon != null)
                .map(VcaService::toFindingPolygon)
                .toList();
    }

    private static List<ReportResponse.Point> toFindingPolygon(
            List<VcaAiAssessmentFinding.Point> polygon
    ) {
        return polygon.stream()
                .filter(point -> point != null)
                .map(point -> new ReportResponse.Point(point.x(), point.y()))
                .toList();
    }

    // 아티팩트 종합 상태 계산. ANALYZING > ASSESSED > READY > DRAFT 순으로 우선순위를 두고
    // 가장 먼저 해당하는 조건으로 판정한다(여러 run이 섞여 있어도 이 순서로 단순화).
    private String artifactStatus(List<AssessmentRun> runs, List<UploadedImage> images) {
        if (runs.stream().anyMatch(run ->
                "QUEUED".equals(run.getStatus()) || "RUNNING".equals(run.getStatus()))) {
            return "ANALYZING";
        }
        if (runs.stream().anyMatch(run -> "COMPLETED".equals(run.getStatus()))) {
            return "ASSESSED";
        }
        if (images.stream().anyMatch(image -> "UPLOADED".equals(image.getStatus()))) {
            return "READY";
        }
        return "DRAFT";
    }

    // 존재하지 않으면 404. artifactId는 createArtifact가 반환한 UUID여야 하며(더는 클라이언트가
    // 자유롭게 정하는 문자열이 아니다), 이미지/run 등 하위 리소스를 다루는 모든 메서드가 이
    // 조회 전용 헬퍼를 거친다.
    private VcaArtifactEntity requireArtifact(String artifactId) {
        UUID id = validateUuid(artifactId, "artifactId");
        return artifactStore.findById(id)
                .orElseThrow(() -> new VcaApiException(
                        HttpStatus.NOT_FOUND,
                        "ARTIFACT_NOT_FOUND",
                        "The requested VCA artifact was not found."
                ));
    }

    private UploadedImage requireImage(VcaArtifactEntity artifact, UUID imageId) {
        return imageStore.findById(imageId)
                .filter(image -> image.getArtifactId().equals(artifact.getId()))
                .orElseThrow(() -> new VcaApiException(
                        HttpStatus.NOT_FOUND,
                        "IMAGE_NOT_FOUND",
                        "The requested VCA image was not found."
                ));
    }

    private AssessmentRun requireRun(VcaArtifactEntity artifact, String assessmentRunId) {
        UUID id = validateUuid(assessmentRunId, "assessmentRunId");
        return runStore.findById(id)
                .filter(run -> run.getArtifactId().equals(artifact.getId()))
                .orElseThrow(() -> new VcaApiException(
                        HttpStatus.NOT_FOUND,
                        "RUN_NOT_FOUND",
                        "The requested VCA assessment run was not found."
                ));
    }

    private ReportPdfJob requirePdfJob(VcaArtifactEntity artifact, String jobId) {
        UUID id = validateUuid(jobId, "jobId");
        ReportPdfJob job = pdfJobStore.findById(id).orElseThrow(() -> new VcaApiException(
                HttpStatus.NOT_FOUND,
                "PDF_JOB_NOT_FOUND",
                "The requested VCA report PDF job was not found."
        ));
        AssessmentRun run = runStore.findById(job.getAssessmentRunId()).orElseThrow(() -> new VcaApiException(
                HttpStatus.NOT_FOUND,
                "PDF_JOB_NOT_FOUND",
                "The requested VCA report PDF job was not found."
        ));
        if (!run.getArtifactId().equals(artifact.getId())) {
            throw new VcaApiException(
                    HttpStatus.NOT_FOUND,
                    "PDF_JOB_NOT_FOUND",
                    "The requested VCA report PDF job was not found."
            );
        }
        return job;
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

    private UUID validateUuid(String value, String fieldName) {
        try {
            return UUID.fromString(value);
        } catch (IllegalArgumentException | NullPointerException exception) {
            throw new VcaApiException(
                    HttpStatus.BAD_REQUEST,
                    "VALIDATION_ERROR",
                    fieldName + " must be a UUID."
            );
        }
    }

    // VcaArtifactEntity의 createdAt/updatedAt은 팀 공유 artifacts 테이블 컬럼 타입(TIMESTAMP,
    // 타임존 없음)에 맞춰 LocalDateTime이다. API 응답 DTO(ArtifactDetailResponse 등)는 다른
    // 엔티티(AssessmentRun 등)와 일관되게 Instant를 쓰므로, 엔티티에 쓰고 읽는 두 지점에서만
    // UTC 기준으로 변환한다.
    private static LocalDateTime toEntityTimestamp(Instant instant) {
        return LocalDateTime.ofInstant(instant, ZoneOffset.UTC);
    }

    private static Instant toApiTimestamp(LocalDateTime localDateTime) {
        return localDateTime.toInstant(ZoneOffset.UTC);
    }

    private String fileGatewayUrl(String artifactId, String sha256) {
        return "/api/vca/" + artifactId + "/files/sha256/" + sha256;
    }

    // 설정값(vca.local-direct-complete-enabled)에 따라 업로드 모드를 결정한다.
    // 로컬 개발에서 S3 presign 없이 바로 완료 처리하고 싶을 때 DIRECT_COMPLETE를 켠다.
    private String uploadMode() {
        if (localDirectCompleteEnabled) {
            return "DIRECT_COMPLETE";
        }
        return "SIGNED_PUT";
    }

    private String projectName(String artifactId, String assessmentRunId) {
        return artifactId + "-" + assessmentRunId;
    }

    private String firstUploadedImageUrl(VcaArtifactEntity artifact) {
        return imageStore.findByArtifactId(artifact.getId()).stream()
                .filter(image -> "UPLOADED".equals(image.getStatus()))
                .sorted(Comparator.comparingInt(UploadedImage::getDisplayOrder))
                .findFirst()
                .map(image -> fileGatewayUrl(artifact.getId().toString(), image.getContentSha256()))
                .orElse(null);
    }

    private record RunReservation(
            AssessmentRun run,
            List<UploadedImage> uploadedImages,
            String resumeFromProjectName
    ) {
    }

    private record PotteryInspectionTarget(
            boolean applicable,
            UploadedImage primaryImage,
            boolean alreadyHandled
    ) {
    }

    // 저장된 바이트 배열을 Spring의 MultipartFile 인터페이스로 흉내내는 어댑터.
    // toMultipartFile에서 도자기 검사 클라이언트 호출용으로만 사용된다.
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

    // --- 테스트/독립 실행용 in-memory Store 구현. 운영 빈은 vca.repository의 JPA 구현체. ---

    private static final class InMemoryVcaArtifactStore implements VcaArtifactStore {
        private final Map<UUID, VcaArtifactEntity> byId = new LinkedHashMap<>();

        @Override
        public synchronized VcaArtifactEntity save(VcaArtifactEntity entity) {
            // 실제 JPA 저장소는 엔티티의 @GeneratedValue(UUID)가 id를 채워주지만,
            // 이 인메모리 폴백은 그 생성기 자체가 없으므로 여기서 흉내낸다.
            if (entity.getId() == null) {
                entity.setId(UUID.randomUUID());
            }
            byId.put(entity.getId(), entity);
            return entity;
        }

        @Override
        public synchronized Optional<VcaArtifactEntity> findById(UUID id) {
            return Optional.ofNullable(byId.get(id));
        }

        @Override
        public synchronized List<VcaArtifactEntity> findAll() {
            return List.copyOf(byId.values());
        }
    }

    private static final class InMemoryUploadedImageStore implements UploadedImageStore {
        private final Map<UUID, UploadedImage> byId = new LinkedHashMap<>();

        @Override
        public synchronized UploadedImage save(UploadedImage entity) {
            byId.put(entity.getId(), entity);
            return entity;
        }

        @Override
        public synchronized Optional<UploadedImage> findById(UUID id) {
            return Optional.ofNullable(byId.get(id));
        }

        @Override
        public synchronized List<UploadedImage> findByArtifactId(UUID artifactId) {
            return byId.values().stream()
                    .filter(entity -> entity.getArtifactId().equals(artifactId))
                    .toList();
        }

        @Override
        public synchronized void deleteById(UUID id) {
            byId.remove(id);
        }
    }

    private static final class InMemoryAssessmentRunStore implements AssessmentRunStore {
        private final Map<UUID, AssessmentRun> byId = new LinkedHashMap<>();

        @Override
        public synchronized AssessmentRun save(AssessmentRun entity) {
            // Mirrors the real (artifact_id, run_number) DB unique constraint
            // so tests can exercise the race-losing path the same way
            // production does under DataIntegrityViolationException.
            boolean duplicateRunNumber = byId.values().stream()
                    .anyMatch(existing -> !existing.getId().equals(entity.getId())
                            && existing.getArtifactId().equals(entity.getArtifactId())
                            && existing.getRunNumber() == entity.getRunNumber());
            if (duplicateRunNumber) {
                throw new DataIntegrityViolationException(
                        "duplicate run_number for artifact " + entity.getArtifactId()
                );
            }
            byId.put(entity.getId(), entity);
            return entity;
        }

        @Override
        public synchronized Optional<AssessmentRun> findById(UUID id) {
            return Optional.ofNullable(byId.get(id));
        }

        @Override
        public synchronized List<AssessmentRun> findAll() {
            return List.copyOf(byId.values());
        }

        @Override
        public synchronized List<AssessmentRun> findByArtifactId(UUID artifactId) {
            return byId.values().stream()
                    .filter(entity -> entity.getArtifactId().equals(artifactId))
                    .toList();
        }

        @Override
        public synchronized int countByArtifactId(UUID artifactId) {
            return (int) byId.values().stream()
                    .filter(entity -> entity.getArtifactId().equals(artifactId))
                    .count();
        }

        @Override
        public synchronized void deleteById(UUID id) {
            byId.remove(id);
        }
    }

    private static final class InMemoryAssessmentReportStore implements AssessmentReportStore {
        private final Map<UUID, AssessmentReport> byId = new LinkedHashMap<>();

        @Override
        public synchronized AssessmentReport save(AssessmentReport entity) {
            byId.put(entity.getAssessmentRunId(), entity);
            return entity;
        }

        @Override
        public synchronized Optional<AssessmentReport> findById(UUID assessmentRunId) {
            return Optional.ofNullable(byId.get(assessmentRunId));
        }
    }

    private static final class InMemoryInspectionResultPotteryStore implements InspectionResultPotteryStore {
        private final Map<UUID, InspectionResultPottery> byAssessmentRunId = new LinkedHashMap<>();

        @Override
        public synchronized InspectionResultPottery save(InspectionResultPottery entity) {
            byAssessmentRunId.put(entity.getAssessmentRunId(), entity);
            return entity;
        }

        @Override
        public synchronized Optional<InspectionResultPottery> findByAssessmentRunId(UUID assessmentRunId) {
            return Optional.ofNullable(byAssessmentRunId.get(assessmentRunId));
        }
    }

    private static final class InMemoryReportPdfJobStore implements ReportPdfJobStore {
        private final Map<UUID, ReportPdfJob> byId = new LinkedHashMap<>();

        @Override
        public synchronized ReportPdfJob save(ReportPdfJob entity) {
            byId.put(entity.getId(), entity);
            return entity;
        }

        @Override
        public synchronized Optional<ReportPdfJob> findById(UUID id) {
            return Optional.ofNullable(byId.get(id));
        }

        @Override
        public synchronized Optional<ReportPdfJob> findByAssessmentRunId(UUID assessmentRunId) {
            return byId.values().stream()
                    .filter(entity -> entity.getAssessmentRunId().equals(assessmentRunId))
                    .findFirst();
        }
    }
}
