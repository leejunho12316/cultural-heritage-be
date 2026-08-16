package com.aivle.conservation_backend.report_ai.dto;

import com.aivle.conservation_backend.report_ai.domain.ReportDocument;

import java.time.LocalDateTime;
import java.util.Map;
import java.util.UUID;

// 저장된 보고서 조회 응답. docxDownloadUrl은 저장된 영구 S3 key를 조회
// 시점에 매번 새로 presign한 값이다(대표 이미지와 같은 패턴) - 응답을
// 캐싱해서 나중에 다시 쓰면 만료될 수 있으니 매번 새로 조회해야 한다.
public record ReportDocumentResponseDto(
        UUID id,
        UUID artifactId,
        Map<String, Object> reportJson,
        String docxDownloadUrl,
        LocalDateTime createdAt,
        LocalDateTime updatedAt
) {
    public static ReportDocumentResponseDto of(ReportDocument document, String docxDownloadUrl) {
        return new ReportDocumentResponseDto(
                document.getId(),
                document.getArtifact().getId(),
                document.getReportJson(),
                docxDownloadUrl,
                document.getCreatedAt(),
                document.getUpdatedAt()
        );
    }
}
