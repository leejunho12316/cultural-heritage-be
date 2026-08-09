package com.aivle.conservation_backend.common.config;

import org.junit.jupiter.api.Test;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.AwsCredentialsProvider;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.presigner.model.GetObjectPresignRequest;

import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThat;

class S3ConfigTest {

    @Test
    void presignerUsesPublicEndpointWhenInternalMinioEndpointIsNotBrowserReachable() {
        S3Config config = new S3Config();
        AwsCredentialsProvider credentialsProvider = StaticCredentialsProvider.create(
                AwsBasicCredentials.create("minioadmin", "minioadmin-vca-20260805")
        );

        try (var presigner = config.s3Presigner(
                "ap-northeast-2",
                credentialsProvider,
                "http://minio:9000",
                "http://localhost:9000",
                true
        )) {
            var request = GetObjectPresignRequest.builder()
                    .signatureDuration(Duration.ofMinutes(5))
                    .getObjectRequest(GetObjectRequest.builder()
                            .bucket("conservation-local")
                            .key("wetting-photos/example.jpg")
                            .build())
                    .build();

            assertThat(presigner.presignGetObject(request).url().toString())
                    .startsWith("http://localhost:9000/conservation-local/wetting-photos/example.jpg");
        }
    }
}
