package com.aivle.conservation_backend.common.config;


import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestClient;


//Docker로 띄운 AI Service 들에 HTTP Rest 요청 보내기 위한 Configuration
@Configuration
public class RestClientConfig {

    @Bean
    public RestClient conservationGuideAiRestClient(@Value("${conservation-guide-ai.base-url}") String baseUrl){ //@Value : application.yaml에서 값 가져옴.
        return RestClient.builder()
                .baseUrl(baseUrl)
                .build();
    }

}
