package com.aivle.conservation_backend.photo.service;

import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.services.s3.presigner.model.GetObjectPresignRequest;
import software.amazon.awssdk.services.s3.model.DeleteObjectRequest;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.time.Duration;
import java.util.UUID;

// 사진을 S3에 업로드하고, AI 서비스/FE가 접근할 수 있는 presigned URL을 발급.
@RequiredArgsConstructor
@Service
public class S3PhotoStorageService {

    private final S3Client s3Client;
    private final S3Presigner s3Presigner;

    @Value("${aws.s3.bucket}")
    private String bucket;

    public String upload(MultipartFile file) {

        String key =
                "wetting-photos/"
                        + UUID.randomUUID()
                        + "-"
                        + file.getOriginalFilename();

        uploadToS3(key, file);

        return presignedUrl(key);
    }

    /*
     * 유물 대표 이미지 업로드
     */
    public String uploadArtifactRepresentative(
            UUID artifactId,
            MultipartFile file
    ) {
        validateImage(file);

        String extension =
                getExtension(file.getOriginalFilename());

        String key =
                "artifacts/"
                        + artifactId
                        + "/representative/"
                        + UUID.randomUUID()
                        + extension;

        uploadToS3(key, file);

        return key;
    }

    private void uploadToS3(
            String key,
            MultipartFile file
    ) {
        try {
            s3Client.putObject(
                    PutObjectRequest.builder()
                            .bucket(bucket)
                            .key(key)
                            .contentType(file.getContentType())
                            .build(),

                    RequestBody.fromInputStream(
                            file.getInputStream(),
                            file.getSize()
                    )
            );

        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /*
     * DB에 저장된 key를 프론트에서 사용할 URL로 변경
     */
    public String presignedUrl(String key) {

        if (key == null || key.isBlank()) {
            return null;
        }

        GetObjectRequest getObjectRequest =
                GetObjectRequest.builder()
                        .bucket(bucket)
                        .key(key)
                        .build();

        GetObjectPresignRequest presignRequest =
                GetObjectPresignRequest.builder()
                        .signatureDuration(
                                Duration.ofHours(1)
                        )
                        .getObjectRequest(getObjectRequest)
                        .build();

        return s3Presigner
                .presignGetObject(presignRequest)
                .url()
                .toString();
    }

    private void validateImage(MultipartFile file) {

        if (file == null || file.isEmpty()) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "대표 이미지를 선택해주세요."
            );
        }

        String contentType = file.getContentType();

        if (contentType == null
                || !contentType.startsWith("image/")) {

            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "이미지 파일만 업로드할 수 있습니다."
            );
        }
    }

    private String getExtension(String filename) {

        if (filename == null || filename.isBlank()) {
            return "";
        }

        int dotIndex = filename.lastIndexOf('.');

        if (dotIndex < 0) {
            return "";
        }

        String extension =
                filename.substring(dotIndex);

        if (!extension.matches("\\.[A-Za-z0-9]+")) {
            return "";
        }

        return extension;
    }

    public void delete(String key) {
        if (key == null || key.isBlank()) {
            return;
        }

        s3Client.deleteObject(
                DeleteObjectRequest.builder()
                        .bucket(bucket)
                        .key(key)
                        .build()
        );
    }
}