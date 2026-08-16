package com.aivle.conservation_backend.report_ai.service;

import com.aivle.conservation_backend.artifact.domain.Artifact;
import com.aivle.conservation_backend.artifact.repository.ArtifactRepository;
import com.aivle.conservation_backend.photo.service.S3PhotoStorageService;
import com.aivle.conservation_backend.report_ai.client.ReportAiClient;
import com.aivle.conservation_backend.report_ai.domain.ReportDocument;
import com.aivle.conservation_backend.report_ai.dto.DocxRequestDto;
import com.aivle.conservation_backend.report_ai.dto.ReportDocumentResponseDto;
import com.aivle.conservation_backend.report_ai.dto.SaveReportRequestDto;
import com.aivle.conservation_backend.report_ai.repository.ReportDocumentRepository;

import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.UUID;

/**
 * report-ai가 만든 report_json을 유물(artifact) 기준으로 저장/조회한다.
 *
 * assessment_report(vca 파이프라인 산출물)와는 별개다 - 여기서는
 * assessment_run 존재 여부와 무관하게, artifact_id만 있으면 저장할 수
 * 있다 (2026-08-11 팀 논의 결과).
 */
@RequiredArgsConstructor
@Service
@Transactional(readOnly = true)
public class ReportDocumentService {

    private final ArtifactRepository artifactRepository;
    private final ReportDocumentRepository reportDocumentRepository;
    private final ReportAiClient reportAiClient;
    private final S3PhotoStorageService photoStorageService;

    /** 생성된 report_json만 먼저 저장한다. DOCX는 나중에 별도 저장한다. */
    @Transactional
    public ReportDocumentResponseDto saveJson(UUID artifactId, SaveReportRequestDto request) {
        Artifact artifact = findArtifact(artifactId);

        ReportDocument document = reportDocumentRepository.save(
                ReportDocument.builder()
                        .artifact(artifact)
                        .reportJson(request.reportJson())
                        .docxObjectKey(null)
                        .build()
        );

        return toResponse(document);
    }

    /**
     * 이미 만들어진(미리보기까지 끝난) report_json을 .docx로 변환해서
     * S3에 영구 저장하고, 그 결과를 DB에 기록한다. LLM은 재호출하지
     * 않는다(reportToDocx는 변환만 함).
     */
    @Transactional
    public ReportDocumentResponseDto save(UUID artifactId, SaveReportRequestDto request) {
        Artifact artifact = findArtifact(artifactId);

        byte[] docx = reportAiClient.reportToDocx(new DocxRequestDto(
                artifactId.toString(),
                request.reportJson(),
                request.photos()
        ));
        String docxObjectKey = photoStorageService.uploadReportDocx(artifactId, docx);

        ReportDocument document = reportDocumentRepository.save(
                ReportDocument.builder()
                        .artifact(artifact)
                        .reportJson(request.reportJson())
                        .docxObjectKey(docxObjectKey)
                        .build()
        );

        return toResponse(document);
    }

    /** 이 유물의 가장 최근 저장 보고서를 조회한다. */
    public ReportDocumentResponseDto findLatest(UUID artifactId) {
        ReportDocument document = reportDocumentRepository
                .findFirstByArtifact_IdOrderByCreatedAtDesc(artifactId)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND,
                        "저장된 보고서가 없습니다: " + artifactId
                ));

        return toResponse(document);
    }

    private Artifact findArtifact(UUID artifactId) {
        return artifactRepository.findById(artifactId)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND,
                        "존재하지 않는 유물입니다: " + artifactId
                ));
    }

    private ReportDocumentResponseDto toResponse(ReportDocument document) {
        String objectKey = document.getDocxObjectKey();
        String downloadUrl = objectKey == null || objectKey.isBlank()
                ? null
                : photoStorageService.presignedUrl(objectKey);
        return ReportDocumentResponseDto.of(document, downloadUrl);
    }
}
