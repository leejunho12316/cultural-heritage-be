package com.aivle.conservation_backend.conservation_guide_ai;

import com.aivle.conservation_backend.artifact.domain.Artifact;
import com.aivle.conservation_backend.artifact.repository.ArtifactRepository;
import com.aivle.conservation_backend.conservation_guide_ai.client.ConservationGuideAiClient;
import com.aivle.conservation_backend.conservation_guide_ai.domain.Task;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiResponseDto;
import com.aivle.conservation_backend.conservation_guide_ai.dto.ConservationGuideAiStartRequestDto;
import com.aivle.conservation_backend.conservation_guide_ai.service.TaskService;
import com.aivle.conservation_backend.user.domain.Role;
import com.aivle.conservation_backend.user.domain.User;
import com.aivle.conservation_backend.user.repository.UserRepository;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

/**
 * TaskServiceTest는 전부 Mockito로 리포지토리를 목킹했다 - 실제 SQL이
 * 한 번도 안 나갔다는 뜻이다. 이 테스트는 그 갭을 메운다: 진짜 Spring
 * 컨텍스트 + 진짜(내장 H2, Postgres 호환 모드) DB로 Artifact -&gt; Task
 * FK 저장/조회를 끝까지 돌려서, tasks.artifact_id 컬럼 매핑과
 * findFirstByArtifact_IdOrderByCreatedDateDesc 쿼리가 실제 SQL
 * 레벨에서 맞는지 확인한다.
 *
 * 보존 가이드 AI(FastAPI/LangGraph) 호출 자체는 이 저장소만으로는
 * 재현할 수 없는 외부 의존성이라 목으로 대체한다.
 */
@SpringBootTest
@ActiveProfiles("test")
@TestPropertySource(properties = "spring.jpa.hibernate.ddl-auto=create-drop")
@Transactional
class TaskArtifactPersistenceTest {

    @Autowired
    private ArtifactRepository artifactRepository;

    @Autowired
    private TaskService taskService;

    @Autowired
    private UserRepository userRepository;

    @MockitoBean
    private ConservationGuideAiClient client;

    @Test
    void artifact_id로_task를_저장하고_실제_DB에서_다시_조회된다() {
        // 1. artifacts.user_id는 운영 RDS에서 NOT NULL이므로 테스트 유물도 실제 owner를 저장한다.
        User owner = userRepository.save(User.builder()
                .loginId("task-persistence-user")
                .email("task-persistence@example.com")
                .password("test-password")
                .nickname("task tester")
                .role(Role.USER)
                .build());

        Artifact artifact = artifactRepository.save(
                Artifact.builder()
                        .owner(owner)
                        .name("청자기린향로")
                        .category("도자기")
                        .material("청자")
                        .build()
        );
        assertThat(artifact.getId()).isNotNull();

        // 2. 보존 가이드 AI 호출만 목킹 - 나머지는 전부 실제 빈/DB를 탄다.
        when(client.startTask(eq("task-real-1"), any()))
                .thenReturn(new ConservationGuideAiResponseDto(
                        "waiting_for_input",
                        Map.of("step", "checklist"),
                        null
                ));

        // 3. 시작 - tasks에 artifact_id FK를 포함해 실제 INSERT.
        taskService.startTask("task-real-1", new ConservationGuideAiStartRequestDto(
                artifact.getId().toString(),
                "문화재 복원",
                "오서하",
                Map.of("name", "청자기린향로"),
                List.of(),
                List.of("disassembly", "cleaning")
        ));

        // 4. artifactId만으로 조회 - taskId를 몰라도 복구되는지가 이 테스트의 핵심.
        Task found = taskService.getLatestTaskByArtifact(artifact.getId());

        assertThat(found.getTaskId()).isEqualTo("task-real-1");
        assertThat(found.getArtifact().getId()).isEqualTo(artifact.getId());
        assertThat(found.getTaskName()).isEqualTo("문화재 복원");
    }

    @Test
    void 해당_유물의_작업이_없으면_실제_리포지토리_조회_후_404를_던진다() {
        UUID randomArtifactId = UUID.randomUUID();

        assertThatThrownBy(() -> taskService.getLatestTaskByArtifact(randomArtifactId))
                .isInstanceOf(ResponseStatusException.class);
    }
}
