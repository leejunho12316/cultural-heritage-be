package com.aivle.conservation_backend.conservation_guide_ai.dto;

import com.aivle.conservation_backend.conservation_guide_ai.domain.Task;
import com.aivle.conservation_backend.conservation_guide_ai.domain.TaskStatus;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

// DB에 저장된 Task 상태 조회용 DTO. FE가 재접속 시 진행 상황(마지막 interrupt/완료 결과)을 복구할 때 사용.
public record TaskDetailResponseDto(
        String taskId,
        String taskName,
        String taskManager,
        Map<String, Object> relicInfo,
        List<String> relicPhoto,
        List<String> flow,
        TaskStatus totalState,
        Map<String, Object> currentInterrupt,
        Map<String, Object> results,
        Map<String, Object> documentPath,
        OffsetDateTime createdDate,
        OffsetDateTime lastEditedDate
) {
    public static TaskDetailResponseDto from(Task task) {
        return new TaskDetailResponseDto(
                task.getTaskId(),
                task.getTaskName(),
                task.getTaskManager(),
                task.getRelicInfo(),
                task.getRelicPhoto(),
                task.getFlow(),
                task.getTotalState(),
                task.getCurrentInterrupt(),
                task.getResults(),
                task.getDocumentPath(),
                task.getCreatedDate(),
                task.getLastEditedDate()
        );
    }
}
