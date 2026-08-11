package com.aivle.conservation_backend.report_ai.dto;

import java.util.List;
import java.util.Map;

// 이미 만들어진(미리보기까지 끝난) report_json을 저장할 때 쓰는 요청 바디.
// artifact_id는 URL 경로 변수로 받으므로 여기엔 없다.
public record SaveReportRequestDto(
        Map<String, Object> reportJson,
        Map<String, List<PhotoAttachmentDto>> photos
) {
}
