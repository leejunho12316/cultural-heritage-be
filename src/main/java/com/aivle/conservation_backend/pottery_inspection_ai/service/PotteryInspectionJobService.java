package com.aivle.conservation_backend.pottery_inspection_ai.service;

import com.aivle.conservation_backend.photo.service.S3PhotoStorageService;
import com.aivle.conservation_backend.pottery_inspection_ai.client.PotteryInspectionAiClient;
import com.aivle.conservation_backend.vca.domain.InspectionResultPottery;
import com.aivle.conservation_backend.pottery_inspection_ai.dto.PotteryInspectionJobResponseDto;
import com.aivle.conservation_backend.pottery_inspection_ai.dto.PotteryInspectionResponseDto;
import com.aivle.conservation_backend.vca.repository.InspectionResultPotteryRepository;
import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import com.aivle.conservation_backend.vca.repository.AssessmentRunRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.ObjectMapper;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * 육안조사 job의 서버 영속 상태를 관리한다.
 *
 * FastAPI의 job은 프로세스 메모리에서 실행되지만, Spring은 ai_run_id와 상태,
 * 원본 사진 S3 key를 assessment_run(RDS)에 기록한다. 따라서 FE가 페이지를
 * 벗어나거나 새로고침해도 artifactId만으로 진행 중 작업을 다시 찾을 수 있다.
 */
@RequiredArgsConstructor
@Service
public class PotteryInspectionJobService {

    private static final String DETAIL_ANNOTATED_IMAGE_KEY = "_annotated_image_key";

    private static final List<String> ACTIVE_STATUSES = List.of("queued", "running");
    private static final String CONFIG_IMAGE_KEY = "potteryInspectionImageKey";
    private static final String STAGE_ERROR_STATUS = "errorStatus";
    private static final String STAGE_ERROR_DETAIL = "errorDetail";
    private static final String STAGE_LAST_POLL_ERROR = "lastPollError";
    private static final String STATE_AI_STATUS = "aiStatus";
    private static final String STATE_PENDING_RESULT = "pendingResult";
    private static final String STATE_FINALIZED_AT = "finalizedAt";

    private final AssessmentRunRepository assessmentRunRepository;
    private final InspectionResultPotteryRepository inspectionResultPotteryRepository;
    private final PotteryInspectionAiClient potteryInspectionAiClient;
    private final S3PhotoStorageService photoStorageService;
    private final ObjectMapper objectMapper;

    /** 새 육안조사 run을 만들고 FastAPI job까지 접수한다. */
    @Transactional
    public PotteryInspectionJobResponseDto createJob(
            UUID artifactId,
            MultipartFile image,
            int nCalls,
            boolean useVlmPattern,
            boolean treatAsSingleArtifact
    ) {
        if (assessmentRunRepository.existsByArtifactIdAndRunTypeAndStatusInAndAiRunIdIsNotNull(
        artifactId,
        AssessmentRun.RUN_TYPE_POTTERY_PATTERN,
        ACTIVE_STATUSES
        )) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "이미 진행 중인 육안조사 작업이 있습니다. 기존 작업을 다시 불러와주세요."
            );
        }

        int nextRunNumber = assessmentRunRepository.findMaxRunNumberByArtifactId(artifactId) + 1;
        AssessmentRun run = AssessmentRun.create(
                UUID.randomUUID(),
                artifactId,
                nextRunNumber,
                null,
                false,
                null,
                Map.of(
                        "nCalls", nCalls,
                        "useVlmPattern", useVlmPattern,
                        "treatAsSingleArtifact", treatAsSingleArtifact
                )
        );
        assessmentRunRepository.save(run);

        String imageKey;
        try {
            imageKey = photoStorageService.uploadPotteryInspection(artifactId, run.getId(), image);
            run.mergeConfig(Map.of(CONFIG_IMAGE_KEY, imageKey));
            run.setInputSnapshot(1, null, null);
            assessmentRunRepository.save(run);
        } catch (RuntimeException error) {
            run.markFailed("IMAGE_UPLOAD", rootMessage(error));
            assessmentRunRepository.save(run);
            throw error;
        }

        try {
            Map<String, Object> accepted = potteryInspectionAiClient.createInspectionJob(
                    image,
                    nCalls,
                    useVlmPattern,
                    treatAsSingleArtifact
            );
            String aiJobId = valueAsString(accepted == null ? null : accepted.get("job_id"));
            if (aiJobId == null || aiJobId.isBlank()) {
                throw new IllegalStateException("AI 서비스가 job_id를 반환하지 않았습니다.");
            }

            run.bindAiRun(aiJobId, null);
            run.markRunning(null, "AI_QUEUED");
            run.updateProgress("AI_QUEUED", 5);
            assessmentRunRepository.save(run);
            return toResponse(run);
        } catch (RuntimeException error) {
            run.markFailed("AI_SUBMIT", rootMessage(error));
            run.updatePotteryJobState(errorStages(error));
            assessmentRunRepository.save(run);
            throw error;
        }
    }

    /** 특정 assessment run을 서버 상태 기준으로 조회하고, 필요하면 FastAPI 상태도 동기화한다. */
    @Transactional
    public PotteryInspectionJobResponseDto getJob(UUID artifactId, UUID assessmentRunId) {
        AssessmentRun run = assessmentRunRepository
                .findByIdAndArtifactIdForUpdate(assessmentRunId, artifactId)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND,
                        "육안조사 작업을 찾을 수 없습니다: " + assessmentRunId
                ));

        refreshFromAiIfActive(run);
        return toResponse(run);
    }

    /** artifactId의 가장 최근 육안조사 job/저장 결과를 찾는다. */
    @Transactional
    public PotteryInspectionJobResponseDto getLatestJob(UUID artifactId) {
        // 동일 artifact에 VCA 상태조사 run도 함께 존재할 수 있으므로
        // 문양조사(POTTERY_PATTERN) run만 최신순으로 조회한다.
        List<AssessmentRun> runs = assessmentRunRepository
                .findAllByArtifactIdAndRunTypeOrderByRunNumberDesc(
                        artifactId,
                        AssessmentRun.RUN_TYPE_POTTERY_PATTERN
                );

        for (AssessmentRun candidate : runs) {
            boolean hasAiJob = candidate.getAiRunId() != null && !candidate.getAiRunId().isBlank();
            // VCA v2에서는 InspectionResultPottery가 AssessmentRun 연관관계를 직접 가지지 않고
            // assessmentRunId(UUID)만 저장하므로 assessmentRunId 기준으로 결과 존재 여부를 조회한다.
            boolean hasResult = inspectionResultPotteryRepository
                    .findByAssessmentRunId(candidate.getId())
                    .isPresent();

            if (!hasAiJob && !hasResult) {
                continue;
            }

            AssessmentRun locked = assessmentRunRepository
                    .findByIdAndArtifactIdForUpdate(candidate.getId(), artifactId)
                    .orElseThrow();

            // 기존 FE는 결과만 저장하고 assessment_run을 queued로 남겼다.
            // ai_run_id 없이 결과가 이미 있으면 완료된 레거시 run으로 정규화한다.
            if (!hasAiJob && hasResult && ACTIVE_STATUSES.contains(locked.getStatus())) {
                locked.markCompleted();
                assessmentRunRepository.save(locked);
            }

            refreshFromAiIfActive(locked);
            return toResponse(locked);
        }

        throw new ResponseStatusException(HttpStatus.NOT_FOUND, "저장된 육안조사 작업이 없습니다.");
    }

    @Transactional
    public PotteryInspectionJobResponseDto saveAnnotatedPhoto(
            UUID artifactId,
            UUID assessmentRunId,
            MultipartFile file
    ) {
        AssessmentRun run = assessmentRunRepository
                .findByIdAndArtifactIdForUpdate(assessmentRunId, artifactId)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND,
                        "육안조사 작업을 찾을 수 없습니다: " + assessmentRunId
                ));

        InspectionResultPottery stored = inspectionResultPotteryRepository
                .findByAssessmentRunId(run.getId())
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.CONFLICT,
                        "완료된 문양조사 결과가 없어 보정 이미지를 저장할 수 없습니다."
                ));

        String key = photoStorageService.uploadPotteryAnnotated(artifactId, assessmentRunId, file);
        Map<String, Object> detail = new LinkedHashMap<>();
        if (stored.getDetail() != null) detail.putAll(stored.getDetail());
        detail.put(DETAIL_ANNOTATED_IMAGE_KEY, key);
        stored.setDetail(detail);
        inspectionResultPotteryRepository.save(stored);

        return toResponse(run);
    }

    private void refreshFromAiIfActive(AssessmentRun run) {
        if (!ACTIVE_STATUSES.contains(run.getStatus())) {
            return;
        }
        if (run.getAiRunId() == null || run.getAiRunId().isBlank()) {
            return;
        }

        try {
            Map<String, Object> aiJob = potteryInspectionAiClient.getInspectionJob(run.getAiRunId());
            applyAiJob(run, aiJob);
        } catch (RestClientResponseException error) {
            // FastAPI의 failed job은 422/500 등 비-2xx로 상태와 detail을 반환한다.
            Map<String, Object> body = parseMap(error.getResponseBodyAsString());
            Object detail = body == null ? error.getResponseBodyAsString() : body.get("detail");

            run.markFailed("AI_ANALYSIS", detailText(detail, error.getMessage()));
            run.updatePotteryJobState(Map.of(
                    STAGE_ERROR_STATUS, error.getStatusCode().value(),
                    STAGE_ERROR_DETAIL, detail == null ? "AI 분석 실패" : detail
            ));
            assessmentRunRepository.save(run);
        } catch (ResourceAccessException error) {
            // 일시적인 네트워크 오류는 job 자체를 FAILED로 확정하지 않는다.
            Map<String, Object> stages = new LinkedHashMap<>();
            if (run.getPotteryJobStateJson() != null) {
                stages.putAll(run.getPotteryJobStateJson());
            }
            stages.put(STAGE_LAST_POLL_ERROR, rootMessage(error));
            run.updatePotteryJobState(stages);
            assessmentRunRepository.save(run);
        }
    }

    private void applyAiJob(AssessmentRun run, Map<String, Object> aiJob) {
        if (aiJob == null) {
            return;
        }

        String status = valueAsString(aiJob.get("status"));
        if (status == null) {
            return;
        }

        switch (status) {
            case "queued" -> {
                run.markRunning(null, "AI_QUEUED");
                run.updateProgress("AI_QUEUED", 10);
                assessmentRunRepository.save(run);
            }
            case "processing" -> {
                run.markRunning(null, "AI_ANALYSIS");
                run.updateProgress("AI_ANALYSIS", 50);
                assessmentRunRepository.save(run);
            }
            case "done" -> prepareReview(run, aiJob.get("result"));
            case "failed" -> {
                Object detail = aiJob.get("detail");
                run.markFailed("AI_ANALYSIS", detailText(detail, "AI 분석 실패"));
                run.updatePotteryJobState(Map.of(STAGE_ERROR_DETAIL, detail == null ? "AI 분석 실패" : detail));
                assessmentRunRepository.save(run);
            }
            default -> {
                Map<String, Object> stages = new LinkedHashMap<>();
                if (run.getPotteryJobStateJson() != null) {
                    stages.putAll(run.getPotteryJobStateJson());
                }
                stages.put("unknownAiStatus", status);
                run.updatePotteryJobState(stages);
                assessmentRunRepository.save(run);
            }
        }
    }

    /**
     * AI 분석 완료 결과를 최종 결과 테이블에 즉시 저장하지 않고 review_ready 상태로
     * 보관한다. 사용자가 화면에서 결과를 확인한 뒤 completeJob()을 호출해야만
     * InspectionResultPottery 생성과 AssessmentRun COMPLETED 전환이 일어난다.
     */
    private void prepareReview(AssessmentRun run, Object rawResult) {
        PotteryInspectionResponseDto result = validateResult(rawResult);

        Map<String, Object> state = copyPotteryJobState(run);
        state.put(STATE_AI_STATUS, "done");
        state.put(STATE_PENDING_RESULT, objectMapper.convertValue(result, Map.class));
        state.remove(STAGE_ERROR_STATUS);
        state.remove(STAGE_ERROR_DETAIL);
        state.remove(STAGE_LAST_POLL_ERROR);

        run.markReviewReady();
        run.updatePotteryJobState(state);
        assessmentRunRepository.save(run);
    }

    /**
     * 사용자가 '문양 기반 상태 조사 완료'를 눌렀을 때 호출되는 최종 확정 처리.
     * 결과 저장과 run 완료 전환을 하나의 Spring transaction 안에서 처리한다.
     */
    @Transactional
    public PotteryInspectionJobResponseDto completeJob(UUID artifactId, UUID assessmentRunId) {
        AssessmentRun run = assessmentRunRepository
                .findByIdAndArtifactIdForUpdate(assessmentRunId, artifactId)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND,
                        "육안조사 작업을 찾을 수 없습니다: " + assessmentRunId
                ));

        if (!AssessmentRun.RUN_TYPE_POTTERY_PATTERN.equals(run.getRunType())) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "문양 기반 상태 조사 run이 아닙니다."
            );
        }

        InspectionResultPottery existing = inspectionResultPotteryRepository
                .findByAssessmentRunId(run.getId())
                .orElse(null);

        // 네트워크 재시도 등으로 완료 API가 중복 호출돼도 안전하게 같은 결과를 반환한다.
        if (existing != null && "completed".equalsIgnoreCase(run.getStatus())) {
            return toResponse(run);
        }

        if (!"review_ready".equalsIgnoreCase(run.getStatus())) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "AI 분석 결과가 최종 검토 대기 상태가 아닙니다."
            );
        }

        PotteryInspectionResponseDto pending = pendingResult(run);
        if (pending == null) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "최종 저장할 문양 기반 상태 조사 결과를 찾을 수 없습니다."
            );
        }

        if (existing == null) {
            inspectionResultPotteryRepository.save(toEntity(run, pending));
        }

        Map<String, Object> state = copyPotteryJobState(run);
        state.remove(STATE_PENDING_RESULT);
        state.put(STATE_AI_STATUS, "done");
        state.put(STATE_FINALIZED_AT, java.time.Instant.now().toString());
        run.updatePotteryJobState(state);
        run.markCompleted();
        assessmentRunRepository.save(run);

        return toResponse(run);
    }

    private PotteryInspectionResponseDto validateResult(Object rawResult) {
        PotteryInspectionResponseDto result = objectMapper.convertValue(
                rawResult,
                PotteryInspectionResponseDto.class
        );
        if (result == null || result.inspectionText() == null || result.inspectionText().isBlank()) {
            throw new IllegalStateException("AI 완료 응답에 육안조사 결과가 없습니다.");
        }
        return result;
    }

    private InspectionResultPottery toEntity(AssessmentRun run, PotteryInspectionResponseDto result) {
        Map<String, Object> detail = new LinkedHashMap<>();
        if (result.detail() != null) {
            detail.putAll(result.detail());
        }
        if (result.moduleVersion() != null) {
            detail.put("_module_version", result.moduleVersion());
        }
        if (result.summary() != null) {
            detail.put("_summary", result.summary());
        }

        return InspectionResultPottery.create(
                UUID.randomUUID(),
                run.getId(),
                result.inspectionText(),
                result.humanReviewRecommended(),
                detail
        );
    }

    @SuppressWarnings("unchecked")
    private PotteryInspectionResponseDto pendingResult(AssessmentRun run) {
        Map<String, Object> state = run.getPotteryJobStateJson();
        if (state == null) return null;
        Object raw = state.get(STATE_PENDING_RESULT);
        if (raw == null) return null;
        return objectMapper.convertValue(raw, PotteryInspectionResponseDto.class);
    }

    private Map<String, Object> copyPotteryJobState(AssessmentRun run) {
        Map<String, Object> state = new LinkedHashMap<>();
        if (run.getPotteryJobStateJson() != null) {
            state.putAll(run.getPotteryJobStateJson());
        }
        return state;
    }

    private PotteryInspectionJobResponseDto toResponse(AssessmentRun run) {
        // 문양조사 결과는 assessmentRunId와 1:1로 저장되므로
        // 현재 run ID로 저장 결과를 조회해 API 응답으로 변환한다.
        InspectionResultPottery stored = inspectionResultPotteryRepository
            .findByAssessmentRunId(run.getId())
            .orElse(null);

        PotteryInspectionResponseDto result = stored == null
                ? pendingResult(run)
                : toAiResult(stored);

        Object errorDetail = null;
        Integer errorStatus = null;
        if (run.getPotteryJobStateJson() != null) {
            errorDetail = run.getPotteryJobStateJson().get(STAGE_ERROR_DETAIL);
            Object rawStatus = run.getPotteryJobStateJson().get(STAGE_ERROR_STATUS);
            if (rawStatus instanceof Number number) {
                errorStatus = number.intValue();
            } else if (rawStatus != null) {
                try {
                    errorStatus = Integer.parseInt(String.valueOf(rawStatus));
                } catch (NumberFormatException ignored) {
                    // 잘못된 메타데이터 하나 때문에 상태 조회 전체를 실패시키지 않는다.
                }
            }
        }
        if (errorDetail == null && "failed".equals(run.getStatus())) {
            errorDetail = run.getFailureReason();
        }

        return new PotteryInspectionJobResponseDto(
                run.getId(),
                run.getArtifactId(),
                run.getAiRunId(),
                externalStatus(run),
                run.getCurrentStage(),
                run.getProgressPercent(),
                photoUrl(run),
                annotatedPhotoUrl(stored),
                result,
                errorStatus,
                errorDetail
        );
    }

    private PotteryInspectionResponseDto toAiResult(InspectionResultPottery stored) {
        Map<String, Object> detail = stored.getDetail() == null
                ? Map.of()
                : stored.getDetail();
        return new PotteryInspectionResponseDto(
                valueAsString(detail.get("_module_version")),
                stored.getInspectionText(),
                valueAsString(detail.get("_summary")),
                stored.isHumanReviewRecommended(),
                detail
        );
    }

    private String photoUrl(AssessmentRun run) {
        if (run.getConfigJson() == null) {
            return null;
        }
        String key = valueAsString(run.getConfigJson().get(CONFIG_IMAGE_KEY));
        return key == null ? null : photoStorageService.presignedUrl(key);
    }

    private String annotatedPhotoUrl(InspectionResultPottery stored) {
        if (stored == null || stored.getDetail() == null) return null;
        String key = valueAsString(stored.getDetail().get(DETAIL_ANNOTATED_IMAGE_KEY));
        return key == null ? null : photoStorageService.presignedUrl(key);
    }

    private String externalStatus(AssessmentRun run) {
        return switch (run.getStatus()) {
            case "completed" -> "done";
            case "review_ready" -> "review_ready";
            case "failed" -> "failed";
            case "queued" -> "queued";
            default -> "AI_QUEUED".equals(run.getCurrentStage()) ? "queued" : "processing";
        };
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> parseMap(String json) {
        if (json == null || json.isBlank()) {
            return null;
        }
        try {
            return objectMapper.readValue(json, Map.class);
        } catch (Exception ignored) {
            return null;
        }
    }

    private Map<String, Object> errorStages(RuntimeException error) {
        Map<String, Object> stages = new LinkedHashMap<>();
        if (error instanceof RestClientResponseException responseError) {
            stages.put(STAGE_ERROR_STATUS, responseError.getStatusCode().value());
            Map<String, Object> body = parseMap(responseError.getResponseBodyAsString());
            Object detail = body == null ? responseError.getResponseBodyAsString() : body.get("detail");
            stages.put(STAGE_ERROR_DETAIL, detail == null ? rootMessage(error) : detail);
        } else {
            stages.put(STAGE_ERROR_DETAIL, rootMessage(error));
        }
        return stages;
    }

    private String detailText(Object detail, String fallback) {
        if (detail == null) {
            return fallback;
        }
        if (detail instanceof String text) {
            return text;
        }
        try {
            return objectMapper.writeValueAsString(detail);
        } catch (Exception ignored) {
            return String.valueOf(detail);
        }
    }

    private String valueAsString(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    private String rootMessage(Throwable error) {
        Throwable cursor = error;
        while (cursor.getCause() != null) {
            cursor = cursor.getCause();
        }
        return cursor.getMessage() == null ? error.getClass().getSimpleName() : cursor.getMessage();
    }
}
