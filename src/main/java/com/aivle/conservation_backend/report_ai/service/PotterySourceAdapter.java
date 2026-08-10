package com.aivle.conservation_backend.report_ai.service;

import com.aivle.conservation_backend.pottery_inspection_ai.domain.InspectionResultPottery;
import com.aivle.conservation_backend.pottery_inspection_ai.repository.InspectionResultPotteryRepository;
import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import com.aivle.conservation_backend.vca.repository.AssessmentRunRepository;

import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * 육안조사(pottery-inspection-ai)가 실제로 DB(RDS)에 저장한 결과
 * (AssessmentRun -&gt; InspectionResultPottery)를, report-ai가 기대하는
 * GenerateReportRequestDto.potteryInspection 형태로 변환한다.
 *
 * X-ray는 유물당 job이 하나라고 가정하지만([[XraySourceAdapter]] 참고),
 * 육안조사는 AssessmentRun이 (artifact_id, run_number)로 재실행을
 * 지원한다 - 재실행 중 가장 최근 run을 기준으로 삼는다. 그 run에 아직
 * InspectionResultPottery가 없으면(진행 중이거나 이 파트가 아직 실행 전인
 * 경우) 빈 값을 반환한다 - 예외로 취급하지 않는다.
 *
 * report-ai는 지금 pottery_inspection.inspection_text만 실제로 읽지만
 * (app/nodes/pre_investigation.py), human_review_recommended/detail도
 * 함께 넘겨서 이후 report-ai가 더 쓰게 되더라도 어댑터를 다시 안 만들어도
 * 되게 한다.
 */
@Service
public class PotterySourceAdapter {

    private final AssessmentRunRepository assessmentRunRepository;
    private final InspectionResultPotteryRepository inspectionResultPotteryRepository;

    public PotterySourceAdapter(
            AssessmentRunRepository assessmentRunRepository,
            InspectionResultPotteryRepository inspectionResultPotteryRepository
    ) {
        this.assessmentRunRepository = assessmentRunRepository;
        this.inspectionResultPotteryRepository = inspectionResultPotteryRepository;
    }

    /**
     * artifactId(문자열)로 가장 최근 AssessmentRun을 찾고, 그 run의 육안조사
     * 결과를 report-ai 입력 형태로 변환한다. artifactId가 UUID 형식이
     * 아니거나, run이 없거나, run은 있는데 아직 육안조사 결과가 없으면
     * 빈 값을 반환한다.
     */
    public Optional<PotterySource> resolve(String artifactId) {
        UUID uuid;
        try {
            uuid = UUID.fromString(artifactId);
        } catch (IllegalArgumentException e) {
            return Optional.empty();
        }

        List<AssessmentRun> runs = assessmentRunRepository.findAllByArtifactIdOrderByRunNumberDesc(uuid);
        if (runs.isEmpty()) {
            return Optional.empty();
        }
        AssessmentRun latestRun = runs.get(0);

        return inspectionResultPotteryRepository
                .findFirstByAssessmentRun_IdAndAssessmentRun_ArtifactIdOrderByCreatedAtDesc(latestRun.getId(), uuid)
                .map(this::toSource);
    }

    private PotterySource toSource(InspectionResultPottery result) {
        return new PotterySource(
                result.getInspectionText(),
                result.isHumanReviewRecommended(),
                result.getDetail()
        );
    }

    public record PotterySource(
            String inspectionText,
            boolean humanReviewRecommended,
            Map<String, Object> detail
    ) {
        public Map<String, Object> asMap() {
            Map<String, Object> map = new LinkedHashMap<>();
            map.put("inspection_text", inspectionText);
            map.put("human_review_recommended", humanReviewRecommended);
            map.put("detail", detail);
            return map;
        }
    }
}
