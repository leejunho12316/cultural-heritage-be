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
        // assessment_run은 VCA 상태조사와 문양조사가 공유한다. 문양조사
        // POTTERY_PATTERN run까지 섞으면 문양조사만 완료돼도 육안 상태 조사가
        // DONE으로 보일 수 있으므로 VCA run만 상태 판정에 사용한다.
        List<AssessmentRun> runs = assessmentRunRepository
                .findAllByArtifactIdOrderByRunNumberDesc(artifactId)
                .stream()
                .filter(this::isVcaRun)
                .toList();
        if (runs.isEmpty()) return "NOT_STARTED";
        if (runs.stream().anyMatch(run -> "completed".equalsIgnoreCase(run.getStatus()))) return "DONE";
        if (runs.stream().anyMatch(run -> {
            String status = run.getStatus();
            return status != null && !"failed".equalsIgnoreCase(status);
        })) return "IN_PROGRESS";
        return "FAILED";
    }

    private boolean isVcaRun(AssessmentRun run) {
        if (run == null) return false;
        String runType = run.getRunType();
        return runType == null
                || runType.isBlank()
                || AssessmentRun.RUN_TYPE_VCA.equals(runType);
    }
}
