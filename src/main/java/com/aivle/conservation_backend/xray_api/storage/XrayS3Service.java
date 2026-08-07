package com.aivle.conservation_backend.xray_api.storage;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import software.amazon.awssdk.core.ResponseBytes;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.model.GetObjectResponse;
import software.amazon.awssdk.services.s3.model.HeadObjectRequest;
import software.amazon.awssdk.services.s3.model.ListObjectsV2Request;
import software.amazon.awssdk.services.s3.model.ListObjectsV2Response;
import software.amazon.awssdk.services.s3.model.NoSuchKeyException;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.model.S3Exception;
import software.amazon.awssdk.services.s3.model.S3Object;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.services.s3.presigner.model.GetObjectPresignRequest;
import software.amazon.awssdk.services.s3.presigner.model.PresignedGetObjectRequest;
import software.amazon.awssdk.services.s3.presigner.model.PresignedPutObjectRequest;
import software.amazon.awssdk.services.s3.presigner.model.PutObjectPresignRequest;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

@Service
public class XrayS3Service {

    private final S3Client s3Client;
    private final S3Presigner presigner;
    private final String bucket;
    private final Duration inputPutDuration;
    private final Duration outputPutDuration;
    private final Duration getDuration;

    public XrayS3Service(
            S3Client s3Client,
            S3Presigner presigner,
            @Value("${aws.s3.bucket:}") String bucket,
            @Value("${xray.s3.presign-input-minutes:30}") long inputPutMinutes,
            @Value("${xray.s3.presign-output-minutes:120}") long outputPutMinutes,
            @Value("${xray.s3.presign-get-minutes:120}") long getMinutes
    ) {
        this.s3Client = s3Client;
        this.presigner = presigner;
        this.bucket = bucket;
        this.inputPutDuration = Duration.ofMinutes(inputPutMinutes);
        this.outputPutDuration = Duration.ofMinutes(outputPutMinutes);
        this.getDuration = Duration.ofMinutes(getMinutes);
    }

    public String presignInputPut(String key, String contentType) {
        return presignPut(key, contentType, inputPutDuration);
    }

    public String presignOutputPut(String key, String contentType) {
        return presignPut(key, contentType, outputPutDuration);
    }

    public String presignGet(String key) {
        requireBucket();
        GetObjectRequest objectRequest = GetObjectRequest.builder()
                .bucket(bucket)
                .key(key)
                .build();
        PresignedGetObjectRequest request = presigner.presignGetObject(
                GetObjectPresignRequest.builder()
                        .signatureDuration(getDuration)
                        .getObjectRequest(objectRequest)
                        .build()
        );
        return request.url().toString();
    }

    public void putBytes(String key, byte[] bytes, String contentType) {
        requireBucket();
        PutObjectRequest.Builder builder = PutObjectRequest.builder()
                .bucket(bucket)
                .key(key);
        if (contentType != null && !contentType.isBlank()) {
            builder.contentType(contentType);
        }
        s3Client.putObject(builder.build(), RequestBody.fromBytes(bytes));
    }

    public void putString(String key, String value, String contentType) {
        putBytes(key, value.getBytes(StandardCharsets.UTF_8), contentType);
    }

    public byte[] getBytes(String key) {
        requireBucket();
        ResponseBytes<GetObjectResponse> bytes = s3Client.getObjectAsBytes(
                GetObjectRequest.builder().bucket(bucket).key(key).build()
        );
        return bytes.asByteArray();
    }

    public String getString(String key) {
        return new String(getBytes(key), StandardCharsets.UTF_8);
    }

    public boolean objectExists(String key) {
        requireBucket();
        try {
            s3Client.headObject(HeadObjectRequest.builder()
                    .bucket(bucket)
                    .key(key)
                    .build());
            return true;
        } catch (NoSuchKeyException e) {
            return false;
        } catch (S3Exception e) {
            if (e.statusCode() == 404) {
                return false;
            }
            throw e;
        }
    }

    public List<String> listKeys(String prefix) {
        requireBucket();
        List<String> keys = new ArrayList<>();
        String continuationToken = null;
        do {
            ListObjectsV2Response response = s3Client.listObjectsV2(
                    ListObjectsV2Request.builder()
                            .bucket(bucket)
                            .prefix(prefix)
                            .continuationToken(continuationToken)
                            .build()
            );
            for (S3Object object : response.contents()) {
                keys.add(object.key());
            }
            continuationToken = response.isTruncated() ? response.nextContinuationToken() : null;
        } while (continuationToken != null);
        return keys;
    }

    public String bucket() {
        requireBucket();
        return bucket;
    }

    private String presignPut(String key, String contentType, Duration duration) {
        requireBucket();
        PutObjectRequest.Builder objectBuilder = PutObjectRequest.builder()
                .bucket(bucket)
                .key(key);
        if (contentType != null && !contentType.isBlank()) {
            objectBuilder.contentType(contentType);
        }
        PresignedPutObjectRequest request = presigner.presignPutObject(
                PutObjectPresignRequest.builder()
                        .signatureDuration(duration)
                        .putObjectRequest(objectBuilder.build())
                        .build()
        );
        return request.url().toString();
    }

    private void requireBucket() {
        if (bucket == null || bucket.isBlank()) {
            throw new IllegalStateException("AWS_S3_BUCKET is not configured.");
        }
    }
}
