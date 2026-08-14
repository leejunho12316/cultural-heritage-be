package com.aivle.conservation_backend.common.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.util.StringUtils;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.AwsCredentialsProvider;
import software.amazon.awssdk.auth.credentials.DefaultCredentialsProvider;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3Configuration;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;

import java.net.URI;

@Configuration
public class S3Config {

    @Bean
    public AwsCredentialsProvider awsCredentialsProvider(
            @Value("${aws.access-key-id:}") String accessKeyId,
            @Value("${aws.secret-access-key:}") String secretAccessKey
    ) {
        if (accessKeyId != null && !accessKeyId.isBlank()
                && secretAccessKey != null && !secretAccessKey.isBlank()) {
            return StaticCredentialsProvider.create(
                    AwsBasicCredentials.create(accessKeyId, secretAccessKey)
            );
        }
        // Local AWS profile, ECS task role, EKS IRSA and EC2 instance role are
        // all supported without storing long-lived keys in application.yaml.
        return DefaultCredentialsProvider.create();
    }

    @Bean
    public S3Client s3Client(
            @Value("${aws.region:ap-northeast-2}") String region,
            AwsCredentialsProvider credentialsProvider,
            @Value("${aws.s3.endpoint:}") String endpoint,
            @Value("${aws.s3.path-style-access-enabled:false}") boolean pathStyleAccessEnabled
    ) {
        var builder = S3Client.builder()
                .region(Region.of(region))
                .credentialsProvider(credentialsProvider)
                .serviceConfiguration(S3Configuration.builder()
                        .pathStyleAccessEnabled(pathStyleAccessEnabled)
                        .build());
        // Local MinIO stand-in for S3 needs an explicit endpoint override;
        // real AWS leaves this blank and talks to the region default.
        if (StringUtils.hasText(endpoint)) {
            builder.endpointOverride(URI.create(endpoint));
        }
        return builder.build();
    }

    // 브라우저(FE)가 직접 따라갈 presigned URL용. 로컬 MinIO는 호스트에서만
    // 닿는 endpoint(localhost)가 컨테이너 네트워크 endpoint와 다르므로 별도로
    // 오버라이드한다. 대부분의 소비자(XrayS3Service, S3PhotoStorageService,
    // VcaS3ImageStorage의 업로드/다운로드 presign)가 한정자 없이 이 빈을 쓰므로
    // @Primary로 지정한다.
    @Primary
    @Bean
    public S3Presigner s3Presigner(
            @Value("${aws.region:ap-northeast-2}") String region,
            AwsCredentialsProvider credentialsProvider,
            @Value("${aws.s3.endpoint:}") String endpoint,
            @Value("${aws.s3.presign-endpoint:${aws.s3.endpoint:}}") String presignEndpoint,
            @Value("${aws.s3.path-style-access-enabled:false}") boolean pathStyleAccessEnabled
    ) {
        var builder = S3Presigner.builder()
                .region(Region.of(region))
                .credentialsProvider(credentialsProvider)
                .serviceConfiguration(S3Configuration.builder()
                        .pathStyleAccessEnabled(pathStyleAccessEnabled)
                        .build());
        // Presigned URLs are followed by the browser, not the backend
        // container, so local MinIO needs a separate host-reachable endpoint
        // (localhost) distinct from the container-network endpoint above.
        String endpointForPresignedUrls = StringUtils.hasText(presignEndpoint)
                ? presignEndpoint
                : endpoint;
        if (StringUtils.hasText(endpointForPresignedUrls)) {
            builder.endpointOverride(URI.create(endpointForPresignedUrls));
        }
        return builder.build();
    }

    // 백엔드 컨테이너(vca-ai)가 직접 따라갈 presigned URL용. 브라우저용
    // presign-endpoint가 아니라 항상 컨테이너 네트워크 endpoint(aws.s3.endpoint)를
    // 쓴다 - 로컬 MinIO에서는 "http://minio:9000"처럼 docker-compose 서비스명으로
    // 다른 컨테이너에서도 닿는 주소, 실제 AWS에서는 endpoint가 비어있어 위
    // s3Presigner와 동일하게 동작한다(오버라이드 없음).
    @Bean
    public S3Presigner internalS3Presigner(
            @Value("${aws.region:ap-northeast-2}") String region,
            AwsCredentialsProvider credentialsProvider,
            @Value("${aws.s3.endpoint:}") String endpoint,
            @Value("${aws.s3.path-style-access-enabled:false}") boolean pathStyleAccessEnabled
    ) {
        var builder = S3Presigner.builder()
                .region(Region.of(region))
                .credentialsProvider(credentialsProvider)
                .serviceConfiguration(S3Configuration.builder()
                        .pathStyleAccessEnabled(pathStyleAccessEnabled)
                        .build());
        if (StringUtils.hasText(endpoint)) {
            builder.endpointOverride(URI.create(endpoint));
        }
        return builder.build();
    }
}
