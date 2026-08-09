package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.vca.dto.PresignImageRequest;
import org.springframework.web.multipart.MultipartFile;

import java.net.URI;
import java.time.Instant;
import java.util.List;
import java.util.Map;

// 이미지 저장소 추상화. 운영에서는 VcaS3ImageStorage가 구현하며, 이 빈이 없을 때(로컬 개발 등)
// VcaService는 VcaSharedStorage 기반 로컬 디스크 경로로 자동 폴백한다.
interface VcaImageStorage {

    // 클라이언트가 S3로 직접 PUT할 수 있는 presigned URL을 발급(SIGNED_PUT 업로드 모드).
    PresignedUpload presignUpload(
            String artifactId,
            String imageId,
            PresignImageRequest request,
            Instant expiresAt
    );

    // Spring을 거쳐 바로 저장하는 업로드 경로(DIRECT_UPLOAD). 매직 바이트 검증 후 저장.
    StoredImage storeUpload(String artifactId, String imageId, MultipartFile file);

    // presignUpload로 올라간 객체의 크기/sha256이 예약된 값과 일치하는지 사후 검증.
    void verifyUpload(String objectKey, long expectedSizeBytes, String expectedSha256);

    URI presignedDownload(String objectKey);

    // assessment run 실행을 위해 업로드된 이미지들을 엔진이 읽을 입력 디렉터리로 구체화.
    VcaSharedStorage.RunInputDirectory materializeRunInput(
            String assessmentRunId,
            List<StoredImageReference> images
    );

    StoredImageContent read(String objectKey, String fileName, String contentType);

    void delete(String objectKey);

    record PresignedUpload(
            String objectKey,
            URI uploadUrl,
            Map<String, String> requiredHeaders
    ) {
    }

    record StoredImage(
            String fileName,
            String contentType,
            long sizeBytes,
            String sha256,
            String objectKey
    ) {
    }

    record StoredImageReference(String imageId, String fileName, String objectKey) {
    }

    record StoredImageContent(String fileName, String contentType, byte[] bytes) {
    }
}
