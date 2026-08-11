package com.aivle.conservation_backend.artifact.service;

import com.aivle.conservation_backend.artifact.domain.Artifact;
import com.aivle.conservation_backend.artifact.dto.AddArtifactRequest;
import com.aivle.conservation_backend.artifact.dto.ArtifactResponse;
import com.aivle.conservation_backend.artifact.dto.UpdateArtifactRequest;
import com.aivle.conservation_backend.artifact.repository.ArtifactRepository;
import com.aivle.conservation_backend.photo.service.S3PhotoStorageService;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Sort;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.UUID;

@RequiredArgsConstructor
@Service
@Transactional(readOnly = true)
public class ArtifactService {

    private final ArtifactRepository artifactRepository;
    private final S3PhotoStorageService photoStorageService;

    /*
     * 신규 유물 등록
     */
    @Transactional
    public ArtifactResponse save(
            AddArtifactRequest request
    ) {
        Artifact artifact =
                artifactRepository.save(
                        request.toEntity()
                );

        return toResponse(artifact);
    }

    /*
     * 전체 프로젝트 / 기존 프로젝트
     */
    public List<ArtifactResponse> findAll() {

        return artifactRepository.findAll(
                        Sort.by(
                                Sort.Direction.DESC,
                                "updatedAt"
                        )
                )
                .stream()
                .map(this::toResponse)
                .toList();
    }

    /*
     * 유물 상세
     */
    public ArtifactResponse findById(
            UUID artifactId
    ) {
        return toResponse(
                findArtifact(artifactId)
        );
    }

    /*
     * 공통 유물정보 수정
     */
    @Transactional
    public ArtifactResponse update(
            UUID artifactId,
            UpdateArtifactRequest request
    ) {
        Artifact artifact =
                findArtifact(artifactId);

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

    /*
     * 대표이미지 등록 / 교체
     */
    @Transactional
    public ArtifactResponse uploadRepresentativeImage(
            UUID artifactId,
            MultipartFile file
    ) {
        Artifact artifact = findArtifact(artifactId);

        String oldImageKey =
                artifact.getRepresentativeImageKey();

        String newImageKey =
                photoStorageService
                        .uploadArtifactRepresentative(
                                artifactId,
                                file
                        );

        artifact.updateRepresentativeImage(
                newImageKey
        );

        // 새 이미지 업로드 성공 후 기존 이미지 삭제
        if (oldImageKey != null
                && !oldImageKey.isBlank()
                && !oldImageKey.equals(newImageKey)) {

            photoStorageService.delete(oldImageKey);
        }

        return toResponse(artifact);
    }

    private Artifact findArtifact(
            UUID artifactId
    ) {
        return artifactRepository
                .findById(artifactId)
                .orElseThrow(() ->
                        new ResponseStatusException(
                                HttpStatus.NOT_FOUND,
                                "존재하지 않는 유물입니다."
                        )
                );
    }

    private ArtifactResponse toResponse(
            Artifact artifact
    ) {
        String imageUrl = null;

        if (artifact.getRepresentativeImageKey() != null) {

            imageUrl =
                    photoStorageService.presignedUrl(
                            artifact.getRepresentativeImageKey()
                    );
        }

        return new ArtifactResponse(
                artifact,
                imageUrl
        );
    }

    @Transactional
    public void delete(UUID artifactId) {

        Artifact artifact = findArtifact(artifactId);

        String imageKey =
                artifact.getRepresentativeImageKey();

        // 대표이미지가 있으면 S3에서도 삭제
        if (imageKey != null && !imageKey.isBlank()) {
            photoStorageService.delete(imageKey);
        }

        artifactRepository.delete(artifact);
    }


}