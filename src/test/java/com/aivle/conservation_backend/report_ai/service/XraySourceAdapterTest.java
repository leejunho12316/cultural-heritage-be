package com.aivle.conservation_backend.report_ai.service;

import com.aivle.conservation_backend.xray_api.domain.XrayDefect;
import com.aivle.conservation_backend.xray_api.domain.XrayDefectOriginType;
import com.aivle.conservation_backend.xray_api.domain.XrayDefectReviewDecision;
import com.aivle.conservation_backend.xray_api.domain.XrayJob;
import com.aivle.conservation_backend.xray_api.repository.XrayDefectRepository;
import com.aivle.conservation_backend.xray_api.repository.XrayJobRepository;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class XraySourceAdapterTest {

    @Mock
    private XrayJobRepository xrayJobRepository;

    @Mock
    private XrayDefectRepository xrayDefectRepository;

    private XraySourceAdapter adapter() {
        return new XraySourceAdapter(xrayJobRepository, xrayDefectRepository);
    }

    private XrayDefect defectWithId(long id, XrayJob job, Map<String, Object> geometry) {
        XrayDefect defect = XrayDefect.create(job, XrayDefectOriginType.MATCHED, geometry);
        ReflectionTestUtils.setField(defect, "id", id);
        return defect;
    }

    private Map<String, Object> bbox(double x1, double y1, double x2, double y2) {
        Map<String, Object> geometry = new LinkedHashMap<>();
        geometry.put("type", "bbox");
        geometry.put("x1", x1);
        geometry.put("y1", y1);
        geometry.put("x2", x2);
        geometry.put("y2", y2);
        return geometry;
    }

    @Test
    void artifactId가_UUID가_아니면_빈값을_반환한다() {
        Optional<XraySourceAdapter.XraySource> result = adapter().resolve("not-a-uuid");

        assertThat(result).isEmpty();
    }

    @Test
    void 매칭되는_Xray_job이_없으면_빈값을_반환한다() {
        UUID artifactId = UUID.randomUUID();
        when(xrayJobRepository.findByArtifactId(artifactId)).thenReturn(Optional.empty());

        Optional<XraySourceAdapter.XraySource> result = adapter().resolve(artifactId.toString());

        assertThat(result).isEmpty();
    }

    @Test
    void NORMAL로_확정된_결함은_제외하고_DAMAGE만_변환한다() {
        UUID artifactId = UUID.randomUUID();
        XrayJob job = XrayJob.create(UUID.randomUUID(), artifactId, null, 3, 5);
        job.updateReportText("측면 X-ray 촬영 결과 이상영역이 확인되었다.");

        XrayDefect damage = defectWithId(1L, job, bbox(120, 340, 210, 410));
        // DAMAGE는 XrayDefect.create()의 기본값이라 별도 처리 불필요.

        when(xrayJobRepository.findByArtifactId(artifactId)).thenReturn(Optional.of(job));
        when(xrayDefectRepository.findAllByXrayJob_IdAndReviewDecisionOrderByIdAsc(
                job.getId(), XrayDefectReviewDecision.DAMAGE
        )).thenReturn(List.of(damage));

        Optional<XraySourceAdapter.XraySource> result = adapter().resolve(artifactId.toString());

        assertThat(result).isPresent();
        XraySourceAdapter.XraySource source = result.get();
        assertThat(source.reportText()).isEqualTo("측면 X-ray 촬영 결과 이상영역이 확인되었다.");
        assertThat(source.regions()).hasSize(1);

        Map<String, Object> region = source.regions().get(0);
        assertThat(region.get("region_code")).isEqualTo("R-001");
        // report-ai는 review_decision을 소문자로 기대한다 - X-ray는 대문자 enum(DAMAGE)을 쓰므로
        // 이 변환이 정확히 이뤄지는지가 이 어댑터의 핵심 검증 포인트다.
        assertThat(region.get("review_decision")).isEqualTo("damage");
        assertThat(region.get("position")).isEqualTo("결합본 좌표 (120.0, 340.0) ~ (210.0, 410.0)");
        assertThat(region.get("user_note")).isEqualTo("");
    }

    @Test
    void reportText가_없으면_빈문자열을_반환한다() {
        UUID artifactId = UUID.randomUUID();
        XrayJob job = XrayJob.create(UUID.randomUUID(), artifactId, null, 3, 5);
        // updateReportText를 호출하지 않음 - 아직 report-text/generate 전 단계.

        when(xrayJobRepository.findByArtifactId(artifactId)).thenReturn(Optional.of(job));
        when(xrayDefectRepository.findAllByXrayJob_IdAndReviewDecisionOrderByIdAsc(
                job.getId(), XrayDefectReviewDecision.DAMAGE
        )).thenReturn(List.of());

        Optional<XraySourceAdapter.XraySource> result = adapter().resolve(artifactId.toString());

        assertThat(result).isPresent();
        assertThat(result.get().reportText()).isNull();
        assertThat(result.get().regions()).isEmpty();
    }
}
