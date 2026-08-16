package com.aivle.conservation_backend.common.config.jwt;

import com.aivle.conservation_backend.user.service.UserDetailService;
import io.jsonwebtoken.JwtException;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContext;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.security.web.authentication.WebAuthenticationDetailsSource;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

@Component
@RequiredArgsConstructor
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private static final String AUTHORIZATION_HEADER = "Authorization";
    private static final String BEARER_PREFIX = "Bearer ";

    private final JwtTokenProvider jwtTokenProvider;
    private final UserDetailService userDetailService;

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain
    ) throws ServletException, IOException {

        try {
            String token = resolveToken(request);

            // 토큰이 존재하고 아직 인증되지 않은 요청만 인증 처리
            if (token != null
                    && SecurityContextHolder.getContext().getAuthentication() == null
                    && jwtTokenProvider.validateToken(token)) {

                String loginId = jwtTokenProvider.getLoginId(token);

                UserDetails userDetails =
                        userDetailService.loadUserByUsername(loginId);

                UsernamePasswordAuthenticationToken authentication =
                        new UsernamePasswordAuthenticationToken(
                                userDetails,
                                null,
                                userDetails.getAuthorities()
                        );

                authentication.setDetails(
                        new WebAuthenticationDetailsSource()
                                .buildDetails(request)
                );

                SecurityContext securityContext =
                        SecurityContextHolder.createEmptyContext();

                securityContext.setAuthentication(authentication);
                SecurityContextHolder.setContext(securityContext);
            }

        } catch (
                JwtException
                | UsernameNotFoundException
                | IllegalArgumentException e
        ) {
            SecurityContextHolder.clearContext();
        }

        filterChain.doFilter(request, response);
    }

    private String resolveToken(HttpServletRequest request) {
        String authorizationHeader =
                request.getHeader(AUTHORIZATION_HEADER);

        if (authorizationHeader != null && authorizationHeader.startsWith(BEARER_PREFIX)) {
            String token = authorizationHeader
                    .substring(BEARER_PREFIX.length())
                    .trim();
            if (!token.isBlank()) {
                return token;
            }
        }

        // <img src>/PDF 다운로드 링크는 Authorization 헤더를 붙일 수 없다 -
        // VcaAccessTokenInterceptor가 X-VCA-Access-Token에 대해 이미 쓰고 있는
        // "이 두 GET 미디어 경로에 한해 쿼리 파라미터 허용" 패턴을 JWT에도
        // 그대로 적용한다. 의도적으로 범위를 좁혀둔 예외이니 다른 경로로
        // 넓히지 말 것.
        if (isMediaGatewayQueryTokenEligible(request)) {
            String queryToken = request.getParameter("access_token");
            if (queryToken != null && !queryToken.isBlank()) {
                return queryToken;
            }
        }

        return null;
    }

    // VcaAccessTokenInterceptor.isMediaGatewayPath와 동일한 화이트리스트(이미지
    // 파일 다운로드/리포트 PDF 다운로드 두 GET 경로만) - 두 인증 레이어가
    // 어긋나지 않도록 같은 경로 집합을 그대로 맞춘다.
    private boolean isMediaGatewayQueryTokenEligible(HttpServletRequest request) {
        if (!"GET".equalsIgnoreCase(request.getMethod())) {
            return false;
        }
        String path = request.getRequestURI();
        return path.matches("^/api/vca/[^/]+/files/sha256/[A-Fa-f0-9]{64}$")
                || path.matches("^/api/vca/[^/]+/report-pdf-jobs/[^/]+/download$");
    }
}