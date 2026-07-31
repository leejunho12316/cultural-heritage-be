package com.aivle.conservation_backend.common.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

// 로컬 개발용 CORS 허용 설정.
// FE(Vite, 기본 5173번 포트)가 BE(8080번 포트)를 브라우저에서 직접 호출하면
// 포트가 다르기 때문에 브라우저가 기본적으로 요청을 막는다(CORS).
// 이 설정이 없으면 FE 콘솔에 "blocked by CORS policy" 에러가 뜬다.
//
// 지금은 로컬 개발 단계라 5173/3000 등 흔한 dev 포트를 다 열어뒀는데,
// 실제 배포 시에는 allowedOrigins를 배포된 FE 도메인으로 좁혀야 한다.
@Configuration
public class WebConfig implements WebMvcConfigurer {

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/**")
                .allowedOrigins("http://localhost:5173", "http://localhost:3000","http://localhost:5174")
                .allowedMethods("GET", "POST", "PUT", "DELETE", "OPTIONS")
                .allowedHeaders("*");
    }
}
