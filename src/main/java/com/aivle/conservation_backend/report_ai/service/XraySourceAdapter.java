package com.aivle.conservation_backend.report_ai.service;

import com.aivle.conservation_backend.xray_api.domain.XrayDefect;
import com.aivle.conservation_backend.xray_api.domain.XrayDefectReviewDecision;
import com.aivle.conservation_backend.xray_api.domain.XrayJob;
import com.aivle.conservation_backend.xray_api.repository.XrayDefectRepository;
import com.aivle.conservation_backend.xray_api.repository.XrayJobRepository;

import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * X-ray가 실제로 DB(RDS)에 저장한 결과(XrayJob/XrayDefect)를,
 * report-ai가 기대하는 GenerateReportRequestDto.xrayReportText /
 * xrayRegions 형태로 변환한다.
 *
 * report-ai와 X-ray는 서로 다른 스키마로 발전해왔다 - X-ray는 리뷰 결정을
 * 대문자 enum(DAMAGE/NORMAL)으로, report-ai는 소문자 문자열("damage")로
 * 기대한다. 이 어댑터가 그 차이를 흡수한다.
 *
 * 육안조사/보존가이드는 아직 DB 저장이 없어서 이런 어댑터를 만들 수
 * 없다 - X-ray가 현재 유일하게 DB까지 연동된 파트다.
 */
@Service
public class XraySourceAdapter {

    private final XrayJobRepository xrayJobRepository;
    private final XrayDefectRepository xrayDefectRepository;

    public XraySourceAdapter(
            XrayJobRepository xrayJobRepository,
            XrayDefectRepository xrayDefectRepository
    ) {
        this.xrayJobRepository = xrayJobRepository;
        this.xrayDefectRepository = xrayDefectRepository;
    }

    /**
     * artifactId(문자열)로 X-ray job을 찾아 report-ai 입력 형태로 변환한다.
     * artifactId가 UUID 형식이 아니거나 매칭되는 X-ray job이 없으면
     * 빈 값을 반환한다 - 이 유물은 X-ray를 아직 안 했거나 진행 중일 수
     * 있으므로 예외로 취급하지 않는다.
     */
    public Optional<XraySource> resolve(String artifactId) {
        UUID uuid;
        try {
            uuid = UUID.fromString(artifactId);
        } catch (IllegalArgumentException e) {
            return Optional.empty();
        }

        return xrayJobRepository.findByArtifactId(uuid).map(this::toSource);
    }

    private XraySource toSource(XrayJob job) {
        List<Map<String, Object>> regions = xrayDefectRepository
                .findAllByXrayJob_IdAndReviewDecisionOrderByIdAsc(
                        job.getId(),
                        XrayDefectReviewDecision.DAMAGE
                )
                .stream()
                .map(this::toRegion)
                .toList();

        return new XraySource(job.getReportText(), regions);
    }

    private Map<String, Object> toRegion(XrayDefect defect) {
        Map<String, Object> region = new LinkedHashMap<>();
        region.put("region_code", "R-%03d".formatted(defect.getId()));
        // XrayDefect.geometry는 결합본 픽셀 좌표(bbox)만 갖고 있고 캔버스
        // 전체 크기는 별도 저장하지 않아서, "우측 하단" 같은 한글 3x3
        // 위치 라벨은 지금 이 정보만으로는 정확히 계산할 수 없다.
        // 좌표를 그대로 서술하는 쪽이, 틀릴 수 있는 라벨을 지어내는 것보다
        // 안전하다고 판단했다.
        region.put("position", describeGeometry(defect.getGeometry()));
        region.put("review_decision", defect.getReviewDecision().name().toLowerCase(Locale.ROOT));
        region.put("user_note", "");
        return region;
    }

    private String describeGeometry(Map<String, Object> geometry) {
        if (geometry == null) {
            return "";
        }
        Object x1 = geometry.get("x1");
        Object y1 = geometry.get("y1");
        Object x2 = geometry.get("x2");
        Object y2 = geometry.get("y2");
        if (x1 == null || y1 == null || x2 == null || y2 == null) {
            return "";
        }
        return "결합본 좌표 (%s, %s) ~ (%s, %s)".formatted(x1, y1, x2, y2);
    }

    public record XraySource(String reportText, List<Map<String, Object>> regions) {
    }
}
