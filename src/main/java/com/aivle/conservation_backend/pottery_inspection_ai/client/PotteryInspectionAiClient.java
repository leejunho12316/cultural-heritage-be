package com.aivle.conservation_backend.pottery_inspection_ai.client;

import com.aivle.conservation_backend.pottery_inspection_ai.dto.PotteryInspectionResponseDto;
import lombok.RequiredArgsConstructor;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.core.io.ByteArrayResource;
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

    /** 기존 동기 방식. 하위 호환용으로 유지한다. */
    public PotteryInspectionResponseDto inspect(
            MultipartFile image,
            int nCalls,
            boolean useVlmPattern,
            boolean treatAsSingleArtifact
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

    /** FastAPI에 분석 job을 접수하고 {job_id, status}를 받는다. */
    public Map<String, Object> createInspectionJob(
            MultipartFile image,
            int nCalls,
            boolean useVlmPattern,
            boolean treatAsSingleArtifact
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

    /** FastAPI 메모리에 있는 job의 현재 상태/결과를 조회한다. */
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

    // pottery_api가 확장자로 파일 형식을 판단하므로 원본 파일명을 유지한다.
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
