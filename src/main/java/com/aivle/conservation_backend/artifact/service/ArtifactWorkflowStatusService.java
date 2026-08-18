package com.aivle.conservation_backend.artifact.service;

import com.aivle.conservation_backend.artifact.dto.ArtifactWorkflowStatusResponse;
import com.aivle.conservation_backend.conservation_guide_ai.domain.Task;
import com.aivle.conservation_backend.conservation_guide_ai.domain.TaskStatus;
import com.aivle.conservation_backend.conservation_guide_ai.repository.TaskRepository;
import com.aivle.conservation_backend.report_ai.repository.ReportDocumentRepository;
import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import com.aivle.conservation_backend.vca.repository.AssessmentRunRepository;
import com.aivle.conservation_backend.xray_api.domain.XrayJob;
import com.aivle.conservation_backend.xray_api.domain.XrayJobStatus;
import com.aivle.conservation_backend.xray_api.repository.XrayJobRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.UUID;

@RequiredArgsConstructor
@Service
@Transactional(readOnly = true)
public class ArtifactWorkflowStatusService {

    private final ArtifactAccessService artifactAccessService;
    private final TaskRepository taskRepository;
    private final XrayJobRepository xrayJobRepository;
    private final AssessmentRunRepository assessmentRunRepository;
    private final ReportDocumentRepository reportDocumentRepository;

    public ArtifactWorkflowStatusResponse get(UUID artifactId) {
        artifactAccessService.requireArtifact(artifactId);

        String guide = guideStatus(artifactId);
        String xray = xrayStatus(artifactId);
        String visual = visualStatus(artifactId);
        boolean finalReportExists = reportDocumentRepository
                .findFirstByArtifact_IdOrderByCreatedAtDesc(artifactId)
                .isPresent();
        boolean allCompleted = "DONE".equals(guide)
                && "DONE".equals(xray)
                && "DONE".equals(visual)
                && finalReportExists;

        return new ArtifactWorkflowStatusResponse(
                artifactId,
                guide,
                xray,
                visual,
                finalReportExists,
                allCompleted
        );
    }

    private String guideStatus(UUID artifactId) {
        return taskRepository.findFirstByArtifact_IdOrderByCreatedDateDesc(artifactId)
                .map(Task::getTotalState)
                .map(status -> status == TaskStatus.COMPLETED ? "DONE" : "IN_PROGRESS")
                .orElse("NOT_STARTED");
    }

    private String xrayStatus(UUID artifactId) {
        return xrayJobRepository.findByArtifactId(artifactId)
                .map(XrayJob::getStatus)
                .map(status -> {
                    if (status == XrayJobStatus.COMPLETED) return "DONE";
                    if (status == XrayJobStatus.FAILED) return "FAILED";
                    return "IN_PROGRESS";
                })
                .orElse("NOT_STARTED");
    }

    private String visualStatus(UUID artifactId) {
        List<AssessmentRun> runs = assessmentRunRepository.findAllByArtifactIdOrderByRunNumberDesc(artifactId);
        if (runs.isEmpty()) return "NOT_STARTED";
        if (runs.stream().anyMatch(run -> "completed".equalsIgnoreCase(run.getStatus()))) return "DONE";
        if (runs.stream().anyMatch(run -> {
            String status = run.getStatus();
            return status != null && !"failed".equalsIgnoreCase(status);
        })) return "IN_PROGRESS";
        return "FAILED";
    }
}
