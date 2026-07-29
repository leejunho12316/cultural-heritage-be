package com.aivle.conservation_backend.xray_api.client;

import com.aivle.conservation_backend.xray_api.dto.UploadedFile;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchResponse;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.core.io.Resource;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;

import java.time.Duration;
import java.util.List;

/**
 * X-ray 조각 결합(스티칭) AI 서비스 클라이언트.
 *
 * 이상영역 탐지는 XrayAnomalyClient가 담당한다.
 * 도메인별로 나눈 이유는 두 명이 같은 파일을 수정할 때
 * git 충돌이 나는 것을 막기 위함이다.
 *
 * 주의: 결합은 탐지보다 훨씬 오래 걸린다.
 * 조각 수와 설정에 따라 수 분에서 수십 분이다.
 * 그래서 읽기 타임아웃을 탐지와 분리해 별도로 둔다.
 *
 * AI 서비스 쪽 상한(XRAY_STITCH_TIMEOUT, 기본 1800초)보다
 * 이 값이 커야 한다. 반대면 엔진이 도는 중에 Spring이
 * 먼저 끊어져 원인 파악이 어려워진다.
 */
@Component
public class XrayStitchClient {

    private final RestClient restClient;

    public XrayStitchClient(
            @Value("${xray.ai.base-url:http://xray-ai:8000}")
            String baseUrl,

            @Value("${xray.ai.stitch-timeout-seconds:2100}")
            long stitchTimeoutSeconds
    ) {
        SimpleClientHttpRequestFactory factory =
                new SimpleClientHttpRequestFactory();

        factory.setConnectTimeout(
                Duration.ofSeconds(10)
        );

        factory.setReadTimeout(
                Duration.ofSeconds(stitchTimeoutSeconds)
        );

        this.restClient = RestClient.builder()
                .baseUrl(baseUrl)
                .requestFactory(factory)
                .build();
    }

    // ------------------------------------------------------------
    // 조각 결합
    // ------------------------------------------------------------

    /**
     * X-ray 조각들을 결합한다.
     *
     * 응답의 fragments[].transform 은 조각별 2x3 affine 행렬이다.
     * 이 값으로 원본 조각의 탐지 좌표를 결합본 좌표로 투영하면,
     * 결합본에서만 나타난 영역이 실제 손상인지 결합 과정의
     * 인공물인지 구분할 수 있다.
     *
     * @param xrayFiles  X-ray 조각 이미지. 최소 1장
     * @param colorFiles 컬러 기준 이미지. 없으면 null
     * @param artifactId 유물 식별자. 결합 엔진 매핑에 등록된 값
     * @param configName 결합 설정 이름. null이면 AI 서비스 기본값
     */
    public XrayStitchResponse stitch(
            List<UploadedFile> xrayFiles,
            List<UploadedFile> colorFiles,
            String artifactId,
            String configName
    ) {
        MultiValueMap<String, Object> body =
                new LinkedMultiValueMap<>();

        for (UploadedFile file : xrayFiles) {
            body.add("files", toResource(file));
        }

        if (colorFiles != null) {
            for (UploadedFile file : colorFiles) {
                if (file != null && file.content().length > 0) {
                    body.add(
                            "color_files",
                            toResource(file)
                    );
                }
            }
        }

        body.add("artifact_id", artifactId);

        if (configName != null && !configName.isBlank()) {
            body.add("config_name", configName);
        }

        return restClient.post()
                .uri("/stitch/run")
                .contentType(MediaType.MULTIPART_FORM_DATA)
                .body(body)
                .retrieve()
                .body(XrayStitchResponse.class);
    }

    // ------------------------------------------------------------
    // 내부 유틸
    // ------------------------------------------------------------

    /**
     * 업로드 내용을 multipart 요청에 넣을 수 있는
     * Resource로 변환한다.
     *
     * 파일명을 유지해야 AI 응답의 fileName이
     * 원본 이름으로 돌아온다. 결합에서는 이 이름이
     * 변환행렬과 조각을 잇는 키가 되므로 특히 중요하다.
     */
    private Resource toResource(UploadedFile file) {
        final String filename =
                file.fileName() != null
                        ? file.fileName()
                        : "image.jpg";

        return new ByteArrayResource(file.content()) {
            @Override
            public String getFilename() {
                return filename;
            }
        };
    }
}
