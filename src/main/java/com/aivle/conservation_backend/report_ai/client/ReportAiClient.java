package com.aivle.conservation_backend.report_ai.client;

import com.aivle.conservation_backend.report_ai.dto.DocxRequestDto;
import com.aivle.conservation_backend.report_ai.dto.GenerateReportRequestDto;
import com.aivle.conservation_backend.report_ai.dto.GenerateReportResponseDto;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.util.Map;

/**
 * 보고서 자동생성 AI 서비스(report-ai) 클라이언트.
 *
 * report-ai는 GUIDE_TASK/XRAY_JOB/INSPECTION_RESULT_POTTERY를 직접 조회하지
 * 않으므로, 이 클라이언트를 호출하기 전에 각 파트 결과를 모아서 채워야 한다.
 */
@RequiredArgsConstructor
@Service
public class ReportAiClient {

    private final RestClient reportAiRestClient;

    /** report_json만 생성한다 (LangGraph 실행 + LLM 호출 발생). */
    public Map<String, Object> generateReport(GenerateReportRequestDto request) {
        GenerateReportResponseDto response = reportAiRestClient.post()
                .uri("/reports/generate")
                .body(request)
                .retrieve()
                .body(GenerateReportResponseDto.class);

        return response == null ? null : response.reportJson();
    }

    /**
     * report_json 생성 + .docx 변환을 한 번에 한다.
     *
     * 데모/직접 테스트용. 실제 운영에서는 매번 그래프를 다시 돌리면 LLM 호출
     * 비용이 중복 발생하므로, generateReport()로 한 번 만든 report_json을
     * ASSESSMENT_REPORT에 저장해두고 reportToDocx()로 변환만 반복 요청하는
     * 흐름을 쓰는 게 맞다.
     */
    public byte[] generateReportDocx(GenerateReportRequestDto request) {
        return reportAiRestClient.post()
                .uri("/reports/generate/docx")
                .body(request)
                .retrieve()
                .body(byte[].class);
    }

    /** 이미 생성/저장된 report_json을 .docx로 변환만 한다 (LLM 재호출 없음). */
    public byte[] reportToDocx(DocxRequestDto request) {
        return reportAiRestClient.post()
                .uri("/reports/docx")
                .body(request)
                .retrieve()
                .body(byte[].class);
    }
}
