package com.aivle.conservation_backend.pottery_inspection_ai.client;

import com.aivle.conservation_backend.pottery_inspection_ai.dto.PotteryInspectionResponseDto;
import lombok.RequiredArgsConstructor;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.util.Map;

@RequiredArgsConstructor
@Service
public class PotteryInspectionAiClient {

    private final RestClient potteryInspectionAiRestClient;

    public PotteryInspectionResponseDto inspect(
            MultipartFile image, int nCalls, boolean useVlmPattern, boolean treatAsSingleArtifact
    ) {
        return potteryInspectionAiRestClient.post()
                .uri(uriBuilder -> uriBuilder
                        .path("/inspect")
                        .queryParam("n_calls", nCalls)
                        .queryParam("use_vlm_pattern", useVlmPattern)
                        .queryParam("treat_as_single_artifact", treatAsSingleArtifact)
                        .build())
                .contentType(MediaType.MULTIPART_FORM_DATA)
                .body(toBody(image))
                .retrieve()
                .body(PotteryInspectionResponseDto.class);
    }

    /**
     * 분석을 백그라운드로 접수하고 즉시 반환한다({job_id, status}).
     * 문양이 여러 개면 확정된 문양별 상태조사까지 순차로 이어져서 60초를
     * 넘길 수 있는데, 그동안 ALB가 연결을 붙들고 있으면 유휴 타임아웃(기본
     * 60초)에 걸려 504가 났다 - 접수만 하고 폴링으로 결과를 받으면 개별
     * 요청은 항상 짧게 끝나서 이 문제를 피할 수 있다.
     */
    public Map<String, Object> createInspectionJob(
            MultipartFile image, int nCalls, boolean useVlmPattern, boolean treatAsSingleArtifact
    ) {
        return potteryInspectionAiRestClient.post()
                .uri(uriBuilder -> uriBuilder
                        .path("/inspect/jobs")
                        .queryParam("n_calls", nCalls)
                        .queryParam("use_vlm_pattern", useVlmPattern)
                        .queryParam("treat_as_single_artifact", treatAsSingleArtifact)
                        .build())
                .contentType(MediaType.MULTIPART_FORM_DATA)
                .body(toBody(image))
                .retrieve()
                .body(new ParameterizedTypeReference<Map<String, Object>>() {});
    }

    /** job 상태를 폴링한다. FE가 보통 1~2초 간격으로 반복 호출한다. */
    public Map<String, Object> getInspectionJob(String jobId) {
        return potteryInspectionAiRestClient.get()
                .uri("/inspect/jobs/{jobId}", jobId)
                .retrieve()
                .body(new ParameterizedTypeReference<Map<String, Object>>() {});
    }

    private MultiValueMap<String, Object> toBody(MultipartFile image) {
        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        body.add("image", toResource(image));
        return body;
    }

    // pottery_api가 확장자로 파일 형식을 판단하므로(os.path.splitext),
    // 원본 파일명을 그대로 넘겨줘야 한다 - 안 그러면 기본값 .jpg로 처리된다.
    private ByteArrayResource toResource(MultipartFile image) {
        try {
            byte[] bytes = image.getBytes();
            return new ByteArrayResource(bytes) {
                @Override
                public String getFilename() {
                    return image.getOriginalFilename();
                }
            };
        } catch (IOException e) {
            throw new UncheckedIOException("이미지 파일을 읽는 중 오류가 발생했습니다.", e);
        }
    }
}