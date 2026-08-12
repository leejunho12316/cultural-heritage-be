package com.aivle.conservation_backend.report_ai.service;

import com.aivle.conservation_backend.conservation_guide_ai.domain.Task;
import com.aivle.conservation_backend.conservation_guide_ai.repository.TaskRepository;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ConservationGuideSourceAdapterTest {

    @Mock
    private TaskRepository taskRepository;

    private ConservationGuideSourceAdapter adapter() {
        return new ConservationGuideSourceAdapter(taskRepository);
    }

    @Test
    void artifactId가_UUID가_아니면_빈값을_반환한다() {
        Optional<Map<String, Object>> result = adapter().resolve("not-a-uuid");

        assertThat(result).isEmpty();
    }

    @Test
    void 매칭되는_작업이_없으면_빈값을_반환한다() {
        UUID artifactId = UUID.randomUUID();
        when(taskRepository.findFirstByArtifact_IdOrderByCreatedDateDesc(artifactId))
                .thenReturn(Optional.empty());

        Optional<Map<String, Object>> result = adapter().resolve(artifactId.toString());

        assertThat(result).isEmpty();
    }

    @Test
    void 작업은_있지만_아직_완료된_단계가_없으면_빈값을_반환한다() {
        UUID artifactId = UUID.randomUUID();
        Task task = Task.builder().taskId("task-1").results(Map.of()).build();
        when(taskRepository.findFirstByArtifact_IdOrderByCreatedDateDesc(artifactId))
                .thenReturn(Optional.of(task));

        Optional<Map<String, Object>> result = adapter().resolve(artifactId.toString());

        assertThat(result).isEmpty();
    }

    @Test
    void results를_report_ai가_기대하는_guide_result_형태로_그대로_넘긴다() {
        UUID artifactId = UUID.randomUUID();
        Map<String, Object> results = Map.of(
                "disassembly", Map.of("status", "completed", "memo", "볼트 4개 제거"),
                "cleaning", Map.of("status", "skipped")
        );
        Task task = Task.builder().taskId("task-2").results(results).build();
        when(taskRepository.findFirstByArtifact_IdOrderByCreatedDateDesc(artifactId))
                .thenReturn(Optional.of(task));

        Optional<Map<String, Object>> result = adapter().resolve(artifactId.toString());

        assertThat(result).isPresent();
        // report-ai의 guide_stage 노드가 stage_key로 직접 꺼내 쓰므로,
        // 변환 없이 그대로 전달되는지가 이 어댑터의 핵심 검증 포인트다.
        assertThat(result.get()).isEqualTo(results);
    }
}
