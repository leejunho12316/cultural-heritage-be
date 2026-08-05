package com.aivle.conservation_backend.common.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.util.StringUtils;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3Configuration;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;

import java.net.URI;

@Configuration
public class S3Config {

    //S3와 소통할 수 있는 AWSSDK - S3Client
    @Bean
    public S3Client s3Client(@Value("${aws.region}") String region,
                               @Value("${aws.access-key-id}") String accessKeyId,
                               @Value("${aws.secret-access-key}") String secretAccessKey,
                               @Value("${aws.s3.endpoint:}") String endpoint,
                               @Value("${aws.s3.path-style-access-enabled:false}") boolean pathStyleAccessEnabled) {
        var builder = S3Client.builder()
                .region(Region.of(region))
                .credentialsProvider(StaticCredentialsProvider.create(
                        AwsBasicCredentials.create(accessKeyId, secretAccessKey)))
                .serviceConfiguration(S3Configuration.builder()
                        .pathStyleAccessEnabled(pathStyleAccessEnabled)
                        .build());
        if (StringUtils.hasText(endpoint)) {
            builder.endpointOverride(URI.create(endpoint));
        }
        return builder.build();
    }


    @Bean
    public S3Presigner s3Presigner(@Value("${aws.region}") String region,
                                     @Value("${aws.access-key-id}") String accessKeyId,
                                     @Value("${aws.secret-access-key}") String secretAccessKey,
                                     @Value("${aws.s3.endpoint:}") String endpoint,
                                     @Value("${aws.s3.presign-endpoint:${aws.s3.endpoint:}}") String presignEndpoint,
                                     @Value("${aws.s3.path-style-access-enabled:false}") boolean pathStyleAccessEnabled) {
        var builder = S3Presigner.builder()
                .region(Region.of(region))
                .credentialsProvider(StaticCredentialsProvider.create(
                        AwsBasicCredentials.create(accessKeyId, secretAccessKey)))
                .serviceConfiguration(S3Configuration.builder()
                        .pathStyleAccessEnabled(pathStyleAccessEnabled)
                        .build());
        String endpointForPresignedUrls = StringUtils.hasText(presignEndpoint)
                ? presignEndpoint
                : endpoint;
        if (StringUtils.hasText(endpointForPresignedUrls)) {
            builder.endpointOverride(URI.create(endpointForPresignedUrls));
        }
        return builder.build();
    }
}
