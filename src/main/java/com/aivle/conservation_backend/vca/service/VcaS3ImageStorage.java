package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.vca.dto.PresignImageRequest;
import com.aivle.conservation_backend.vca.exception.VcaApiException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.core.sync.ResponseTransformer;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.DeleteObjectRequest;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.model.HeadObjectRequest;
import software.amazon.awssdk.services.s3.model.HeadObjectResponse;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.model.S3Exception;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.services.s3.presigner.model.GetObjectPresignRequest;
import software.amazon.awssdk.services.s3.presigner.model.PutObjectPresignRequest;

import java.io.IOException;
import java.net.URI;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Locale;
import java.util.Map;

// VcaImageStorage의 운영용 구현체(S3 기반). aws.s3.bucket이 설정된 환경에서 스프링 빈으로 등록되며,
// 없으면 VcaService는 VcaSharedStorage 기반 로컬 저장으로 대신 동작한다.
@Component
class VcaS3ImageStorage implements VcaImageStorage {

    private static final Logger log = LoggerFactory.getLogger(VcaS3ImageStorage.class);

    private static final Duration DOWNLOAD_URL_TTL = Duration.ofMinutes(5);

    private final S3Client s3Client;
    private final S3Presigner s3Presigner;
    private final S3Presigner internalS3Presigner;
    private final String bucket;
    private final String objectPrefix;

    VcaS3ImageStorage(
            S3Client s3Client,
            S3Presigner s3Presigner,
            @Qualifier("internalS3Presigner") S3Presigner internalS3Presigner,
            @Value("${aws.s3.bucket}") String bucket,
            @Value("${vca.s3.object-prefix:vca/images}") String objectPrefix
    ) {
        this.s3Client = s3Client;
        this.s3Presigner = s3Presigner;
        this.internalS3Presigner = internalS3Presigner;
        this.bucket = bucket;
        this.objectPrefix = removeSlashes(objectPrefix);
    }

    // 클라이언트가 S3로 직접 PUT할 presigned URL을 발급한다. 이 경로는 파일 바이트를 Spring이
    // 직접 보지 않으므로 storeUpload와 달리 매직 바이트 검증이 불가능하다 - 이후 완료 단계에서
    // verifyUpload가 크기/sha256만 사후 검증한다.
    @Override
    public PresignedUpload presignUpload(
            String artifactId,
            String imageId,
            PresignImageRequest request,
            Instant expiresAt
    ) {
        String sha256 = request.sha256().toLowerCase(Locale.ROOT);
        String objectKey = objectKey(artifactId, imageId, request.fileName());
        PutObjectRequest putObjectRequest = PutObjectRequest.builder()
                .bucket(bucket)
                .key(objectKey)
                .contentType(request.contentType())
                .contentLength(request.sizeBytes())
                .metadata(Map.of("sha256", sha256))
                .build();
        PutObjectPresignRequest presignRequest = PutObjectPresignRequest.builder()
                .signatureDuration(Duration.between(Instant.now(), expiresAt))
                .putObjectRequest(putObjectRequest)
                .build();
        Map<String, String> requiredHeaders = Map.of(
                "Content-Type", request.contentType(),
                "x-amz-meta-sha256", sha256
        );
        return new PresignedUpload(
                objectKey,
                URI.create(s3Presigner.presignPutObject(presignRequest).url().toString()),
                requiredHeaders
        );
    }

    // Spring을 거쳐 바로 S3에 저장하는 업로드 경로(DIRECT_UPLOAD). 매직 바이트 검증까지 마친 뒤 저장한다.
    @Override
    public StoredImage storeUpload(String artifactId, String imageId, MultipartFile file) {
        VcaImageUploadValidator.validateUpload(file);
        String fileName = VcaImageUploadValidator.safeFileName(file.getOriginalFilename());
        String objectKey = objectKey(artifactId, imageId, fileName);
        try {
            VcaImageUploadValidator.VerifiedImage verified = VcaImageUploadValidator.verifyAndReadBytes(file);
            s3Client.putObject(
                    PutObjectRequest.builder()
                            .bucket(bucket)
                            .key(objectKey)
                            .contentType(file.getContentType())
                            .contentLength((long) verified.bytes().length)
                            .metadata(Map.of("sha256", verified.sha256()))
                            .build(),
                    RequestBody.fromBytes(verified.bytes())
            );
            return new StoredImage(fileName, file.getContentType(), (long) verified.bytes().length, verified.sha256(), objectKey);
        } catch (IOException exception) {
            log.error("Failed to store uploaded VCA image (I/O)", exception);
            throw new VcaApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "UPLOAD_STORAGE_FAILED",
                    "Failed to store the uploaded VCA image."
            );
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 digest is unavailable.", exception);
        } catch (S3Exception exception) {
            log.error("Failed to store uploaded VCA image (S3)", exception);
            throw new VcaApiException(
                    HttpStatus.BAD_GATEWAY,
                    "UPLOAD_STORAGE_FAILED",
                    "Failed to store the uploaded VCA image in object storage."
            );
        }
    }

    // presignUpload로 직접 업로드된 객체가 예약 당시 크기/sha256과 일치하는지 확인한다.
    // completeImage 호출 시 실행되며, presignUpload 경로에서 유일하게 내용을 검증하는 지점이다.
    @Override
    public void verifyUpload(String objectKey, long expectedSizeBytes, String expectedSha256) {
        HeadObjectResponse response;
        try {
            response = s3Client.headObject(HeadObjectRequest.builder()
                    .bucket(bucket)
                    .key(objectKey)
                    .build());
        } catch (S3Exception exception) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "UPLOAD_NOT_VERIFIED",
                    "The upload object must be verified before completion."
            );
        }
        if (response.contentLength() != expectedSizeBytes) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "UPLOAD_SIZE_MISMATCH",
                    "The completed upload size does not match the reserved image."
            );
        }
        String uploadedSha256 = response.metadata().get("sha256");
        if (!expectedSha256.equalsIgnoreCase(uploadedSha256)) {
            throw new VcaApiException(
                    HttpStatus.CONFLICT,
                    "SHA256_MISMATCH",
                    "The completed upload checksum does not match the reserved image."
            );
        }
    }

    @Override
    public URI presignedDownload(String objectKey) {
        return presignedDownload(objectKey, s3Presigner);
    }

    // vca-ai(백엔드 컨테이너)가 직접 따라갈 다운로드 URL. presignedDownload와 달리
    // internalS3Presigner를 써서, 로컬 MinIO에서는 컨테이너 네트워크 endpoint를
    // 가리키는 URL을 만든다(브라우저용 presignedDownload와 endpoint가 다를 수 있음).
    private URI presignedDownloadForBackend(String objectKey) {
        return presignedDownload(objectKey, internalS3Presigner);
    }

    private URI presignedDownload(String objectKey, S3Presigner presigner) {
        GetObjectPresignRequest presignRequest = GetObjectPresignRequest.builder()
                .signatureDuration(DOWNLOAD_URL_TTL)
                .getObjectRequest(GetObjectRequest.builder()
                        .bucket(bucket)
                        .key(objectKey)
                        .build())
                .build();
        return URI.create(presigner.presignGetObject(presignRequest).url().toString());
    }

    @Override
    public void storeBytes(String objectKey, byte[] bytes, String contentType) {
        try {
            s3Client.putObject(
                    PutObjectRequest.builder()
                            .bucket(bucket)
                            .key(objectKey)
                            .contentType(contentType)
                            .contentLength((long) bytes.length)
                            .build(),
                    RequestBody.fromBytes(bytes)
            );
        } catch (S3Exception exception) {
            log.error("Failed to store generated file (S3)", exception);
            throw new VcaApiException(
                    HttpStatus.BAD_GATEWAY,
                    "UPLOAD_STORAGE_FAILED",
                    "Failed to store the generated file in object storage."
            );
        }
    }

    // run 실행 전, S3에 있는 업로드 이미지 각각의 presigned GET URL을 만든다. vca-ai가 자기 pod의
    // 로컬 임시 디렉터리로 직접 내려받으므로(EFS 등 공유 볼륨 불필요), 여기서는 로컬 디스크에
    // 아무것도 쓰지 않는다.
    @Override
    public VcaSharedStorage.RunInputDirectory materializeRunInput(
            String assessmentRunId,
            List<StoredImageReference> images
    ) {
        try {
            List<VcaSharedStorage.RunInputDirectory.RemoteImage> remoteImages = images.stream()
                    .map(image -> new VcaSharedStorage.RunInputDirectory.RemoteImage(
                            image.imageId() + "-" + VcaImageUploadValidator.safeFileName(image.fileName()),
                            presignedDownloadForBackend(image.objectKey())
                    ))
                    .toList();
            return new VcaSharedStorage.RunInputDirectory(null, remoteImages);
        } catch (S3Exception exception) {
            throw new VcaApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "RUN_INPUT_STORAGE_FAILED",
                    "Failed to prepare VCA assessment input images."
            );
        }
    }

    @Override
    public StoredImageContent read(String objectKey, String fileName, String contentType) {
        try {
            byte[] bytes = s3Client.getObject(
                    GetObjectRequest.builder().bucket(bucket).key(objectKey).build(),
                    ResponseTransformer.toBytes()
            ).asByteArray();
            return new StoredImageContent(fileName, contentType, bytes);
        } catch (S3Exception exception) {
            throw new VcaApiException(
                    HttpStatus.BAD_GATEWAY,
                    "UPLOAD_STORAGE_READ_FAILED",
                    "Failed to read the stored VCA image."
            );
        }
    }

    @Override
    public void delete(String objectKey) {
        try {
            s3Client.deleteObject(DeleteObjectRequest.builder()
                    .bucket(bucket)
                    .key(objectKey)
                    .build());
        } catch (S3Exception exception) {
            throw new VcaApiException(
                    HttpStatus.BAD_GATEWAY,
                    "UPLOAD_STORAGE_DELETE_FAILED",
                    "Failed to delete the stored VCA image."
            );
        }
    }

    // S3 오브젝트 키 레이아웃: {objectPrefix}/{artifactId}/{imageId}/{fileName}.
    private String objectKey(String artifactId, String imageId, String fileName) {
        return objectPrefix + "/" + artifactId + "/" + imageId + "/" + VcaImageUploadValidator.safeFileName(fileName);
    }

    private static String removeTrailingSlash(String value) {
        String result = value;
        while (result.endsWith("/")) {
            result = result.substring(0, result.length() - 1);
        }
        return result;
    }

    private static String removeSlashes(String value) {
        String result = value;
        while (result.startsWith("/")) {
            result = result.substring(1);
        }
        return removeTrailingSlash(result);
    }
}
