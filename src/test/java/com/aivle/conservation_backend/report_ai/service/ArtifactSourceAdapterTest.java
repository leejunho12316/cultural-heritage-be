package com.aivle.conservation_backend.report_ai.service;

import com.aivle.conservation_backend.artifact.domain.Artifact;
import com.aivle.conservation_backend.artifact.repository.ArtifactRepository;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ArtifactSourceAdapterTest {

    @Mock
    private ArtifactRepository artifactRepository;

    private ArtifactSourceAdapter adapter() {
        return new ArtifactSourceAdapter(artifactRepository);
    }

    private Artifact artifactWithId(UUID id) {
        Artifact artifact = Artifact.builder()
                .name("청자상감운학문매병")
                .category("도자기")
                .material("청자")
                .era("고려")
                .description("본문 설명")
                .conditionSummary("전체적으로 양호")
                .weight("1.2kg")
                .bondingArea("동체 하단")
                .treatmentPurpose("전시 보존")
                .build();
        ReflectionTestUtils.setField(artifact, "id", id);
        return artifact;
    }

    @Test
    void artifactId가_UUID가_아니면_빈값을_반환한다() {
        Optional<Map<String, Object>> result = adapter().resolve("not-a-uuid");

        assertThat(result).isEmpty();
    }

    @Test
    void 매칭되는_유물이_없으면_빈값을_반환한다() {
        UUID artifactId = UUID.randomUUID();
        when(artifactRepository.findById(artifactId)).thenReturn(Optional.empty());

        Optional<Map<String, Object>> result = adapter().resolve(artifactId.toString());

        assertThat(result).isEmpty();
    }

    @Test
    void 유물_정보를_report_ai가_기대하는_relic_info_형태로_변환한다() {
        UUID artifactId = UUID.randomUUID();
        Artifact artifact = artifactWithId(artifactId);
        when(artifactRepository.findById(artifactId)).thenReturn(Optional.of(artifact));

        Optional<Map<String, Object>> result = adapter().resolve(artifactId.toString());

        assertThat(result).isPresent();
        Map<String, Object> relicInfo = result.get();
        assertThat(relicInfo.get("id")).isEqualTo(artifactId.toString());
        assertThat(relicInfo.get("artifact_code")).isEqualTo(artifactId.toString());
        assertThat(relicInfo.get("name")).isEqualTo("청자상감운학문매병");
        assertThat(relicInfo.get("material")).isEqualTo("청자");
        // Artifact.era -> report-ai가 기대하는 relic_info.period로 이름이 바뀌는지가
        // 이 어댑터의 핵심 검증 포인트다.
        assertThat(relicInfo.get("period")).isEqualTo("고려");
        assertThat(relicInfo.get("weight")).isEqualTo("1.2kg");
        assertThat(relicInfo.get("bondingArea")).isEqualTo("동체 하단");
        assertThat(relicInfo.get("treatmentPurpose")).isEqualTo("전시 보존");
    }
}
