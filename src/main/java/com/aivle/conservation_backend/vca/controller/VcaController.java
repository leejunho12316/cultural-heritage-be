package com.aivle.conservation_backend.vca.controller;

import com.aivle.conservation_backend.vca.dto.ArtifactCollectionResponse;
import com.aivle.conservation_backend.vca.dto.ArtifactDetailResponse;
import com.aivle.conservation_backend.vca.dto.CompleteImageRequest;
import com.aivle.conservation_backend.vca.dto.CreateRunRequest;
import com.aivle.conservation_backend.vca.dto.ImageResponse;
import com.aivle.conservation_backend.vca.dto.IntermediateResultsResponse;
import com.aivle.conservation_backend.vca.dto.PdfJobResponse;
import com.aivle.conservation_backend.vca.dto.PotteryInspectionRequest;
import com.aivle.conservation_backend.vca.dto.PresignImageRequest;
import com.aivle.conservation_backend.vca.dto.PresignImageResponse;
import com.aivle.conservation_backend.vca.dto.ReportResponse;
import com.aivle.conservation_backend.vca.dto.RunResponse;
import com.aivle.conservation_backend.vca.dto.SystemInfoResponse;
import com.aivle.conservation_backend.vca.dto.VcaCorpusPdfCollectionResponse;
import com.aivle.conservation_backend.vca.dto.VcaCorpusPdfResponse;
import com.aivle.conservation_backend.vca.exception.VcaApiException;
import com.aivle.conservation_backend.vca.service.VcaService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.net.URI;

// FE(육안조사 화면)가 호출하는 VCA REST API 진입점. 실제 로직은 모두 VcaService에 위임하고,
// 이 클래스는 요청/응답 매핑과 HTTP 상태 코드만 담당한다.
@RestController
@RequestMapping("/api/vca")
public class VcaController {

    private final VcaService vcaService;

    public VcaController(VcaService vcaService) {
        this.vcaService = vcaService;
    }

    // 전체 아티팩트 목록(대시보드 카드용). 조회 시마다 데모 run 진행 상태도 함께 갱신된다.
    @GetMapping
    public ArtifactCollectionResponse getArtifacts() {
        return vcaService.getArtifacts();
    }

    // RAG 근거로 쓰이는 PDF 코퍼스 관리 엔드포인트(아티팩트 단위가 아닌 전역 코퍼스).
    @GetMapping("/corpus/pdfs")
    public VcaCorpusPdfCollectionResponse getCorpusPdfs() {
        return vcaService.getCorpusPdfs();
    }

    @PostMapping(
            path = "/corpus/pdfs",
            consumes = MediaType.MULTIPART_FORM_DATA_VALUE
    )
    public ResponseEntity<VcaCorpusPdfResponse> uploadCorpusPdf(
            @RequestParam("file") MultipartFile file
    ) {
        return ResponseEntity.status(HttpStatus.CREATED).body(vcaService.uploadCorpusPdf(file));
    }

    @DeleteMapping("/corpus/pdfs/{fileName}")
    public ResponseEntity<Void> deleteCorpusPdf(@PathVariable String fileName) {
        vcaService.deleteCorpusPdf(fileName);
        return ResponseEntity.noContent().build();
    }

    // 조사 보고서 하단 "시스템 환경 정보" 표기용 - 특정 run이 아니라 vca-ai
    // 엔진이 지금 도는 환경을 설명하는 전역 정보라 artifactId 없이 조회한다.
    @GetMapping("/system-info")
    public SystemInfoResponse getSystemInfo() {
        return vcaService.getSystemInfo();
    }

    // 아티팩트 상세(업로드 이미지, run 이력 포함). artifactId가 없으면 VcaService가 자동 생성한다.
    @GetMapping("/{artifactId}")
    public ArtifactDetailResponse getArtifact(@PathVariable String artifactId) {
        return vcaService.getArtifact(artifactId);
    }

    @GetMapping("/{artifactId}/runs/{assessmentRunId}/report")
    public ReportResponse getReport(
            @PathVariable String artifactId,
            @PathVariable String assessmentRunId
    ) {
        return vcaService.getReport(artifactId, assessmentRunId);
    }

    @GetMapping("/{artifactId}/runs/{assessmentRunId}/intermediate-results")
    public IntermediateResultsResponse getIntermediateResults(
            @PathVariable String artifactId,
            @PathVariable String assessmentRunId
    ) {
        return vcaService.getIntermediateResults(artifactId, assessmentRunId);
    }

    // 이미지 파일 게이트웨이. 실제 바이트를 스트리밍하지 않고 303으로 presigned/로컬 URL로 리다이렉트한다.
    // <img>가 직접 호출하는 경로라 VcaAccessTokenInterceptor의 쿼리 토큰 예외 대상이기도 하다.
    @GetMapping("/{artifactId}/files/sha256/{sha256}")
    public ResponseEntity<Void> getFile(
            @PathVariable String artifactId,
            @PathVariable String sha256
    ) {
        return ResponseEntity.status(HttpStatus.SEE_OTHER)
                .location(vcaService.getFileDownloadLocation(artifactId, sha256))
                .build();
    }

    // 업로드 전 단계: presigned URL(또는 로컬 폴백 URL)을 발급하고 이미지를 PENDING 상태로 예약한다.
    // 실제 바이트 저장은 클라이언트가 이 URL로 직접 PUT한 뒤 completeImage로 완료 처리해야 한다.
    @PostMapping("/{artifactId}/images/presign")
    public ResponseEntity<PresignImageResponse> presignImage(
            @PathVariable String artifactId,
            @Valid @RequestBody PresignImageRequest request
    ) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(vcaService.presignImage(artifactId, request));
    }

    @PostMapping(
            path = "/{artifactId}/images",
            consumes = MediaType.MULTIPART_FORM_DATA_VALUE
    )
    // presign 없이 Spring을 거쳐 바로 업로드하는 경로(DIRECT_UPLOAD). presignImage와 별개의 흐름.
    public ResponseEntity<ImageResponse> uploadImage(
            @PathVariable String artifactId,
            @RequestParam("file") MultipartFile file
    ) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(vcaService.uploadImage(artifactId, file));
    }

    // presignImage로 예약된 업로드를 확정. 클라이언트가 보고한 sha256과 실제 저장된 값이
    // 일치하는지 확인한 뒤에만 이미지 상태를 UPLOADED로 전환한다.
    @PostMapping("/{artifactId}/images/{imageId}/complete")
    public ImageResponse completeImage(
            @PathVariable String artifactId,
            @PathVariable String imageId,
            @Valid @RequestBody CompleteImageRequest request
    ) {
        return vcaService.completeImage(artifactId, imageId, request);
    }

    @DeleteMapping("/{artifactId}/images/{imageId}")
    public ResponseEntity<Void> deleteImage(
            @PathVariable String artifactId,
            @PathVariable String imageId
    ) {
        vcaService.deleteImage(artifactId, imageId);
        return ResponseEntity.noContent().build();
    }

    // 새 assessment run 생성(업로드된 이미지로 AI 분석 시작). 이미 진행 중인 run이 있으면 실패한다.
    @PostMapping("/{artifactId}/runs")
    public ResponseEntity<RunResponse> createRun(
            @PathVariable String artifactId,
            @RequestBody(required = false) CreateRunRequest request
    ) {
        return ResponseEntity.accepted().body(vcaService.createRun(artifactId, request));
    }

    // FE의 "분석 중지" 버튼이 호출하는 엔드포인트. 이미 종료된 run에 대해서는 에러 없이
    // 현재 상태만 그대로 반환한다(중복 클릭 대비).
    @PostMapping("/{artifactId}/runs/{assessmentRunId}/cancel")
    public RunResponse cancelRun(
            @PathVariable String artifactId,
            @PathVariable String assessmentRunId
    ) {
        return vcaService.cancelRun(artifactId, assessmentRunId);
    }

    // 리포트 PDF 생성 job을 등록. object storage(S3/MinIO)가 설정된 실제 환경에서는
    // PDFBox로 진짜 PDF를 동기 렌더링해 즉시 COMPLETED로 반환한다. object storage가
    // 없는 데모 모드만 예전처럼 QUEUED로 남기는 스텁 동작을 유지한다 - getPdfJob 참고.
    @PostMapping("/{artifactId}/runs/{assessmentRunId}/report/pdf")
    public ResponseEntity<PdfJobResponse> createPdfJob(
            @PathVariable String artifactId,
            @PathVariable String assessmentRunId
    ) {
        return ResponseEntity.accepted()
                .body(vcaService.createPdfJob(artifactId, assessmentRunId));
    }

    // 도자기 재질 run에 대해서만 유효한 별도 추가 검사(수동 트리거). material이 도자기 계열이
    // 아니면 VcaService가 조용히 "적용 안 됨" 상태로 리포트를 돌려준다.
    @PostMapping("/{artifactId}/runs/{assessmentRunId}/pottery-inspection")
    public ReportResponse runPotteryInspection(
            @PathVariable String artifactId,
            @PathVariable String assessmentRunId,
            @RequestBody(required = false) PotteryInspectionRequest request
    ) {
        return vcaService.runPotteryInspection(artifactId, assessmentRunId, request);
    }

    // PDF job 상태 폴링. 실제 렌더링은 createPdfJob에서 이미 동기로 끝나 저장된 상태를
    // 그대로 돌려준다. object storage 없는 데모 모드 job만 QUEUED/RUNNING으로 조회되면
    // 그 즉시 COMPLETED로 전환되는 예전 폴링 스텁 동작을 유지한다.
    @GetMapping("/{artifactId}/report-pdf-jobs/{jobId}")
    public PdfJobResponse getPdfJob(
            @PathVariable String artifactId,
            @PathVariable String jobId
    ) {
        return vcaService.getPdfJob(artifactId, jobId);
    }

    // getFile과 동일한 패턴의 303 리다이렉트 게이트웨이. 쿼리 토큰 인증 예외 대상 경로이기도 하다.
    @GetMapping("/{artifactId}/report-pdf-jobs/{jobId}/download")
    public ResponseEntity<Void> downloadPdf(
            @PathVariable String artifactId,
            @PathVariable String jobId
    ) {
        URI location = vcaService.getPdfDownloadLocation(artifactId, jobId);
        return ResponseEntity.status(HttpStatus.SEE_OTHER).location(location).build();
    }

    // /api/vca/** 안에서 위의 어느 매핑에도 걸리지 않은 요청을 잡는 catch-all.
    // Spring이 더 구체적인 매핑을 우선하므로 실제로는 정의되지 않은 하위 경로만 여기로 온다.
    @RequestMapping("/**")
    public void handleUnknownVcaEndpoint() {
        throw new VcaApiException(
                HttpStatus.NOT_FOUND,
                "VCA_ENDPOINT_NOT_FOUND",
                "The requested VCA endpoint was not found."
        );
    }
}
