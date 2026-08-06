package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.vca.dto.PresignImageRequest;
import org.springframework.web.multipart.MultipartFile;

import java.net.URI;
import java.time.Instant;
import java.util.List;
import java.util.Map;

interface VcaImageStorage {

    PresignedUpload presignUpload(
            String artifactId,
            String imageId,
            PresignImageRequest request,
            Instant expiresAt
    );

    StoredImage storeUpload(String artifactId, String imageId, MultipartFile file);

    void verifyUpload(String objectKey, long expectedSizeBytes, String expectedSha256);

    URI presignedDownload(String objectKey);

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
