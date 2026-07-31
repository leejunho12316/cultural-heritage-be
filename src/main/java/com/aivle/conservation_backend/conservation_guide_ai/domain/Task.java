package com.aivle.conservation_backend.conservation_guide_ai.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

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

    // AI 쪽 _now()(KST) 포맷을 그대로 문자열로 보존 (타임존 재해석에 따른 오차 방지).
    @Column(name = "created_date")
    private String createdDate;

    @Column(name = "last_edited_date")
    private String lastEditedDate;
}
