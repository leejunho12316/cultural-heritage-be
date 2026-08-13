package com.aivle.conservation_backend.pottery_inspection_ai.client;

import com.aivle.conservation_backend.pottery_inspection_ai.dto.PotteryInspectionResponseDto;
import lombok.RequiredArgsConstructor;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.UncheckedIOException;

@RequiredArgsConstructor
@Service
public class PotteryInspectionAiClient {

    private final RestClient potteryInspectionAiRestClient;

    public PotteryInspectionResponseDto inspect(
            MultipartFile image, int nCalls, boolean useVlmPattern, boolean treatAsSingleArtifact
    ) {
        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        body.add("image", toResource(image));

        return potteryInspectionAiRestClient.post()
                .uri(uriBuilder -> uriBuilder
                        .path("/inspect")
                        .queryParam("n_calls", nCalls)
                        .queryParam("use_vlm_pattern", useVlmPattern)
                        .queryParam("treat_as_single_artifact", treatAsSingleArtifact)
                        .build())
                .contentType(MediaType.MULTIPART_FORM_DATA)
                .body(body)
                .retrieve()
                .body(PotteryInspectionResponseDto.class);
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