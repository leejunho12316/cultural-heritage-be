package com.aivle.conservation_backend.common.config;

import com.aivle.conservation_backend.common.config.jwt.JwtAuthenticationFilter;
import com.aivle.conservation_backend.user.service.UserDetailService;
import lombok.RequiredArgsConstructor;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configuration.WebSecurityCustomizer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

@Configuration
@EnableWebSecurity
@RequiredArgsConstructor
public class WebSecurityConfig {

    private final JwtAuthenticationFilter jwtAuthenticationFilter;


    @Bean
    public SecurityFilterChain securityFilterChain(
            HttpSecurity http
    ) throws Exception {

        return http
                .csrf(csrf -> csrf.disable())

                .formLogin(form -> form.disable())

                .httpBasic(basic -> basic.disable())

                .sessionManagement(session ->
                        session.sessionCreationPolicy(
                                SessionCreationPolicy.STATELESS
                        )
                )

                .authorizeHttpRequests(auth -> auth
                        // 공지사항
                        .requestMatchers(
                                HttpMethod.POST,
                                "/api/notices"
                        ).hasRole("ADMIN")

                        .requestMatchers(
                                HttpMethod.PUT,
                                "/api/notices/**"
                        ).hasRole("ADMIN")

                        .requestMatchers(
                                HttpMethod.DELETE,
                                "/api/notices/**"
                        ).hasRole("ADMIN")

                        // 회원 탈퇴는 로그인 필요
                        .requestMatchers(
                                HttpMethod.DELETE,
                                "/api/users/me"
                        ).authenticated()

                        // 내 게시물 조회: 로그인 필수
                        .requestMatchers(
                                HttpMethod.GET,
                                "/api/posts/me"
                        ).authenticated()

                        // 일반 게시글 조회·검색: 공개
                        .requestMatchers(
                                HttpMethod.GET,
                                "/api/posts/**"
                        ).permitAll()

                        // 게시글 작성·수정·삭제: 로그인 필수
                        .requestMatchers(
                                HttpMethod.POST,
                                "/api/posts"
                        ).authenticated()

                        .requestMatchers(
                                HttpMethod.PUT,
                                "/api/posts/**"
                        ).authenticated()

                        .requestMatchers(
                                HttpMethod.DELETE,
                                "/api/posts/**"
                        ).authenticated()

                        .anyRequest().permitAll()
                )

                .addFilterBefore(
                        jwtAuthenticationFilter,
                        UsernamePasswordAuthenticationFilter.class
                )

                .build();


    }

    @Bean
    public BCryptPasswordEncoder bCryptPasswordEncoder(){
        return new BCryptPasswordEncoder();
    }
}
