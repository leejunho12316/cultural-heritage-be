package com.aivle.conservation_backend.report_ai.service;

import com.aivle.conservation_backend.vca.domain.InspectionResultPottery;
import com.aivle.conservation_backend.vca.repository.InspectionResultPotteryRepository;
import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import com.aivle.conservation_backend.vca.repository.AssessmentRunRepository;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PotterySourceAdapterTest {

    @Mock
    private AssessmentRunRepository assessmentRunRepository;

    @Mock
    private InspectionResultPotteryRepository inspectionResultPotteryRepository;

    private PotterySourceAdapter adapter() {
        return new PotterySourceAdapter(assessmentRunRepository, inspectionResultPotteryRepository);
    }

    private AssessmentRun run(UUID id, UUID artifactId, int runNumber) {
        return AssessmentRun.builder()
                .id(id)
                .artifactId(artifactId)
                .runNumber(runNumber)
                .status("queued")
                .dryRun(false)
                .progressPercent(0)
                .build();
    }

    @Test
    void artifactId가_UUID가_아니면_빈값을_반환한다() {
        Optional<PotterySourceAdapter.PotterySource> result = adapter().resolve("not-a-uuid");

        assertThat(result).isEmpty();
    }

    @Test
    void AssessmentRun이_없으면_빈값을_반환한다() {
        UUID artifactId = UUID.randomUUID();
        when(assessmentRunRepository.findAllByArtifactIdOrderByRunNumberDesc(artifactId))
                .thenReturn(List.of());

        Optional<PotterySourceAdapter.PotterySource> result = adapter().resolve(artifactId.toString());

        assertThat(result).isEmpty();
    }

    @Test
    void run은_있지만_육안조사_결과가_아직_없으면_빈값을_반환한다() {
        UUID artifactId = UUID.randomUUID();
        AssessmentRun latestRun = run(UUID.randomUUID(), artifactId, 2);

        when(assessmentRunRepository.findAllByArtifactIdOrderByRunNumberDesc(artifactId))
                .thenReturn(List.of(latestRun));
        when(inspectionResultPotteryRepository
                .findByAssessmentRunId(latestRun.getId()))
                .thenReturn(Optional.empty());

        Optional<PotterySourceAdapter.PotterySource> result = adapter().resolve(artifactId.toString());

        assertThat(result).isEmpty();
    }

    @Test
    void 여러_run_중_가장_최근_run의_결과를_변환한다() {
        UUID artifactId = UUID.randomUUID();
        // findAllByArtifactIdOrderByRunNumberDesc 계약상 첫 번째가 최신 run(run_number 내림차순)이다.
        AssessmentRun latestRun = run(UUID.randomUUID(), artifactId, 2);
        AssessmentRun olderRun = run(UUID.randomUUID(), artifactId, 1);

        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("crack_ratio", 0.12);

        InspectionResultPottery inspection = InspectionResultPottery.builder()
                .id(UUID.randomUUID())
                .assessmentRunId(latestRun.getId())
                .inspectionText("표면에 미세한 균열이 다수 관찰됨.")
                .humanReviewRecommended(true)
                .detail(detail)
                .createdAt(Instant.now())
                .build();

        when(assessmentRunRepository.findAllByArtifactIdOrderByRunNumberDesc(artifactId))
                .thenReturn(List.of(latestRun, olderRun));
        when(inspectionResultPotteryRepository
                .findByAssessmentRunId(latestRun.getId()))
                .thenReturn(Optional.of(inspection));

        Optional<PotterySourceAdapter.PotterySource> result = adapter().resolve(artifactId.toString());

        assertThat(result).isPresent();
        PotterySourceAdapter.PotterySource source = result.get();
        assertThat(source.inspectionText()).isEqualTo("표면에 미세한 균열이 다수 관찰됨.");
        assertThat(source.humanReviewRecommended()).isTrue();
        assertThat(source.detail()).containsEntry("crack_ratio", 0.12);

        // report-ai가 실제로 소비하는 pottery_inspection 맵 형태(snake_case) 검증.
        Map<String, Object> map = source.asMap();
        assertThat(map.get("inspection_text")).isEqualTo("표면에 미세한 균열이 다수 관찰됨.");
        assertThat(map.get("human_review_recommended")).isEqualTo(true);
    }
}
