package com.aivle.conservation_backend.photo.service;

import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.DeleteObjectRequest;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.services.s3.presigner.model.GetObjectPresignRequest;
import software.amazon.awssdk.services.s3.model.DeleteObjectRequest;
import software.amazon.awssdk.services.s3.model.ListObjectsV2Request;
import software.amazon.awssdk.services.s3.model.ListObjectsV2Response;
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

    /** 기존 범용 임시 사진 업로드. 기존 화면 호환을 위해 그대로 유지한다. */
    public String upload(MultipartFile file) {
        return upload(null, file);
    }

    public String upload(UUID artifactId, MultipartFile file) {
        return uploadStored(artifactId, file).url();
    }

    /**
     * 보존가이드 작업 사진을 S3에 저장하고 영구 식별자인 key와 즉시 표시용
     * presigned URL을 함께 반환한다. Task/AI checkpoint에는 만료되는 URL만
     * 저장하지 않고 key도 함께 보관해 페이지 재진입 시 URL을 새로 발급한다.
     */
    public StoredPhoto uploadStored(UUID artifactId, MultipartFile file) {
        String prefix = artifactId == null
                ? "wetting-photos/"
                : "artifacts/" + artifactId + "/uploads/";

        String key = prefix
                + UUID.randomUUID()
                + "-"
                + file.getOriginalFilename();

        uploadToS3(key, file);

        return new StoredPhoto(key, presignedUrl(key));
    }

    /**
     * 특정 유물의 보존가이드 업로드 사진 key에 대해서만 새 presigned URL을 발급한다.
     * 다른 유물의 S3 key를 임의로 전달해 조회할 수 없도록 prefix를 검증한다.
     */
    public String presignedArtifactUploadUrl(UUID artifactId, String key) {
        if (artifactId == null || key == null || key.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "사진 key가 올바르지 않습니다.");
        }

        String expectedPrefix = "artifacts/" + artifactId + "/uploads/";
        if (!key.startsWith(expectedPrefix)) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "이 유물의 사진이 아닙니다.");
        }

        return presignedUrl(key);
    }

    public record StoredPhoto(String key, String url) {
    }

    /**
     * 육안조사 원본 사진을 artifact/run 기준 영구 key로 저장한다.
     * DB에는 만료되는 presigned URL이 아니라 이 key를 저장하고, 조회 시마다
     * presignedUrl(key)를 새로 발급한다.
     */
    public String uploadPotteryInspection(
            UUID artifactId,
            UUID assessmentRunId,
            MultipartFile file
    ) {
        validateImage(file);

        String key =
                "artifacts/"
                        + artifactId
                        + "/vca/"
                        + assessmentRunId
                        + "/inspection/"
                        + UUID.randomUUID()
                        + getExtension(file.getOriginalFilename());

        uploadToS3(key, file);
        return key;
    }

    /** 문양조사 보정 이미지를 run 기준 영구 key로 저장한다. */
    public String uploadPotteryAnnotated(
            UUID artifactId,
            UUID assessmentRunId,
            MultipartFile file
    ) {
        validateImage(file);

        String key =
                "artifacts/"
                        + artifactId
                        + "/vca/"
                        + assessmentRunId
                        + "/annotated/"
                        + UUID.randomUUID()
                        + getExtension(file.getOriginalFilename());

        uploadToS3(key, file);
        return key;
    }

    /* 생성된 보고서 .docx를 유물별 영구 key로 업로드한다. */
    public String uploadReportDocx(UUID artifactId, byte[] docx) {
        String key = "artifacts/" + artifactId + "/reports/" + UUID.randomUUID() + ".docx";

        s3Client.putObject(
                PutObjectRequest.builder()
                        .bucket(bucket)
                        .key(key)
                        .contentType(
                                "application/vnd.openxmlformats-officedocument"
                                        + ".wordprocessingml.document"
                        )
                        .build(),
                RequestBody.fromBytes(docx)
        );

        return key;
    }

    /* 유물 대표 이미지 업로드 */
    public String uploadArtifactRepresentative(
            UUID artifactId,
            MultipartFile file
    ) {
        validateImage(file);

        String extension = getExtension(file.getOriginalFilename());

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

    /* DB에 저장된 key를 프론트에서 사용할 URL로 변경 */
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
                        .signatureDuration(Duration.ofHours(1))
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
                    "이미지를 선택해주세요."
            );
        }

        String contentType = file.getContentType();

        if (contentType == null || !contentType.startsWith("image/")) {
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

        String extension = filename.substring(dotIndex);

        if (!extension.matches("\\.[A-Za-z0-9]+")) {
            return "";
        }

        return extension;
    }

    public void deletePrefix(String prefix) {
        if (prefix == null || prefix.isBlank()) {
            return;
        }

        String continuationToken = null;
        do {
            ListObjectsV2Response response = s3Client.listObjectsV2(
                    ListObjectsV2Request.builder()
                            .bucket(bucket)
                            .prefix(prefix)
                            .continuationToken(continuationToken)
                            .build()
            );

            response.contents().forEach(object -> delete(object.key()));
            continuationToken = response.isTruncated()
                    ? response.nextContinuationToken()
                    : null;
        } while (continuationToken != null);
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
