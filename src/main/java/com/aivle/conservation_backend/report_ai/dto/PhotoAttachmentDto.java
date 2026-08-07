package com.aivle.conservation_backend.report_ai.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

// .docx에 붙일 사진 한 장. S3_FILE에서 조회한 이미지를 base64로 인코딩해서
// 담는다 - report-ai는 파일을 직접 조회하지 않는다.
public record PhotoAttachmentDto(
        String caption,
        @JsonProperty("image_base64") String imageBase64
) {
}
