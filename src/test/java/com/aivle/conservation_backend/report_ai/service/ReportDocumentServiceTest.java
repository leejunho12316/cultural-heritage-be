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

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ReportDocumentServiceTest {

    @Mock
    private ArtifactRepository artifactRepository;

    @Mock
    private ReportDocumentRepository reportDocumentRepository;

    @Mock
    private ReportAiClient reportAiClient;

    @Mock
    private S3PhotoStorageService photoStorageService;

    private ReportDocumentService service() {
        return new ReportDocumentService(
                artifactRepository,
                reportDocumentRepository,
                reportAiClient,
                photoStorageService
        );
    }

    private Artifact artifactWithId(UUID id) {
        Artifact artifact = Artifact.builder().name("청자상감운학문매병").build();
        ReflectionTestUtils.setField(artifact, "id", id);
        return artifact;
    }

    private ReportDocument documentWithId(UUID id, Artifact artifact, Map<String, Object> reportJson, String docxKey) {
        ReportDocument document = ReportDocument.builder()
                .artifact(artifact)
                .reportJson(reportJson)
                .docxObjectKey(docxKey)
                .build();
        ReflectionTestUtils.setField(document, "id", id);
        return document;
    }

    @Test
    void save는_존재하지_않는_유물이면_404를_던진다() {
        UUID artifactId = UUID.randomUUID();
        when(artifactRepository.findById(artifactId)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service().save(
                artifactId,
                new SaveReportRequestDto(Map.of("sections", Map.of()), Map.of())
        )).isInstanceOf(ResponseStatusException.class);
    }

    @Test
    void save는_docx로_변환해서_S3에_올리고_report_document를_저장한다() {
        UUID artifactId = UUID.randomUUID();
        Artifact artifact = artifactWithId(artifactId);
        Map<String, Object> reportJson = Map.of("report_type", "ceramic_treatment_report");
        byte[] docxBytes = "fake-docx-bytes".getBytes();

        when(artifactRepository.findById(artifactId)).thenReturn(Optional.of(artifact));
        when(reportAiClient.reportToDocx(any(DocxRequestDto.class))).thenReturn(docxBytes);
        when(photoStorageService.uploadReportDocx(artifactId, docxBytes)).thenReturn("artifacts/" + artifactId + "/reports/x.docx");
        when(reportDocumentRepository.save(any(ReportDocument.class)))
                .thenAnswer(invocation -> {
                    ReportDocument saved = invocation.getArgument(0);
                    ReflectionTestUtils.setField(saved, "id", UUID.randomUUID());
                    return saved;
                });
        when(photoStorageService.presignedUrl("artifacts/" + artifactId + "/reports/x.docx"))
                .thenReturn("https://s3.example.com/presigned");

        ReportDocumentResponseDto response = service().save(
                artifactId,
                new SaveReportRequestDto(reportJson, Map.of())
        );

        // report-ai에는 artifactId/reportJson/photos가 정확히 그대로 전달돼야 한다.
        ArgumentCaptor<DocxRequestDto> requestCaptor = ArgumentCaptor.forClass(DocxRequestDto.class);
        verify(reportAiClient).reportToDocx(requestCaptor.capture());
        assertThat(requestCaptor.getValue().artifactId()).isEqualTo(artifactId.toString());
        assertThat(requestCaptor.getValue().reportJson()).isEqualTo(reportJson);

        assertThat(response.artifactId()).isEqualTo(artifactId);
        assertThat(response.reportJson()).isEqualTo(reportJson);
        assertThat(response.docxDownloadUrl()).isEqualTo("https://s3.example.com/presigned");
    }

    @Test
    void findLatest는_저장된_보고서가_없으면_404를_던진다() {
        UUID artifactId = UUID.randomUUID();
        when(reportDocumentRepository.findFirstByArtifact_IdOrderByCreatedAtDesc(artifactId))
                .thenReturn(Optional.empty());

        assertThatThrownBy(() -> service().findLatest(artifactId))
                .isInstanceOf(ResponseStatusException.class);
    }

    @Test
    void findLatest는_가장_최근_저장본을_presigned_URL과_함께_반환한다() {
        UUID artifactId = UUID.randomUUID();
        UUID documentId = UUID.randomUUID();
        Artifact artifact = artifactWithId(artifactId);
        Map<String, Object> reportJson = Map.of("report_type", "ceramic_treatment_report");
        ReportDocument document = documentWithId(documentId, artifact, reportJson, "artifacts/" + artifactId + "/reports/x.docx");

        when(reportDocumentRepository.findFirstByArtifact_IdOrderByCreatedAtDesc(artifactId))
                .thenReturn(Optional.of(document));
        when(photoStorageService.presignedUrl(eq("artifacts/" + artifactId + "/reports/x.docx")))
                .thenReturn("https://s3.example.com/presigned-latest");

        ReportDocumentResponseDto response = service().findLatest(artifactId);

        assertThat(response.id()).isEqualTo(documentId);
        assertThat(response.artifactId()).isEqualTo(artifactId);
        assertThat(response.docxDownloadUrl()).isEqualTo("https://s3.example.com/presigned-latest");
    }
}
