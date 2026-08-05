package com.aivle.conservation_backend.xray_api.dto;

import java.util.List;

/**
 * layout.final.json의 실제 affine transform을 이용한 결함 대응 결과.
 */
public record XrayDefectMappingResponse(
        String jobId,
        String artifactId,
        String layoutType,
        Canvas canvas,
        List<SourceDefectMapping> mappings,

        // 동일 실제 결함으로 판단된 원본 관측들을 그룹화한 결과
        List<DefectGroup> defectGroups
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

    /**
     * 결합본의 동일 결함에 대응되는 원본 결함 관측 그룹.
     *
     * 예:
     * assembledRegionId = R-001
     * observations =
     *   sourceIndex 0 / R-002
     *   sourceIndex 1 / R-004
     */
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
}