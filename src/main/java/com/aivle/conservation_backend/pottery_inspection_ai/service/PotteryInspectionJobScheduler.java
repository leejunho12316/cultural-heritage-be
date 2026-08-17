package com.aivle.conservation_backend.pottery_inspection_ai.service;

import com.aivle.conservation_backend.vca.domain.AssessmentRun;
import com.aivle.conservation_backend.vca.repository.AssessmentRunRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * FE가 페이지를 떠나도 완료 결과를 RDS에 저장하기 위한 서버 폴러.
 * FastAPI job 상태 조회는 짧은 GET이라 ALB 장시간 연결과 무관하다.
 */
@RequiredArgsConstructor
@Component
public class PotteryInspectionJobScheduler {

    private static final List<String> ACTIVE_STATUSES = List.of("queued", "running");

    private final AssessmentRunRepository assessmentRunRepository;
    private final PotteryInspectionJobService jobService;

    @Scheduled(fixedDelayString = "${pottery-inspection-ai.poll-interval-ms:2000}")
    public void refreshActiveJobs() {
        List<AssessmentRun> activeRuns = assessmentRunRepository
                .findAllByStatusInAndAiRunIdIsNotNull(ACTIVE_STATUSES);

        for (AssessmentRun run : activeRuns) {
            try {
                jobService.getJob(run.getArtifactId(), run.getId());
            } catch (RuntimeException error) {
                // 한 job의 일시 오류가 다른 job 폴링까지 막지 않게 한다.
                System.err.println(
                        "[POTTERY] job poll failed run=" + run.getId() + ": " + error.getMessage()
                );
            }
        }
    }
}
