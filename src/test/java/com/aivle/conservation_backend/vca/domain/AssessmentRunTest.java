package com.aivle.conservation_backend.vca.domain;

import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

class AssessmentRunTest {

    @Test
    void createLeavesUploadedImageIdsAsEmptyListNotNull() {
        AssessmentRun run = AssessmentRun.create(
                UUID.randomUUID(), UUID.randomUUID(), 1, "project", false, "cpu", null
        );

        assertThat(run.getUploadedImageIds()).isNotNull().isEmpty();
    }

    @Test
    void getUploadedImageIdsFallsBackToEmptyListWhenFieldIsNull() {
        AssessmentRun run = AssessmentRun.builder()
                .id(UUID.randomUUID())
                .artifactId(UUID.randomUUID())
                .runNumber(1)
                .status("queued")
                .progressPercent(0)
                .imageCount(0)
                .uploadedImageIds(null)
                .build();

        assertThat(run.getUploadedImageIds()).isNotNull().isEmpty();
    }

    @Test
    void getUploadedImageIdsReturnsActualListWhenSet() {
        AssessmentRun run = AssessmentRun.builder()
                .id(UUID.randomUUID())
                .artifactId(UUID.randomUUID())
                .runNumber(1)
                .status("QUEUED")
                .progressPercent(0)
                .imageCount(1)
                .uploadedImageIds(java.util.List.of("image-1"))
                .build();

        assertThat(run.getUploadedImageIds()).containsExactly("image-1");
    }
}
