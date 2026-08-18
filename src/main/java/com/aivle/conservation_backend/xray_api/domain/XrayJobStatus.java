package com.aivle.conservation_backend.xray_api.domain;

/**
 * X-ray 전체 업무 진행 상태.
 *
 * <p>자동 결합 한 번의 기술 상태가 아니라, 유물 하나의 X-ray 파트가
 * 결합 → 최종 보정 → 결함 분석 → 전문가 검수 → 문안 확정까지 진행되는
 * 전체 업무 상태를 나타낸다.</p>
 */
public enum XrayJobStatus {
    PREPARED,
    UPLOADING,
    STITCHING,
    STITCHED,
    /** 이전 버전과 기존 DB 행 호환용 통합 탐지 상태. */
    DETECTING,
    DETECTING_FRAGMENTS,
    DETECTING_ASSEMBLED,
    MAPPING,
    REVIEW_READY,
    REPORT_READY,
    REPORTING,
    COMPLETED,
    FAILED;

    public boolean isDetectionInProgress() {
        return this == DETECTING
                || this == DETECTING_FRAGMENTS
                || this == DETECTING_ASSEMBLED
                || this == MAPPING;
    }

    public boolean isReportInProgress() {
        return this == REPORTING;
    }
}