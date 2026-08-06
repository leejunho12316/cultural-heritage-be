package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.vca.dto.PresignImageRequest;
import com.aivle.conservation_backend.vca.exception.VcaApiException;
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
import java.io.InputStream;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Map;

@Component
class VcaS3ImageStorage implements VcaImageStorage {

    private static final Duration DOWNLOAD_URL_TTL = Duration.ofMinutes(5);

    private final S3Client s3Client;
    private final S3Presigner s3Presigner;
    private final String bucket;
    private final String objectPrefix;
    private final Path storageRoot;
    private final String containerRoot;

    VcaS3ImageStorage(
            S3Client s3Client,
            S3Presigner s3Presigner,
            @Value("${aws.s3.bucket}") String bucket,
            @Value("${vca.s3.object-prefix:vca/images}") String objectPrefix,
            @Value("${vca.storage.local-root}") String localRoot,
            @Value("${vca.storage.container-root}") String containerRoot
    ) {
        this.s3Client = s3Client;
        this.s3Presigner = s3Presigner;
        this.bucket = bucket;
        this.objectPrefix = removeSlashes(objectPrefix);
        this.storageRoot = Path.of(localRoot).toAbsolutePath().normalize();
        this.containerRoot = removeTrailingSlash(containerRoot);
    }

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

    @Override
    public StoredImage storeUpload(String artifactId, String imageId, MultipartFile file) {
        VcaImageUploadValidator.validateUpload(file);
        String fileName = VcaImageUploadValidator.safeFileName(file.getOriginalFilename());
        String objectKey = objectKey(artifactId, imageId, fileName);
        try {
            String sha256 = VcaImageUploadValidator.uploadSha256(file);
            try (InputStream input = VcaImageUploadValidator.verifiedImageInput(file)) {
                s3Client.putObject(
                        PutObjectRequest.builder()
                                .bucket(bucket)
                                .key(objectKey)
                                .contentType(file.getContentType())
                                .contentLength(file.getSize())
                                .metadata(Map.of("sha256", sha256))
                                .build(),
                        RequestBody.fromInputStream(input, file.getSize())
                );
            }
            return new StoredImage(fileName, file.getContentType(), file.getSize(), sha256, objectKey);
        } catch (IOException exception) {
            throw new VcaApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "UPLOAD_STORAGE_FAILED",
                    "Failed to store the uploaded VCA image."
            );
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 digest is unavailable.", exception);
        } catch (S3Exception exception) {
            throw new VcaApiException(
                    HttpStatus.BAD_GATEWAY,
                    "UPLOAD_STORAGE_FAILED",
                    "Failed to store the uploaded VCA image in object storage."
            );
        }
    }

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
        GetObjectPresignRequest presignRequest = GetObjectPresignRequest.builder()
                .signatureDuration(DOWNLOAD_URL_TTL)
                .getObjectRequest(GetObjectRequest.builder()
                        .bucket(bucket)
                        .key(objectKey)
                        .build())
                .build();
        return URI.create(s3Presigner.presignGetObject(presignRequest).url().toString());
    }

    @Override
    public VcaSharedStorage.RunInputDirectory materializeRunInput(
            String assessmentRunId,
            List<StoredImageReference> images
    ) {
        Path runDirectory = storageRoot.resolve(assessmentRunId).normalize();
        requireChild(runDirectory, storageRoot, "Invalid VCA run directory path.");
        Path inputDirectory = runDirectory.resolve("input").normalize();
        requireChild(inputDirectory, runDirectory, "Invalid VCA run input path.");
        try {
            deleteRecursively(runDirectory);
            Files.createDirectories(inputDirectory);
            for (StoredImageReference image : images) {
                Path targetFile = inputDirectory
                        .resolve(image.imageId() + "-" + VcaImageUploadValidator.safeFileName(image.fileName()))
                        .normalize();
                requireChild(targetFile, inputDirectory, "Invalid VCA run input file path.");
                s3Client.getObject(
                        GetObjectRequest.builder().bucket(bucket).key(image.objectKey()).build(),
                        ResponseTransformer.toFile(targetFile)
                );
            }
        } catch (IOException | S3Exception exception) {
            throw new VcaApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "RUN_INPUT_STORAGE_FAILED",
                    "Failed to prepare VCA assessment input images."
            );
        }
        return new VcaSharedStorage.RunInputDirectory(containerRoot + "/" + assessmentRunId + "/input");
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

    private String objectKey(String artifactId, String imageId, String fileName) {
        return objectPrefix + "/" + artifactId + "/" + imageId + "/" + VcaImageUploadValidator.safeFileName(fileName);
    }

    private static void requireChild(Path path, Path parent, String message) {
        if (!path.getParent().equals(parent.normalize())) {
            throw new VcaApiException(HttpStatus.BAD_REQUEST, "VALIDATION_ERROR", message);
        }
    }

    private static void deleteRecursively(Path directory) throws IOException {
        if (!Files.exists(directory)) {
            return;
        }
        try (var paths = Files.walk(directory)) {
            for (Path path : paths.sorted(Comparator.reverseOrder()).toList()) {
                Files.deleteIfExists(path);
            }
        }
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
