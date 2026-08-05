package com.aivle.conservation_backend.vca;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class VcaWebConfig implements WebMvcConfigurer {

    private final String accessToken;

    public VcaWebConfig(@Value("${vca.access-token:}") String accessToken) {
        this.accessToken = accessToken;
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new VcaAccessTokenInterceptor(accessToken))
                .addPathPatterns("/api/vca/**");
    }
}
