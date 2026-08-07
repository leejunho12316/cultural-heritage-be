package com.aivle.conservation_backend.xray_api.domain;

public enum XrayJobStatus {
    PENDING,
    RUNNING,
    COMPLETED,
    FINALIZING,
    FINALIZED,
    FAILED
}
