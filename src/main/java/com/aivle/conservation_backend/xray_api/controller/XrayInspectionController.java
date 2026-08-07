package com.aivle.conservation_backend.xray_api.controller;

import com.aivle.conservation_backend.xray_api.client.XrayAnomalyClient;
import com.aivle.conservation_backend.xray_api.dto.AnalysisTarget;
import com.aivle.conservation_backend.xray_api.dto.XrayDetectionResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayDefectMappingRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayDefectMappingResponse;
import com.aivle.conservation_backend.xray_api.service.XrayDefectMappingService;
import com.aivle.conservation_backend.xray_api.service.XrayStitchService;

import org.springframework.core.io.Resource;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.Map;
import java.util.HashMap;
import java.util.stream.IntStream;

/**
 * X-ray 이상영역 탐지 연동 확인용 컨트롤러.
 *
 * 실제 서비스에서는 이 결과를 바로 반환하지 않고
 * 비동기 job으로 처리하여 DB에 저장한 뒤
 * 프론트가 job 상태를 폴링하는 구조를 권장한다.
 *
 * CPU 추론 기준 소요 시간
 * - 결합본 1장: 5~10초
 * - 조각 30장: 1~2분
 */
@RestController
@RequestMapping("/api/xray")
public class XrayInspectionController {

    private final XrayAnomalyClient xrayAnomalyClient;
    private final XrayDefectMappingService xrayDefectMappingService;
    private final XrayStitchService xrayStitchService;

    public XrayInspectionController(
            XrayAnomalyClient xrayAnomalyClient,
            XrayDefectMappingService xrayDefectMappingService,
            XrayStitchService xrayStitchService
    ) {
        this.xrayAnomalyClient = xrayAnomalyClient;
        this.xrayDefectMappingService = xrayDefectMappingService;
        this.xrayStitchService = xrayStitchService;
    }

    /**
     * AI 서비스 상태 확인.
     */
    @GetMapping("/health")
    public ResponseEntity<Map<String, Object>> health() {
        var r = xrayAnomalyClient.getHealth();

        Map<String, Object> body = new HashMap<>();

        body.put(
                "aiServiceHealthy",
                "ok".equals(r.status())
                        && Boolean.TRUE.equals(r.modelLoaded())
        );
        body.put("modelLoaded", r.modelLoaded());
        body.put("device", r.device());
        body.put("llmEnabled", r.llmEnabled());

        return ResponseEntity.ok(body);
    }

    /**
     * 결합/Konva/final 렌더링까지 끝난 서버 정본 1장을 분석한다.
     */
    @PostMapping("/detect/assembled/{jobId}")
    public ResponseEntity<XrayDetectionResponse> detectFinalAssembled(
            @PathVariable String jobId,
            @RequestParam(value = "confidence", required = false) Double confidence
    ) {
        xrayStitchService.requireFinalizedJob(jobId);
        Resource finalImage = xrayStitchService.getFinalResult(jobId);
        return ResponseEntity.ok(
                xrayAnomalyClient.detect(
                        finalImage,
                        AnalysisTarget.ASSEMBLED,
                        confidence
                )
        );
    }

    /**
     * 결합이 확정된 job의 원본 X-ray를 originalSourceIndex 순서로 분석한다.
     */
    @PostMapping("/detect/fragments/{jobId}")
    public ResponseEntity<XrayDetectionResponse> detectJobFragments(
            @PathVariable String jobId,
            @RequestParam(value = "confidence", required = false) Double confidence
    ) {
        List<Resource> files = xrayStitchService.getOrderedXraySourceResources(jobId);
        List<Integer> sourceIndexes = IntStream.range(0, files.size()).boxed().toList();
        return ResponseEntity.ok(
                xrayAnomalyClient.detectBatchResources(
                        files,
                        sourceIndexes,
                        AnalysisTarget.FRAGMENT,
                        confidence
                )
        );
    }

    /**
     * 결합 완료본 1장 분석.
     */
    @PostMapping("/detect/assembled")
    public ResponseEntity<XrayDetectionResponse> detectAssembled(
            @RequestParam("file") MultipartFile file,

            @RequestParam(
                    value = "confidence",
                    required = false
            )
            Double confidence
    ) {
        XrayDetectionResponse response =
                xrayAnomalyClient.detect(
                        file,
                        AnalysisTarget.ASSEMBLED,
                        confidence
                );

        return ResponseEntity.ok(response);
    }

    /**
     * 원본 조각 여러 장 분석.
     */
    @PostMapping("/detect/fragments")
    public ResponseEntity<XrayDetectionResponse> detectFragments(
            @RequestParam("files") List<MultipartFile> files,

            @RequestParam("source_indexes") List<Integer> sourceIndexes,

            @RequestParam(
                    value = "confidence",
                    required = false
            )
            Double confidence
    ) {
        XrayDetectionResponse response =
                xrayAnomalyClient.detectBatch(
                        files,
                        sourceIndexes,
                        AnalysisTarget.FRAGMENT,
                        confidence
                );

        return ResponseEntity.ok(response);
    }

    /**
     * 원본 조각 결함을 layout.final.json의 최종 transform으로
     * 결합본 좌표계에 투영한 뒤 결합본 결함과 기하학적으로 대응한다.
     */
    @PostMapping(
            value = "/defect-mapping/{jobId}",
            consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE
    )
    public ResponseEntity<XrayDefectMappingResponse> mapDefects(
            @PathVariable String jobId,
            @RequestBody XrayDefectMappingRequest request
    ) {
        xrayStitchService.requireFinalizedJob(jobId);
        return ResponseEntity.ok(
                xrayDefectMappingService.mapDefects(jobId, request)
        );
    }

    /**
     * 전문가용 1차 상태조사 문안 생성.
     *
     * AI 서비스 응답을 그대로 전달한다(passthrough).
     * 응답 구조가 단순하고 Spring이 가공할 내용이 없어
     * DTO 매핑을 생략했다.
     *
     * 실제 서비스에서는 이 결과를 DB에 저장하고
     * 전문가 수정 이력을 함께 관리해야 한다.
     */
    @PostMapping(
            value = "/report",
            produces = MediaType.APPLICATION_JSON_VALUE
    )
    public ResponseEntity<String> generateReport(
            @RequestParam("regions") String regions,

            @RequestParam(
                    value = "artifact_type",
                    required = false
            )
            String artifactType,

            @RequestParam(
                    value = "material",
                    required = false
            )
            String material,

            @RequestParam(
                    value = "report_style",
                    required = false,
                    defaultValue = "summary"
            )
            String reportStyle,

            @RequestParam(
                    value = "assembled",
                    required = false
            )
            MultipartFile assembled,

            @RequestParam(
                    value = "fragments",
                    required = false
            )
            List<MultipartFile> fragments,

            @RequestParam(
                    value = "rgb_images",
                    required = false
            )
            List<MultipartFile> rgbImages
    ) {
        String json = xrayAnomalyClient.generateReport(
                regions,
                artifactType,
                material,
                reportStyle,
                assembled,
                fragments,
                rgbImages
        );

        return ResponseEntity.ok(json);
    }
}
