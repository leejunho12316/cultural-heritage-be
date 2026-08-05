package com.aivle.conservation_backend.vca;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.MediaType;
import org.springframework.http.HttpMethod;
import org.springframework.web.servlet.HandlerInterceptor;

import java.io.IOException;

final class VcaAccessTokenInterceptor implements HandlerInterceptor {

    private static final String HEADER = "X-VCA-Access-Token";

    private final String accessToken;

    VcaAccessTokenInterceptor(String accessToken) {
        this.accessToken = accessToken == null ? "" : accessToken.trim();
    }

    @Override
    public boolean preHandle(
            HttpServletRequest request,
            HttpServletResponse response,
            Object handler
    ) throws IOException {
        if (accessToken.isEmpty() || HttpMethod.OPTIONS.matches(request.getMethod())) {
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

    private boolean acceptsMediaQueryToken(HttpServletRequest request) {
        return HttpMethod.GET.matches(request.getMethod())
                && isMediaGatewayPath(request.getRequestURI())
                && accessToken.equals(request.getParameter("vca_access_token"));
    }

    private boolean isMediaGatewayPath(String path) {
        return path.matches("^/api/vca/[^/]+/files/sha256/[A-Fa-f0-9]{64}$")
                || path.matches("^/api/vca/[^/]+/report-pdf-jobs/[^/]+/download$");
    }
}
