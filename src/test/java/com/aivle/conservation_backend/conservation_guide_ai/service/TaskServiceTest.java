package com.aivle.conservation_backend.conservation_guide_ai.service;

import com.aivle.conservation_backend.artifact.domain.Artifact;
import com.aivle.conservation_backend.artifact.repository.ArtifactRepository;
import com.aivle.conservation_backend.conservation_guide_ai.client.ConservationGuideAiClient;
import com.aivle.conservation_backend.conservation_guide_ai.domain.Task;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiResponseDto;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiStartRequestDto;
import com.aivle.conservation_backend.conservation_guide_ai.repository.TaskRepository;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * artifact_id 연결(TaskService.resolveArtifact / getLatestTaskByArtifact)만
 * 검증한다 - startTask/resumeTask의 나머지 upsert 동작은 이미 운영 중이던
 * 기존 로직이라 이번 변경 범위가 아니다.
 */
@ExtendWith(MockitoExtension.class)
class TaskServiceTest {

    @Mock
    private ConservationGuideAiClient client;

    @Mock
    private TaskRepository taskRepository;

    @Mock
    private ArtifactRepository artifactRepository;

    private TaskService service() {
        return new TaskService(client, taskRepository, artifactRepository);
    }

    private Artifact artifactWithId(UUID id) {
        Artifact artifact = Artifact.builder().name("청자기린향로").build();
        ReflectionTestUtils.setField(artifact, "id", id);
        return artifact;
    }

    private ConservationGuideAiStartRequestDto requestWithArtifactId(String artifactId) {
        return new ConservationGuideAiStartRequestDto(
                artifactId, "문화재 복원", "오서하", Map.of(), List.of(), List.of()
        );
    }

    @Test
    void artifactId가_유효한_UUID이고_매칭되는_유물이_있으면_task에_연결한다() {
        UUID artifactId = UUID.randomUUID();
        Artifact artifact = artifactWithId(artifactId);
        when(taskRepository.findById("task-1")).thenReturn(Optional.empty());
        when(artifactRepository.findById(artifactId)).thenReturn(Optional.of(artifact));
        when(client.startTask(eq("task-1"), any()))
                .thenReturn(new ConservationGuideAiResponseDto("waiting_for_input", Map.of("step", "checklist"), null));

        service().startTask("task-1", requestWithArtifactId(artifactId.toString()));

        ArgumentCaptor<Task> captor = ArgumentCaptor.forClass(Task.class);
        verify(taskRepository).save(captor.capture());
        assertThat(captor.getValue().getArtifact()).isEqualTo(artifact);
    }

    @Test
    void artifactId가_없으면_task_artifact를_비워둔_채_정상적으로_시작한다() {
        when(taskRepository.findById("task-2")).thenReturn(Optional.empty());
        when(client.startTask(eq("task-2"), any()))
                .thenReturn(new ConservationGuideAiResponseDto("waiting_for_input", Map.of(), null));

        service().startTask("task-2", requestWithArtifactId(null));

        ArgumentCaptor<Task> captor = ArgumentCaptor.forClass(Task.class);
        verify(taskRepository).save(captor.capture());
        assertThat(captor.getValue().getArtifact()).isNull();
        verify(artifactRepository, never()).findById(any());
    }

    @Test
    void artifactId가_UUID형식이_아니면_무시하고_정상적으로_시작한다() {
        when(taskRepository.findById("task-3")).thenReturn(Optional.empty());
        when(client.startTask(eq("task-3"), any()))
                .thenReturn(new ConservationGuideAiResponseDto("waiting_for_input", Map.of(), null));

        service().startTask("task-3", requestWithArtifactId("not-a-uuid"));

        ArgumentCaptor<Task> captor = ArgumentCaptor.forClass(Task.class);
        verify(taskRepository).save(captor.capture());
        assertThat(captor.getValue().getArtifact()).isNull();
        verify(artifactRepository, never()).findById(any());
    }

    @Test
    void 매칭되는_유물이_없으면_task_artifact를_비워둔_채_정상적으로_시작한다() {
        UUID artifactId = UUID.randomUUID();
        when(taskRepository.findById("task-4")).thenReturn(Optional.empty());
        when(artifactRepository.findById(artifactId)).thenReturn(Optional.empty());
        when(client.startTask(eq("task-4"), any()))
                .thenReturn(new ConservationGuideAiResponseDto("waiting_for_input", Map.of(), null));

        service().startTask("task-4", requestWithArtifactId(artifactId.toString()));

        ArgumentCaptor<Task> captor = ArgumentCaptor.forClass(Task.class);
        verify(taskRepository).save(captor.capture());
        assertThat(captor.getValue().getArtifact()).isNull();
    }

    @Test
    void 해당_유물의_작업이_없으면_404를_던진다() {
        UUID artifactId = UUID.randomUUID();
        when(taskRepository.findFirstByArtifact_IdOrderByCreatedDateDesc(artifactId))
                .thenReturn(Optional.empty());

        assertThatThrownBy(() -> service().getLatestTaskByArtifact(artifactId))
                .isInstanceOf(ResponseStatusException.class);
    }

    @Test
    void 해당_유물의_최신_작업을_조회한다() {
        UUID artifactId = UUID.randomUUID();
        Task task = Task.builder().taskId("task-5").build();
        when(taskRepository.findFirstByArtifact_IdOrderByCreatedDateDesc(artifactId))
                .thenReturn(Optional.of(task));

        Task result = service().getLatestTaskByArtifact(artifactId);

        assertThat(result.getTaskId()).isEqualTo("task-5");
    }
}
