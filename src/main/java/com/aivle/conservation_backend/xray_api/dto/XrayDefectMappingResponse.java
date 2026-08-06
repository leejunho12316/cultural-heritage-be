package com.aivle.conservation_backend.xray_api.dto;

import java.util.List;

/**
 * layout.final.json의 실제 affine transform과 final provenance를 이용한 결함 대응 결과.
 */
public record XrayDefectMappingResponse(
        String jobId,
        String artifactId,
        String layoutType,
        Canvas canvas,
        List<SourceDefectMapping> mappings,
        List<DefectGroup> defectGroups,
        List<AssembledDecision> assembledDecisions,
        List<SourceOnlyGroup> sourceOnlyGroups
) {
    public record Canvas(
            int width,
            int height
    ) {
    }

    public record SourceDefectMapping(
            String sourceRegionId,
            String sourceFileName,
            int originalSourceIndex,
            String status,
            List<Projection> projections
    ) {
    }

    public record Projection(
            int layoutFragmentIndex,
            int subfragmentIndex,
            List<Point> transformedPolygon,
            BoundingBox transformedBBox,
            String status,
            List<AssembledMatch> assembledMatches
    ) {
    }

    public record Point(
            double x,
            double y
    ) {
    }

    public record BoundingBox(
            double x1,
            double y1,
            double x2,
            double y2
    ) {
    }

    public record AssembledMatch(
            String regionId,
            String className,
            Double confidence,
            double intersectionArea,
            double iou,
            double sourceCoverage,
            double assembledCoverage
    ) {
    }

    /** 결합본의 동일 결함에 대응되는 원본 관측 그룹. */
    public record DefectGroup(
            String assembledRegionId,
            String className,
            Double confidence,
            List<SourceObservation> observations
    ) {
    }

    public record SourceObservation(
            String sourceRegionId,
            String sourceFileName,
            int originalSourceIndex,
            int layoutFragmentIndex,
            int subfragmentIndex,
            BoundingBox transformedBBox,
            double iou,
            double sourceCoverage,
            double assembledCoverage
    ) {
    }

    /**
     * 최종 결합본에서 탐지한 결함의 최종 대응 상태.
     * CONFIRMED: 원본 결함과 대응됨
     * ASSEMBLED_ONLY: 원본 탐지와 대응되지 않음
     * ASSEMBLY_ARTIFACT_SUSPECT: 원본 대응이 없고 seam/overlap 영향이 큼
     */
    public record AssembledDecision(
            String assembledRegionId,
            String status,
            int sourceObservationCount,
            double seamCoverage,
            double overlapCoverage
    ) {
    }

    /** 결합본 AI가 잡지 못한 원본 결함들을 최종 좌표에서 중복 통합한 그룹. */
    public record SourceOnlyGroup(
            String groupId,
            String status,
            BoundingBox unionBBox,
            List<SourceOnlyObservation> observations
    ) {
    }

    public record SourceOnlyObservation(
            String sourceRegionId,
            String sourceFileName,
            int originalSourceIndex,
            int layoutFragmentIndex,
            int subfragmentIndex,
            BoundingBox transformedBBox,
            double visibilityRatio,
            String visibilityStatus
    ) {
    }
}
