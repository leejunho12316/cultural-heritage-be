package com.aivle.conservation_backend.conservation_guide_ai.domain;

import com.aivle.conservation_backend.artifact.domain.Artifact;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

// 보존처리 Task(작업 단위)의 영속 상태.
// start/resume 호출마다 보존 가이드 AI(FastAPI/LangGraph)의 응답을 반영해 upsert 된다.
@Entity
@Table(name = "tasks")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class Task {

    @Id
    @Column(name = "task_id")
    private String taskId;

    @Column(name = "task_name")
    private String taskName;

    @Column(name = "task_manager")
    private String taskManager;

    // 이 작업이 어느 유물에 대한 것인지. taskId는 FE가 새로고침하면 사라지는
    // React Context에만 살아있어 그것만으로는 진행상황을 복구할 수 없다 -
    // artifactId(URL 파라미터로 항상 남아있음) 기준 조회(TaskRepository 참고)를
    // 붙이기 위해 추가됐다. 이 컬럼이 생기기 전에 저장된 row는 null일 수 있어
    // nullable로 둔다(과거 데이터 백필 불가 - 원본 유물 매칭 정보 자체가 없음).
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "artifact_id")
    private Artifact artifact;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "relic_info", columnDefinition = "jsonb")
    private Map<String, Object> relicInfo;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "relic_photo", columnDefinition = "jsonb")
    private List<String> relicPhoto;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "flow", columnDefinition = "jsonb")
    private List<String> flow;

    @Enumerated(EnumType.STRING)
    @Column(name = "total_state")
    private TaskStatus totalState;

    // 마지막으로 받은 interrupt payload. FE가 재접속 시 어디서 이어할지 복구하는 용도.
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "current_interrupt", columnDefinition = "jsonb")
    private Map<String, Object> currentInterrupt;

    // status == "completed" 응답의 전체 result(state) 저장.
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "results", columnDefinition = "jsonb")
    private Map<String, Object> results;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "document_path", columnDefinition = "jsonb")
    private Map<String, Object> documentPath;

    // 다른 테이블들(artifacts.created_at 등)과 동일하게 timestamptz로 통일.
    // 이전엔 문자열로 그대로 보존했지만(타임존 재해석 오차 방지 목적), 실제로는
    // TaskService.nowKst()가 Spring 쪽에서 직접 계산한 값이라 AI 응답 문자열을
    // 그대로 베끼는 게 아니었고, OffsetDateTime을 쓰면 오프셋(+09:00)이 값
    // 자체에 포함돼 있어 같은 문제가 생기지 않는다.
    @Column(name = "created_date")
    private OffsetDateTime createdDate;

    @Column(name = "last_edited_date")
    private OffsetDateTime lastEditedDate;
}
