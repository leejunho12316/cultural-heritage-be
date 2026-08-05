package com.aivle.conservation_backend.common.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

import java.time.Duration;

@Configuration
public class RestClientConfig {

    @Bean
    public RestClient conservationGuideAiRestClient(@Value("${conservation-guide-ai.base-url}") String baseUrl){ //@Value : application.yaml에서 값 가져옴.

        return RestClient.builder()
                .baseUrl(baseUrl)
                // 기본 요청 팩토리(JDK HttpClient)가 h2c 업그레이드를 시도하다 POST 바디를 유실하는 문제가 있어 HTTP/1.1만 사용하는 SimpleClientHttpRequestFactory로 명시적으로 고정.
                .requestFactory(new SimpleClientHttpRequestFactory())
                .build();
    }

    @Bean
    public RestClient vcaAiRestClient(
            @Value("${vca.ai.base-url}") String baseUrl,
            @Value("${vca.ai.timeout-seconds}") long timeoutSeconds
    ) {
        SimpleClientHttpRequestFactory requestFactory =
                new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(Duration.ofSeconds(10));
        requestFactory.setReadTimeout(Duration.ofSeconds(timeoutSeconds));

        return RestClient.builder()
                .baseUrl(baseUrl)
                .requestFactory(requestFactory)
                .build();
    }

    @Bean
    public RestClient.Builder restClientBuilder() {
        return RestClient.builder();
    }
}
