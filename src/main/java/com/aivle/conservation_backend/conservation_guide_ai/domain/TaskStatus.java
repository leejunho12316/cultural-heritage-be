package com.aivle.conservation_backend.conservation_guide_ai.domain;

// 보존 가이드 AI state.py의 total_state 리터럴("draft","in_progress","paused","completed")과 매핑되는 상태값.
public enum TaskStatus {
    DRAFT,
    IN_PROGRESS,
    PAUSED,
    COMPLETED;

    // AI 응답의 total_state(snake_case 문자열)를 TaskStatus로 변환.
    public static TaskStatus fromAiState(String aiState) {
        if (aiState == null) {
            return IN_PROGRESS;
        }
        return switch (aiState) {
            case "draft" -> DRAFT;
            case "paused" -> PAUSED;
            case "completed" -> COMPLETED;
            default -> IN_PROGRESS; // "in_progress" 및 알 수 없는 값은 진행중으로 처리
        };
    }
}
