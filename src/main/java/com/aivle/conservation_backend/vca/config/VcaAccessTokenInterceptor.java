package com.aivle.conservation_backend.vca.config;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.MediaType;
import org.springframework.http.HttpMethod;
import org.springframework.web.servlet.HandlerInterceptor;

import java.io.IOException;

public final class VcaAccessTokenInterceptor implements HandlerInterceptor {

    private static final String HEADER = "X-VCA-Access-Token";

    private final String accessToken;

    public VcaAccessTokenInterceptor(String accessToken) {
        this.accessToken = accessToken == null ? "" : accessToken.trim();
        if (this.accessToken.isEmpty()) {
            throw new IllegalStateException("VCA access token must be configured.");
        }
    }

    // /api/vca/** 전체에 걸리는 인터셉터 진입점(VcaWebConfig에서 등록).
    // 헤더 토큰 또는 미디어용 쿼리 토큰이 일치해야 다음 핸들러로 통과시킨다.
    @Override
    public boolean preHandle(
            HttpServletRequest request,
            HttpServletResponse response,
            Object handler
    ) throws IOException {
        if (HttpMethod.OPTIONS.matches(request.getMethod())) {
            return true;
        }
        if (accessToken.equals(request.getHeader(HEADER)) || acceptsMediaQueryToken(request)) {
            return true;
        }
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getWriter().write("{\"error\":{\"code\":\"VCA_UNAUTHORIZED\","
                + "\"message\":\"A valid VCA access token is required.\"}}"
        );
        return false;
    }

    // 브라우저가 소비하는 미디어(<img src>, PDF 다운로드 링크 등)는 X-VCA-Access-Token 헤더를
    // 붙일 수 없으므로, 이 두 GET 전용 경로에 한해 쿼리 파라미터로 토큰을 받는 것을 허용한다.
    // 의도적으로 범위를 좁혀둔 예외이니 다른 메서드/경로로 넓히지 말 것.
    private boolean acceptsMediaQueryToken(HttpServletRequest request) {
        return HttpMethod.GET.matches(request.getMethod())
                && isMediaGatewayPath(request.getRequestURI())
                && accessToken.equals(request.getParameter("vca_access_token"));
    }

    // acceptsMediaQueryToken이 쿼리 토큰을 허용할 경로인지 판별하는 화이트리스트.
    // 이미지 파일 다운로드/리포트 PDF 다운로드 두 경로만 포함한다.
    private boolean isMediaGatewayPath(String path) {
        return path.matches("^/api/vca/[^/]+/files/sha256/[A-Fa-f0-9]{64}$")
                || path.matches("^/api/vca/[^/]+/report-pdf-jobs/[^/]+/download$");
    }
}
