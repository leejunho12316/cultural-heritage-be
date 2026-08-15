package com.aivle.conservation_backend.artifact.service;

import com.aivle.conservation_backend.artifact.domain.Artifact;
import com.aivle.conservation_backend.artifact.dto.AddArtifactRequest;
import com.aivle.conservation_backend.artifact.dto.ArtifactResponse;
import com.aivle.conservation_backend.artifact.dto.ArtifactPublicResponse;
import com.aivle.conservation_backend.artifact.dto.UpdateArtifactRequest;
import com.aivle.conservation_backend.artifact.repository.ArtifactRepository;
import com.aivle.conservation_backend.conservation_guide_ai.domain.Task;
import com.aivle.conservation_backend.conservation_guide_ai.repository.TaskRepository;
import com.aivle.conservation_backend.photo.service.S3PhotoStorageService;
import com.aivle.conservation_backend.pottery_inspection_ai.repository.InspectionResultPotteryRepository;
import com.aivle.conservation_backend.report_ai.domain.ReportDocument;
import com.aivle.conservation_backend.report_ai.repository.ReportDocumentRepository;
import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import com.aivle.conservation_backend.vca.domain.ReportPdfJob;
import com.aivle.conservation_backend.vca.domain.UploadedImage;
import com.aivle.conservation_backend.vca.repository.AssessmentReportRepository;
import com.aivle.conservation_backend.vca.repository.AssessmentRunRepository;
import com.aivle.conservation_backend.vca.repository.ReportPdfJobRepository;
import com.aivle.conservation_backend.vca.repository.UploadedImageRepository;
import com.aivle.conservation_backend.xray_api.domain.S3FileRecord;
import com.aivle.conservation_backend.xray_api.domain.XrayJob;
import com.aivle.conservation_backend.xray_api.repository.S3FileRecordRepository;
import com.aivle.conservation_backend.xray_api.repository.XrayDefectRepository;
import com.aivle.conservation_backend.xray_api.repository.XrayJobRepository;
import com.aivle.conservation_backend.xray_api.storage.XrayS3Keys;
import com.aivle.conservation_backend.xray_api.storage.XrayS3Service;
import com.aivle.conservation_backend.user.domain.User;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.multipart.MultipartFile;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

@RequiredArgsConstructor
@Service
@Transactional(readOnly = true)
public class ArtifactService {

    private final ArtifactRepository artifactRepository;
    private final ArtifactAccessService artifactAccessService;
    private final S3PhotoStorageService photoStorageService;

    private final TaskRepository taskRepository;
    private final XrayJobRepository xrayJobRepository;
    private final XrayDefectRepository xrayDefectRepository;
    private final S3FileRecordRepository s3FileRecordRepository;
    private final XrayS3Service xrayS3Service;

    private final AssessmentRunRepository assessmentRunRepository;
    private final InspectionResultPotteryRepository inspectionResultPotteryRepository;
    private final AssessmentReportRepository assessmentReportRepository;
    private final ReportPdfJobRepository reportPdfJobRepository;
    private final UploadedImageRepository uploadedImageRepository;

    private final ReportDocumentRepository reportDocumentRepository;

    @Transactional
    public ArtifactResponse save(AddArtifactRequest request) {
        User currentUser = artifactAccessService.currentUser();
        Artifact artifact = artifactRepository.save(request.toEntity(currentUser));
        return toResponse(artifact);
    }

    public List<ArtifactResponse> findAll() {
        User currentUser = artifactAccessService.currentUser();
        List<Artifact> artifacts = artifactAccessService.isAdmin(currentUser)
                ? artifactRepository.findAll(Sort.by(Sort.Direction.DESC, "updatedAt"))
                : artifactRepository.findAllByOwner_IdOrderByUpdatedAtDesc(currentUser.getId());

        return artifacts.stream()
                .map(this::toResponse)
                .toList();
    }

    public List<ArtifactResponse> findMine() {
        User currentUser = artifactAccessService.currentUser();
        return artifactRepository.findAllByOwner_IdOrderByUpdatedAtDesc(currentUser.getId())
                .stream()
                .map(this::toResponse)
                .toList();
    }

    public List<ArtifactPublicResponse> findPublic() {
        artifactAccessService.currentUser();
        return artifactRepository.findAll(Sort.by(Sort.Direction.DESC, "updatedAt"))
                .stream()
                .map(this::toPublicResponse)
                .toList();
    }

    public ArtifactResponse findById(UUID artifactId) {
        return toResponse(findArtifact(artifactId));
    }

    @Transactional
    public ArtifactResponse update(UUID artifactId, UpdateArtifactRequest request) {
        Artifact artifact = findArtifact(artifactId);

        artifact.update(
                request.getName(),
                request.getCategory(),
                request.getMaterial(),
                request.getEra(),
                request.getDescription(),
                request.getConditionSummary(),
                request.getWeight(),
                request.getBondingArea(),
                request.getTreatmentPurpose()
        );

        return toResponse(artifact);
    }

    @Transactional
    public ArtifactResponse uploadRepresentativeImage(UUID artifactId, MultipartFile file) {
        Artifact artifact = findArtifact(artifactId);

        String oldImageKey = artifact.getRepresentativeImageKey();
        String newImageKey = photoStorageService.uploadArtifactRepresentative(artifactId, file);

        artifact.updateRepresentativeImage(newImageKey);

        if (oldImageKey != null
                && !oldImageKey.isBlank()
                && !oldImageKey.equals(newImageKey)) {
            photoStorageService.delete(oldImageKey);
        }

        return toResponse(artifact);
    }

    /**
     * 유물 프로젝트 전체 삭제.
     *
     * DB는 FK 자식 -> 부모 순서로 지우고, S3는 DB transaction commit 이후에 정리한다.
     * S3 정리가 실패하더라도 이미 삭제된 DB를 되살릴 수는 없으므로, DB 정합성을 우선하고
     * 남은 S3 object는 orphan cleanup 대상으로 남기는 방식이다.
     */
    @Transactional
    public void delete(UUID artifactId) {
        Artifact artifact = findArtifact(artifactId);

        List<String> extraS3Keys = new ArrayList<>();

        // report-ai 문서
        List<ReportDocument> reportDocuments =
                reportDocumentRepository.findAllByArtifact_IdOrderByCreatedAtDesc(artifactId);
        reportDocuments.stream()
                .map(ReportDocument::getDocxObjectKey)
                .filter(key -> key != null && !key.isBlank())
                .forEach(extraS3Keys::add);
        reportDocumentRepository.deleteAll(reportDocuments);

        // 보존가이드 task. 앞으로 FE가 artifactId를 넘기므로 artifact FK로 정확히 삭제된다.
        List<Task> tasks = taskRepository.findAllByArtifact_Id(artifactId);
        taskRepository.deleteAll(tasks);

        // X-ray DB
        xrayJobRepository.findByArtifactId(artifactId).ifPresent(job -> {
            xrayDefectRepository.deleteAllByXrayJob_Id(job.getId());
            xrayJobRepository.delete(job);
        });

        List<S3FileRecord> s3Records = s3FileRecordRepository.findAllByArtifactId(artifactId);
        s3Records.stream()
                .map(S3FileRecord::getS3Key)
                .filter(key -> key != null && !key.isBlank())
                .forEach(extraS3Keys::add);
        s3FileRecordRepository.deleteAll(s3Records);

        // 육안조사 업로드 이미지
        List<UploadedImage> uploadedImages =
                uploadedImageRepository.findAllByArtifactIdOrderByDisplayOrderAscCreatedAtAsc(artifactId);
        for (UploadedImage image : uploadedImages) {
            if (image.getObjectKey() != null && !image.getObjectKey().isBlank()) {
                extraS3Keys.add(image.getObjectKey());
            }
            if (image.getThumbnailObjectKey() != null && !image.getThumbnailObjectKey().isBlank()) {
                extraS3Keys.add(image.getThumbnailObjectKey());
            }
        }
        uploadedImageRepository.deleteAll(uploadedImages);

        // 육안조사 run 및 run 하위 결과
        List<AssessmentRun> runs = assessmentRunRepository.findAllByArtifactIdOrderByRunNumberDesc(artifactId);
        for (AssessmentRun run : runs) {
            inspectionResultPotteryRepository
                    .findFirstByAssessmentRun_IdAndAssessmentRun_ArtifactIdOrderByCreatedAtDesc(
                            run.getId(), artifactId
                    )
                    .ifPresent(inspectionResultPotteryRepository::delete);

            assessmentReportRepository
                    .findByAssessmentRun_IdAndAssessmentRun_ArtifactId(run.getId(), artifactId)
                    .ifPresent(assessmentReportRepository::delete);

            List<ReportPdfJob> pdfJobs = reportPdfJobRepository.findAllByAssessmentRun_Id(run.getId());
            pdfJobs.stream()
                    .map(ReportPdfJob::getPdfObjectKey)
                    .filter(key -> key != null && !key.isBlank())
                    .forEach(extraS3Keys::add);
            reportPdfJobRepository.deleteAll(pdfJobs);
        }
        assessmentRunRepository.deleteAll(runs);

        // 부모 artifact는 마지막에 삭제
        artifactRepository.delete(artifact);

        registerS3CleanupAfterCommit(artifactId, extraS3Keys);
    }

    private void registerS3CleanupAfterCommit(UUID artifactId, List<String> extraS3Keys) {
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            cleanupS3(artifactId, extraS3Keys);
            return;
        }

        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                cleanupS3(artifactId, extraS3Keys);
            }
        });
    }

    private void cleanupS3(UUID artifactId, List<String> extraS3Keys) {
        // 대표이미지, report-ai docx, artifactId를 포함해 업로드한 육안/가이드 사진
        photoStorageService.deletePrefix("artifacts/" + artifactId + "/");

        // X-ray 입력/중간/최종 산출물 전체
        xrayS3Service.deletePrefix(XrayS3Keys.root(artifactId.toString()) + "/");

        // 혹시 prefix 밖의 key가 저장된 기존 데이터가 있으면 개별 key도 정리
        extraS3Keys.stream().distinct().forEach(photoStorageService::delete);
    }

    private Artifact findArtifact(UUID artifactId) {
        return artifactAccessService.requireArtifact(artifactId);
    }

    private ArtifactResponse toResponse(Artifact artifact) {
        String imageUrl = null;

        if (artifact.getRepresentativeImageKey() != null) {
            imageUrl = photoStorageService.presignedUrl(artifact.getRepresentativeImageKey());
        }

        return new ArtifactResponse(artifact, imageUrl);
    }

    private ArtifactPublicResponse toPublicResponse(Artifact artifact) {
        String imageUrl = null;

        if (artifact.getRepresentativeImageKey() != null) {
            imageUrl = photoStorageService.presignedUrl(artifact.getRepresentativeImageKey());
        }

        return new ArtifactPublicResponse(artifact, imageUrl);
    }
}
