package com.aivle.conservation_backend.xray_api.client;

import com.aivle.conservation_backend.xray_api.dto.AnalysisTarget;
import com.aivle.conservation_backend.xray_api.dto.XrayDetectionResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayUrlDetectionRequest;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.core.io.Resource;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.time.Duration;
import java.util.List;

/**
 * X-ray 이상영역 탐지 AI 서비스 클라이언트.
 *
 * 조각 결합(스티칭)은 XrayStitchClient가 담당한다.
 * 도메인별로 나눈 이유는 두 명이 같은 파일을 수정할 때
 * git 충돌이 나는 것을 막기 위함이다.
 *
 * WebClient 대신 RestClient를 사용한다.
 * spring-boot-starter-webmvc에 이미 포함되어 있어
 * webflux 의존성을 추가할 필요가 없고, 동기 호출이라
 * 코드가 단순하다.
 *
 * 주의: CPU 추론이라 응답이 느리다.
 * 결합본 1장 5~10초, 조각 30장 1~2분.
 * 호출부에서 비동기 job으로 감싸는 것을 권장한다.
 */
@Component
public class XrayAnomalyClient {

    private final RestClient restClient;

    public XrayAnomalyClient(
            @Value("${xray.ai.base-url:http://xray-ai:8000}")
            String baseUrl,

            @Value("${xray.ai.timeout-seconds:300}")
            long timeoutSeconds
    ) {
        SimpleClientHttpRequestFactory factory =
                new SimpleClientHttpRequestFactory();

        factory.setConnectTimeout(
                Duration.ofSeconds(10)
        );

        factory.setReadTimeout(
                Duration.ofSeconds(timeoutSeconds)
        );

        this.restClient = RestClient.builder()
                .baseUrl(baseUrl)
                .requestFactory(factory)
                .build();
    }

    // ------------------------------------------------------------
    // 헬스체크
    // ------------------------------------------------------------

    /**
     * AI 서비스 상태를 그대로 반환한다.
     *
     * llmEnabled는 프론트에서 문안 생성 버튼 활성화 여부를
     * 판단하는 데 쓰이므로 값을 통과시켜야 한다.
     */
    public HealthResponse getHealth() {
        try {
            HealthResponse response = restClient.get()
                    .uri("/health")
                    .retrieve()
                    .body(HealthResponse.class);

            return response != null
                    ? response
                    : unavailable();

        } catch (RestClientException e) {
            return unavailable();
        }
    }

    private HealthResponse unavailable() {
        return new HealthResponse(
                "unavailable", false, null, false
        );
    }

    /**
     * 탐지 요청을 보낼 수 있는 상태인지 확인한다.
     */
    public boolean isHealthy() {
        HealthResponse r = getHealth();

        return "ok".equals(r.status())
                && Boolean.TRUE.equals(r.modelLoaded());
    }

    public record HealthResponse(
            String status,
            Boolean modelLoaded,
            String device,
            Boolean llmEnabled
    ) {
    }

    // ------------------------------------------------------------
    // 단일 이미지 탐지
    // ------------------------------------------------------------

    /**
     * 이미지 한 장에서 이상영역을 탐지한다.
     *
     * @param file            업로드된 X-ray 이미지
     * @param target          분석 대상 (해상도가 달라진다)
     * @return 탐지 결과
     */
    public XrayDetectionResponse detect(
            MultipartFile file,
            AnalysisTarget target
    ) {
        return detect(file, target, null);
    }

    /**
     * @param confidence 신뢰도 임계값. null이면 AI 서비스 기본값(0.08)
     */
    public XrayDetectionResponse detect(
            MultipartFile file,
            AnalysisTarget target,
            Double confidence
    ) {
        MultiValueMap<String, Object> body =
                new LinkedMultiValueMap<>();

        body.add("file", toResource(file));
        body.add("analysis_target", target.getValue());

        if (confidence != null) {
            body.add(
                    "confidence",
                    String.valueOf(confidence)
            );
        }

        return restClient.post()
                .uri("/detect")
                .contentType(MediaType.MULTIPART_FORM_DATA)
                .body(body)
                .retrieve()
                .body(XrayDetectionResponse.class);
    }

    /**
     * 서버에 저장된 최종 결합본 Resource를 직접 분석한다.
     */
    public XrayDetectionResponse detect(
            Resource file,
            AnalysisTarget target,
            Double confidence
    ) {
        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        body.add("file", file);
        body.add("analysis_target", target.getValue());
        if (confidence != null) {
            body.add("confidence", String.valueOf(confidence));
        }

        return restClient.post()
                .uri("/detect")
                .contentType(MediaType.MULTIPART_FORM_DATA)
                .body(body)
                .retrieve()
                .body(XrayDetectionResponse.class);
    }

    /** S3 presigned GET URL의 이미지를 FastAPI가 직접 내려받아 분석한다. */
    public XrayDetectionResponse detectUrl(
            String fileName,
            String downloadUrl,
            AnalysisTarget target,
            Double confidence,
            Integer sourceIndex
    ) {
        XrayUrlDetectionRequest.Single body = new XrayUrlDetectionRequest.Single(
                fileName,
                downloadUrl,
                target.getValue(),
                confidence,
                sourceIndex
        );
        return restClient.post()
                .uri("/detect-url")
                .contentType(MediaType.APPLICATION_JSON)
                .body(body)
                .retrieve()
                .body(XrayDetectionResponse.class);
    }

    // ------------------------------------------------------------
    // 여러 이미지 일괄 탐지
    // ------------------------------------------------------------

    /**
     * 조각 여러 장을 한 번에 처리한다.
     *
     * 30장이면 1~2분이 걸리므로 동기 호출 시
     * HTTP 타임아웃에 주의해야 한다.
     */
    public XrayDetectionResponse detectBatch(
            List<MultipartFile> files,
            List<Integer> sourceIndexes,
            AnalysisTarget target,
            Double confidence
    ) {
        if (files.size() != sourceIndexes.size()) {
            throw new IllegalArgumentException(
                    "files and sourceIndexes size mismatch: "
                            + files.size() + " != " + sourceIndexes.size()
            );
        }

        MultiValueMap<String, Object> body =
                new LinkedMultiValueMap<>();

        for (int i = 0; i < files.size(); i++) {
            MultipartFile file = files.get(i);
            Integer sourceIndex = sourceIndexes.get(i);

            System.out.println(
                    "### ORIGINAL FILENAME = " + file.getOriginalFilename()
                            + ", SOURCE_INDEX = " + sourceIndex
            );

            body.add("files", toResource(file));
            body.add("source_indexes", String.valueOf(sourceIndex));
        }

        body.add("analysis_target", target.getValue());

        if (confidence != null) {
            body.add(
                    "confidence",
                    String.valueOf(confidence)
            );
        }

        return restClient.post()
                .uri("/detect-batch")
                .contentType(MediaType.MULTIPART_FORM_DATA)
                .body(body)
                .retrieve()
                .body(XrayDetectionResponse.class);
    }

    /**
     * 결합 job에 저장된 원본 X-ray Resource들을 sourceIndex와 함께 분석한다.
     */
    public XrayDetectionResponse detectBatchResources(
            List<Resource> files,
            List<Integer> sourceIndexes,
            AnalysisTarget target,
            Double confidence
    ) {
        if (files.size() != sourceIndexes.size()) {
            throw new IllegalArgumentException(
                    "files and sourceIndexes size mismatch: "
                            + files.size() + " != " + sourceIndexes.size()
            );
        }

        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        for (int i = 0; i < files.size(); i++) {
            body.add("files", files.get(i));
            body.add("source_indexes", String.valueOf(sourceIndexes.get(i)));
        }
        body.add("analysis_target", target.getValue());
        if (confidence != null) {
            body.add("confidence", String.valueOf(confidence));
        }

        return restClient.post()
                .uri("/detect-batch")
                .contentType(MediaType.MULTIPART_FORM_DATA)
                .body(body)
                .retrieve()
                .body(XrayDetectionResponse.class);
    }

    /** 여러 S3 presigned GET URL을 sourceIndex 순서와 함께 분석한다. */
    public XrayDetectionResponse detectBatchUrls(
            List<String> fileNames,
            List<String> downloadUrls,
            List<Integer> sourceIndexes,
            AnalysisTarget target,
            Double confidence
    ) {
        if (fileNames.size() != downloadUrls.size()
                || fileNames.size() != sourceIndexes.size()) {
            throw new IllegalArgumentException("URL detection input size mismatch.");
        }
        List<XrayUrlDetectionRequest.Single> files = java.util.stream.IntStream
                .range(0, fileNames.size())
                .mapToObj(index -> new XrayUrlDetectionRequest.Single(
                        fileNames.get(index),
                        downloadUrls.get(index),
                        target.getValue(),
                        confidence,
                        sourceIndexes.get(index)
                ))
                .toList();
        XrayUrlDetectionRequest.Batch body = new XrayUrlDetectionRequest.Batch(
                files,
                target.getValue(),
                confidence
        );
        return restClient.post()
                .uri("/detect-batch-urls")
                .contentType(MediaType.APPLICATION_JSON)
                .body(body)
                .retrieve()
                .body(XrayDetectionResponse.class);
    }

    // ------------------------------------------------------------
    // 상태조사 문안 생성
    // ------------------------------------------------------------

    /**
     * 전문가용 1차 상태조사 문안을 생성한다.
     *
     * 탐지 좌표만으로는 GPT가 영상을 볼 수 없으므로
     * 이미지를 다시 전달한다. AI 서비스가 박스를 그려
     * 원본과 함께 OpenAI에 보낸다.
     *
     * 30초~2분 걸린다. AI 서비스에 OPENAI_API_KEY가
     * 설정되어 있지 않으면 503을 반환한다.
     *
     * @param regionsJson 검수 반영된 영역 목록 JSON 문자열
     * @param reportStyle summary(PPT용 요약) 또는 detailed(기록용 상세)
     */
    public String generateReport(
            String regionsJson,
            String artifactType,
            String material,
            String reportStyle,
            MultipartFile assembled,
            List<MultipartFile> fragments,
            List<MultipartFile> rgbImages
    ) {
        MultiValueMap<String, Object> body =
                new LinkedMultiValueMap<>();

        body.add("regions", regionsJson);
        body.add(
                "report_style",
                reportStyle != null && !reportStyle.isBlank()
                        ? reportStyle
                        : "summary"
        );
        body.add(
                "artifact_type",
                artifactType != null ? artifactType : ""
        );
        body.add(
                "material",
                material != null ? material : ""
        );

        if (assembled != null && !assembled.isEmpty()) {
            body.add("assembled", toResource(assembled));
        }

        // 요청 크기 제한으로 AI 서비스도 6장까지만 사용한다
        if (fragments != null) {
            fragments.stream()
                    .limit(6)
                    .forEach(f ->
                            body.add(
                                    "fragments",
                                    toResource(f)
                            )
                    );
        }

        if (rgbImages != null) {
            rgbImages.stream()
                    .limit(6)
                    .forEach(f ->
                            body.add(
                                    "rgb_images",
                                    toResource(f)
                            )
                    );
        }

        return restClient.post()
                .uri("/report")
                .contentType(MediaType.MULTIPART_FORM_DATA)
                .body(body)
                .retrieve()
                .body(String.class);
    }

    // ------------------------------------------------------------
    // 내부 유틸
    // ------------------------------------------------------------

    /**
     * MultipartFile을 multipart 요청에 넣을 수 있는
     * Resource로 변환한다.
     *
     * 파일명을 유지해야 AI 응답의 fileName이
     * 원본 이름으로 돌아온다.
     */
    private Resource toResource(MultipartFile file) {
        try {
            final String filename =
                    file.getOriginalFilename() != null
                            ? file.getOriginalFilename()
                            : "image.jpg";

            return new ByteArrayResource(file.getBytes()) {
                @Override
                public String getFilename() {
                    return filename;
                }
            };

        } catch (IOException e) {
            throw new IllegalStateException(
                    "파일을 읽을 수 없습니다: "
                            + file.getOriginalFilename(),
                    e
            );
        }
    }
}
