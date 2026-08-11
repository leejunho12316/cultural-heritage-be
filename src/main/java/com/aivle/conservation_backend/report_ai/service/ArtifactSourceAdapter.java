package com.aivle.conservation_backend.report_ai.service;

import com.aivle.conservation_backend.artifact.domain.Artifact;
import com.aivle.conservation_backend.artifact.repository.ArtifactRepository;

import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * 유물 기본정보(`artifacts` 테이블)를, report-ai가 기대하는
 * GenerateReportRequestDto.relicInfo 형태로 변환한다.
 *
 * X-ray/육안조사와 달리 이 어댑터는 "재실행"이나 "run" 개념이 없다 -
 * artifact 하나당 행이 하나뿐이라 단순 findById면 충분하다.
 *
 * report-ai `header_node`가 읽는 키: artifact_code/id(관리번호로 폴백),
 * name/material/period/weight/bondingArea/treatmentPurpose. `Artifact`
 * 엔티티의 `era` 필드를 report-ai가 기대하는 `period` 키로 이름만 바꿔서
 * 넘긴다 - 컬럼명이 서로 다르게 발전해온 부분이라 이 어댑터가 흡수한다.
 *
 * 육안조사(VCA v2) BE 작업이 아직 진행 중이라, 지금은 이 어댑터만 우선
 * 연결한다 - 육안조사 쪽 어댑터는 해당 BE 엔드포인트가 생긴 뒤에 진행.
 */
@Service
public class ArtifactSourceAdapter {

    private final ArtifactRepository artifactRepository;

    public ArtifactSourceAdapter(ArtifactRepository artifactRepository) {
        this.artifactRepository = artifactRepository;
    }

    /**
     * artifactId(문자열)로 유물 기본정보를 찾아 report-ai 입력 형태로
     * 변환한다. artifactId가 UUID 형식이 아니거나 매칭되는 유물이 없으면
     * 빈 값을 반환한다 - 아직 등록 전이거나 삭제됐을 수 있으므로 예외로
     * 취급하지 않는다.
     */
    public Optional<Map<String, Object>> resolve(String artifactId) {
        UUID uuid;
        try {
            uuid = UUID.fromString(artifactId);
        } catch (IllegalArgumentException e) {
            return Optional.empty();
        }

        return artifactRepository.findById(uuid).map(this::toRelicInfo);
    }

    private Map<String, Object> toRelicInfo(Artifact artifact) {
        String id = artifact.getId().toString();

        Map<String, Object> relicInfo = new LinkedHashMap<>();
        relicInfo.put("id", id);
        relicInfo.put("artifact_code", id);
        relicInfo.put("name", artifact.getName());
        relicInfo.put("material", artifact.getMaterial());
        // Artifact.era -> report-ai가 기대하는 relic_info.period로 이름만 변환.
        relicInfo.put("period", artifact.getEra());
        relicInfo.put("weight", artifact.getWeight());
        relicInfo.put("bondingArea", artifact.getBondingArea());
        relicInfo.put("treatmentPurpose", artifact.getTreatmentPurpose());
        return relicInfo;
    }
}
