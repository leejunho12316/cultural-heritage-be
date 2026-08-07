package com.aivle.conservation_backend.xray_api.service;

import com.aivle.conservation_backend.xray_api.dto.XrayDefectMappingRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayDefectMappingResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayDetectionResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayJobStatusResponse;
import org.springframework.stereotype.Service;
import tools.jackson.databind.ObjectMapper;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.awt.image.Raster;
import java.io.ByteArrayInputStream;
import java.io.IOException;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.LinkedHashMap;
import java.util.HashSet;
import java.util.Set;

@Service
public class XrayDefectMappingService {

    private static final double EPSILON = 1.0e-9;
    private static final double MATCH_IOU_THRESHOLD = 0.10;
    private static final double MATCH_COVERAGE_THRESHOLD = 0.10;
    private static final double ARTIFACT_MASK_COVERAGE_THRESHOLD = 0.15;
    private static final double SOURCE_ONLY_GROUP_IOU_THRESHOLD = 0.50;
    private static final double VISIBLE_THRESHOLD = 0.80;
    private static final double NOT_VISIBLE_THRESHOLD = 0.05;

    private final XrayStitchService xrayStitchService;
    private final ObjectMapper objectMapper;

    public XrayDefectMappingService(
            XrayStitchService xrayStitchService,
            ObjectMapper objectMapper
    ) {
        this.xrayStitchService = xrayStitchService;
        this.objectMapper = objectMapper;
    }

    public XrayDefectMappingResponse mapDefects(
            String jobId,
            XrayDefectMappingRequest request
    ) {
        validateRequest(request);

        // Provenance maps are created only after the remote Finalizer step.
        // Spring의 전체 업무 상태는 STITCHED를 유지하고, 실제 final S3 outputs
        // 존재 여부를 아래 service가 검증한다.
        XrayJobStatusResponse jobStatus = xrayStitchService.requireFinalizedJob(jobId);

        Map<String, Object> layout = readFinalLayout(jobId);
        validateFinalLayout(layout);

        XrayDefectMappingResponse.Canvas canvas = readCanvas(layout);
        validateAssembledImageSize(request.assembledDetection(), canvas);

        List<String> sourceFileNames =
                xrayStitchService.getOrderedXraySourceFileNames(jobId);
        Map<Integer, XrayDetectionResponse.DetectionSummary> sourceSummaries =
                indexSourceSummaries(request.fragmentDetection());

        List<LayoutFragment> layoutFragments = readLayoutFragments(layout);
        validateLayoutSourceIdentity(
                layoutFragments,
                sourceFileNames,
                sourceSummaries
        );

        List<XrayDetectionResponse.AnomalyRegion> assembledRegions =
                safeRegions(request.assembledDetection());

        List<XrayDefectMappingResponse.SourceDefectMapping> mappings =
                new ArrayList<>();

        for (XrayDetectionResponse.AnomalyRegion sourceRegion
                : safeRegions(request.fragmentDetection())) {
            mappings.add(mapSourceRegion(
                    sourceRegion,
                    sourceFileNames,
                    sourceSummaries,
                    layoutFragments,
                    assembledRegions
            ));
        }

        List<XrayDefectMappingResponse.DefectGroup> defectGroups =
                buildDefectGroups(mappings);

        ProvenanceMaps provenance = readProvenance(jobId, canvas);
        List<XrayDefectMappingResponse.AssembledDecision> assembledDecisions =
                buildAssembledDecisions(assembledRegions, defectGroups, provenance);
        List<XrayDefectMappingResponse.SourceOnlyGroup> sourceOnlyGroups =
                buildSourceOnlyGroups(mappings, provenance);

        return new XrayDefectMappingResponse(
                jobId,
                jobStatus.artifactId(),
                "FINAL",
                canvas,
                mappings,
                defectGroups,
                assembledDecisions,
                sourceOnlyGroups
        );
    }

    private XrayDefectMappingResponse.SourceDefectMapping mapSourceRegion(
            XrayDetectionResponse.AnomalyRegion sourceRegion,
            List<String> sourceFileNames,
            Map<Integer, XrayDetectionResponse.DetectionSummary> sourceSummaries,
            List<LayoutFragment> layoutFragments,
            List<XrayDetectionResponse.AnomalyRegion> assembledRegions
    ) {
        validateRegion(sourceRegion, "fragment");

        Integer sourceIndex = sourceRegion.sourceIndex();
        if (sourceIndex == null) {
            throw new IllegalArgumentException(
                    "Fragment detection region sourceIndex is required: "
                            + sourceRegion.regionId()
            );
        }
        if (sourceIndex < 0 || sourceIndex >= sourceFileNames.size()) {
            throw new IllegalArgumentException(
                    "Fragment detection sourceIndex is out of range: "
                            + sourceIndex
            );
        }

        String sourceFileName = sourceFileNames.get(sourceIndex);

        XrayDetectionResponse.DetectionSummary sourceSummary =
                sourceSummaries.get(sourceIndex);
        validateRegionInsideImage(sourceRegion, sourceSummary);

        Rect sourceDefect = Rect.from(sourceRegion.bbox());
        List<XrayDefectMappingResponse.Projection> projections =
                new ArrayList<>();

        for (LayoutFragment layoutFragment : layoutFragments) {
            if (layoutFragment.originalSourceIndex() != sourceIndex) {
                continue;
            }

            Rect intersection = sourceDefect.intersection(layoutFragment.crop());
            if (intersection == null || intersection.area() <= EPSILON) {
                continue;
            }

            List<XrayDefectMappingResponse.Point> transformedPolygon =
                    transformRectangle(intersection, layoutFragment);
            XrayDefectMappingResponse.BoundingBox transformedBBox =
                    boundingBox(transformedPolygon);

            List<XrayDefectMappingResponse.AssembledMatch> matches =
                    findAssembledMatches(transformedPolygon, assembledRegions);

            String projectionStatus = switch (matches.size()) {
                case 0 -> "UNMATCHED";
                case 1 -> "MATCHED";
                default -> "AMBIGUOUS";
            };

            projections.add(new XrayDefectMappingResponse.Projection(
                    layoutFragment.index(),
                    layoutFragment.subfragmentIndex(),
                    transformedPolygon,
                    transformedBBox,
                    projectionStatus,
                    matches
            ));
        }

        String sourceStatus = summarizeSourceStatus(projections);

        return new XrayDefectMappingResponse.SourceDefectMapping(
                sourceRegion.regionId(),
                sourceFileName,
                sourceIndex,
                sourceStatus,
                projections
        );
    }


    private List<XrayDefectMappingResponse.DefectGroup> buildDefectGroups(
            List<XrayDefectMappingResponse.SourceDefectMapping> mappings
    ) {
        Map<String, DefectGroupAccumulator> groups = new LinkedHashMap<>();

        for (XrayDefectMappingResponse.SourceDefectMapping mapping : mappings) {
            if (mapping.projections() == null) {
                continue;
            }
            for (XrayDefectMappingResponse.Projection projection
                    : mapping.projections()) {
                // 둘 이상의 결합본 영역과 겹친 AMBIGUOUS projection은
                // 어느 결함을 확인해 준 것인지 확정할 수 없으므로 CONFIRMED 근거에서 제외한다.
                if (!"MATCHED".equals(projection.status())
                        || projection.assembledMatches() == null) {
                    continue;
                }

                for (XrayDefectMappingResponse.AssembledMatch match
                        : projection.assembledMatches()) {

                    DefectGroupAccumulator group =
                            groups.computeIfAbsent(
                                    match.regionId(),
                                    ignored -> new DefectGroupAccumulator(
                                            match.regionId(),
                                            match.className(),
                                            match.confidence(),
                                            new ArrayList<>()
                                    )
                            );

                    group.observations().add(
                            new XrayDefectMappingResponse.SourceObservation(
                                    mapping.sourceRegionId(),
                                    mapping.sourceFileName(),
                                    mapping.originalSourceIndex(),
                                    projection.layoutFragmentIndex(),
                                    projection.subfragmentIndex(),
                                    projection.transformedBBox(),
                                    match.iou(),
                                    match.sourceCoverage(),
                                    match.assembledCoverage()
                            )
                    );
                }
            }
        }

        List<XrayDefectMappingResponse.DefectGroup> result =
                new ArrayList<>();

        for (DefectGroupAccumulator group : groups.values()) {
            result.add(
                    new XrayDefectMappingResponse.DefectGroup(
                            group.assembledRegionId(),
                            group.className(),
                            group.confidence(),
                            List.copyOf(group.observations())
                    )
            );
        }

        return result;
    }

    private List<XrayDefectMappingResponse.AssembledDecision> buildAssembledDecisions(
            List<XrayDetectionResponse.AnomalyRegion> assembledRegions,
            List<XrayDefectMappingResponse.DefectGroup> defectGroups,
            ProvenanceMaps provenance
    ) {
        Map<String, Integer> observationCounts = new HashMap<>();
        for (XrayDefectMappingResponse.DefectGroup group : defectGroups) {
            observationCounts.put(
                    group.assembledRegionId(),
                    group.observations() != null ? group.observations().size() : 0
            );
        }

        List<XrayDefectMappingResponse.AssembledDecision> result = new ArrayList<>();
        for (XrayDetectionResponse.AnomalyRegion region : assembledRegions) {
            validateRegion(region, "assembled");
            Rect bbox = Rect.from(region.bbox());
            int sourceObservationCount = observationCounts.getOrDefault(region.regionId(), 0);
            double seamCoverage = binaryCoverage(bbox, provenance.seamZone());
            double overlapCoverage = binaryCoverage(bbox, provenance.overlapMask());

            String status;
            if (sourceObservationCount > 0) {
                status = "CONFIRMED";
            } else if (seamCoverage >= ARTIFACT_MASK_COVERAGE_THRESHOLD
                    || overlapCoverage >= ARTIFACT_MASK_COVERAGE_THRESHOLD) {
                status = "ASSEMBLY_ARTIFACT_SUSPECT";
            } else {
                status = "ASSEMBLED_ONLY";
            }

            result.add(new XrayDefectMappingResponse.AssembledDecision(
                    region.regionId(),
                    status,
                    sourceObservationCount,
                    round6(seamCoverage),
                    round6(overlapCoverage)
            ));
        }
        return result;
    }

    private List<XrayDefectMappingResponse.SourceOnlyGroup> buildSourceOnlyGroups(
            List<XrayDefectMappingResponse.SourceDefectMapping> mappings,
            ProvenanceMaps provenance
    ) {
        List<SourceOnlyCandidate> candidates = new ArrayList<>();

        for (XrayDefectMappingResponse.SourceDefectMapping mapping : mappings) {
            if (mapping.projections() == null) {
                continue;
            }
            for (XrayDefectMappingResponse.Projection projection : mapping.projections()) {
                if (projection.assembledMatches() != null
                        && !projection.assembledMatches().isEmpty()) {
                    continue;
                }

                double visibilityRatio = sourceVisibility(
                        projection.transformedPolygon(),
                        mapping.originalSourceIndex(),
                        provenance.sourceOwner()
                );
                String visibilityStatus = visibilityStatus(visibilityRatio);

                XrayDefectMappingResponse.SourceOnlyObservation observation =
                        new XrayDefectMappingResponse.SourceOnlyObservation(
                                mapping.sourceRegionId(),
                                mapping.sourceFileName(),
                                mapping.originalSourceIndex(),
                                projection.layoutFragmentIndex(),
                                projection.subfragmentIndex(),
                                projection.transformedBBox(),
                                round6(visibilityRatio),
                                visibilityStatus
                        );
                candidates.add(new SourceOnlyCandidate(observation));
            }
        }

        if (candidates.isEmpty()) {
            return List.of();
        }

        int[] parent = new int[candidates.size()];
        for (int i = 0; i < parent.length; i++) {
            parent[i] = i;
        }

        for (int i = 0; i < candidates.size(); i++) {
            for (int j = i + 1; j < candidates.size(); j++) {
                var first = candidates.get(i).observation();
                var second = candidates.get(j).observation();
                boolean sameSourceRegion = first.originalSourceIndex() == second.originalSourceIndex()
                        && first.sourceRegionId().equals(second.sourceRegionId());
                boolean crossSourceOverlap = first.originalSourceIndex() != second.originalSourceIndex()
                        && bboxIou(first.transformedBBox(), second.transformedBBox())
                        >= SOURCE_ONLY_GROUP_IOU_THRESHOLD;

                if (sameSourceRegion || crossSourceOverlap) {
                    union(parent, i, j);
                }
            }
        }

        Map<Integer, List<XrayDefectMappingResponse.SourceOnlyObservation>> grouped =
                new LinkedHashMap<>();
        for (int i = 0; i < candidates.size(); i++) {
            int root = find(parent, i);
            grouped.computeIfAbsent(root, ignored -> new ArrayList<>())
                    .add(candidates.get(i).observation());
        }

        List<XrayDefectMappingResponse.SourceOnlyGroup> result = new ArrayList<>();
        int groupIndex = 1;
        for (List<XrayDefectMappingResponse.SourceOnlyObservation> observations
                : grouped.values()) {
            double maxVisibility = observations.stream()
                    .mapToDouble(XrayDefectMappingResponse.SourceOnlyObservation::visibilityRatio)
                    .max()
                    .orElse(0.0);

            String status = maxVisibility >= VISIBLE_THRESHOLD
                    ? "SOURCE_ONLY_VISIBLE"
                    : maxVisibility <= NOT_VISIBLE_THRESHOLD
                    ? "SOURCE_ONLY_NOT_VISIBLE"
                    : "SOURCE_ONLY_PARTIALLY_VISIBLE";

            result.add(new XrayDefectMappingResponse.SourceOnlyGroup(
                    "S-" + String.format("%03d", groupIndex++),
                    status,
                    unionBBox(observations),
                    List.copyOf(observations)
            ));
        }
        return result;
    }

    private ProvenanceMaps readProvenance(
            String jobId,
            XrayDefectMappingResponse.Canvas canvas
    ) {
        PixelMap sourceOwner = readPixelMap(
                xrayStitchService.getOutputBytes(jobId, "source-owner"),
                canvas,
                "source owner"
        );
        PixelMap seamZone = readPixelMap(
                xrayStitchService.getOutputBytes(jobId, "seam-zone"),
                canvas,
                "seam zone"
        );
        PixelMap overlapMask = readPixelMap(
                xrayStitchService.getOutputBytes(jobId, "overlap-mask"),
                canvas,
                "overlap mask"
        );
        return new ProvenanceMaps(sourceOwner, seamZone, overlapMask);
    }

    private PixelMap readPixelMap(
            byte[] bytes,
            XrayDefectMappingResponse.Canvas canvas,
            String label
    ) {
        try {
            BufferedImage image = ImageIO.read(new ByteArrayInputStream(bytes));
            if (image == null) {
                throw new IllegalStateException("Failed to decode final " + label + " from S3.");
            }
            if (image.getWidth() != canvas.width() || image.getHeight() != canvas.height()) {
                throw new IllegalStateException(
                        "Final " + label + " size mismatch. expected="
                                + canvas.width() + "x" + canvas.height()
                                + ", actual=" + image.getWidth() + "x" + image.getHeight()
                );
            }
            return new PixelMap(image.getRaster(), image.getWidth(), image.getHeight());
        } catch (IOException e) {
            throw new IllegalStateException("Failed to read final " + label + " from S3.", e);
        }
    }

    private double binaryCoverage(Rect bbox, PixelMap map) {
        int x0 = Math.max(0, (int) Math.floor(bbox.x1()));
        int y0 = Math.max(0, (int) Math.floor(bbox.y1()));
        int x1 = Math.min(map.width(), (int) Math.ceil(bbox.x2()));
        int y1 = Math.min(map.height(), (int) Math.ceil(bbox.y2()));
        if (x0 >= x1 || y0 >= y1) {
            return 0.0;
        }

        long positive = 0;
        for (int y = y0; y < y1; y++) {
            for (int x = x0; x < x1; x++) {
                if (map.sample(x, y) > 0) {
                    positive++;
                }
            }
        }
        return Math.min(1.0, positive / Math.max(bbox.area(), 1.0));
    }

    private double sourceVisibility(
            List<XrayDefectMappingResponse.Point> polygon,
            int originalSourceIndex,
            PixelMap sourceOwner
    ) {
        double expectedArea = polygonArea(polygon);
        if (expectedArea <= EPSILON) {
            return 0.0;
        }

        XrayDefectMappingResponse.BoundingBox bbox = boundingBox(polygon);
        int x0 = Math.max(0, (int) Math.floor(bbox.x1()));
        int y0 = Math.max(0, (int) Math.floor(bbox.y1()));
        int x1 = Math.min(sourceOwner.width(), (int) Math.ceil(bbox.x2()));
        int y1 = Math.min(sourceOwner.height(), (int) Math.ceil(bbox.y2()));

        long visible = 0;
        int ownerValue = originalSourceIndex + 1;
        for (int y = y0; y < y1; y++) {
            for (int x = x0; x < x1; x++) {
                if (pointInsidePolygon(x + 0.5, y + 0.5, polygon)
                        && sourceOwner.sample(x, y) == ownerValue) {
                    visible++;
                }
            }
        }
        return Math.min(1.0, visible / expectedArea);
    }

    private boolean pointInsidePolygon(
            double x,
            double y,
            List<XrayDefectMappingResponse.Point> polygon
    ) {
        boolean inside = false;
        for (int i = 0, j = polygon.size() - 1; i < polygon.size(); j = i++) {
            var pi = polygon.get(i);
            var pj = polygon.get(j);
            boolean intersects = ((pi.y() > y) != (pj.y() > y))
                    && (x < (pj.x() - pi.x()) * (y - pi.y())
                    / ((pj.y() - pi.y()) + EPSILON) + pi.x());
            if (intersects) {
                inside = !inside;
            }
        }
        return inside;
    }

    private String visibilityStatus(double ratio) {
        if (ratio >= VISIBLE_THRESHOLD) {
            return "VISIBLE";
        }
        if (ratio <= NOT_VISIBLE_THRESHOLD) {
            return "NOT_VISIBLE";
        }
        return "PARTIALLY_VISIBLE";
    }

    private double bboxIou(
            XrayDefectMappingResponse.BoundingBox first,
            XrayDefectMappingResponse.BoundingBox second
    ) {
        double x1 = Math.max(first.x1(), second.x1());
        double y1 = Math.max(first.y1(), second.y1());
        double x2 = Math.min(first.x2(), second.x2());
        double y2 = Math.min(first.y2(), second.y2());
        if (x2 <= x1 || y2 <= y1) {
            return 0.0;
        }
        double intersection = (x2 - x1) * (y2 - y1);
        double firstArea = (first.x2() - first.x1()) * (first.y2() - first.y1());
        double secondArea = (second.x2() - second.x1()) * (second.y2() - second.y1());
        return intersection / Math.max(firstArea + secondArea - intersection, EPSILON);
    }

    private XrayDefectMappingResponse.BoundingBox unionBBox(
            List<XrayDefectMappingResponse.SourceOnlyObservation> observations
    ) {
        double x1 = Double.POSITIVE_INFINITY;
        double y1 = Double.POSITIVE_INFINITY;
        double x2 = Double.NEGATIVE_INFINITY;
        double y2 = Double.NEGATIVE_INFINITY;
        for (var observation : observations) {
            var bbox = observation.transformedBBox();
            x1 = Math.min(x1, bbox.x1());
            y1 = Math.min(y1, bbox.y1());
            x2 = Math.max(x2, bbox.x2());
            y2 = Math.max(y2, bbox.y2());
        }
        return new XrayDefectMappingResponse.BoundingBox(x1, y1, x2, y2);
    }

    private int find(int[] parent, int value) {
        if (parent[value] != value) {
            parent[value] = find(parent, parent[value]);
        }
        return parent[value];
    }

    private void union(int[] parent, int first, int second) {
        int firstRoot = find(parent, first);
        int secondRoot = find(parent, second);
        if (firstRoot != secondRoot) {
            parent[secondRoot] = firstRoot;
        }
    }

    private List<XrayDefectMappingResponse.AssembledMatch> findAssembledMatches(
            List<XrayDefectMappingResponse.Point> sourcePolygon,
            List<XrayDetectionResponse.AnomalyRegion> assembledRegions
    ) {
        double sourceArea = polygonArea(sourcePolygon);
        List<XrayDefectMappingResponse.AssembledMatch> result = new ArrayList<>();

        for (XrayDetectionResponse.AnomalyRegion assembledRegion : assembledRegions) {
            validateRegion(assembledRegion, "assembled");
            Rect assembledRect = Rect.from(assembledRegion.bbox());

            List<XrayDefectMappingResponse.Point> clipped =
                    clipPolygonToRect(sourcePolygon, assembledRect);
            double intersectionArea = polygonArea(clipped);

            if (intersectionArea <= EPSILON) {
                continue;
            }

            double assembledArea = assembledRect.area();
            double unionArea = sourceArea + assembledArea - intersectionArea;

            double iou = unionArea > EPSILON
                    ? intersectionArea / unionArea
                    : 0.0;
            double sourceCoverage = sourceArea > EPSILON
                    ? intersectionArea / sourceArea
                    : 0.0;
            double assembledCoverage = assembledArea > EPSILON
                    ? intersectionArea / assembledArea
                    : 0.0;

            // Ignore trivial geometric overlaps that are not meaningful defect matches.
            if (iou < MATCH_IOU_THRESHOLD
                    || sourceCoverage < MATCH_COVERAGE_THRESHOLD
                    || assembledCoverage < MATCH_COVERAGE_THRESHOLD) {
                continue;
            }

            result.add(new XrayDefectMappingResponse.AssembledMatch(
                    assembledRegion.regionId(),
                    assembledRegion.className(),
                    assembledRegion.confidence(),
                    round6(intersectionArea),
                    round6(iou),
                    round6(sourceCoverage),
                    round6(assembledCoverage)
            ));
        }

        result.sort(
                Comparator.comparingDouble(
                        XrayDefectMappingResponse.AssembledMatch::iou
                ).reversed()
        );
        return result;
    }

    private String summarizeSourceStatus(
            List<XrayDefectMappingResponse.Projection> projections
    ) {
        if (projections.isEmpty()) {
            return "UNMAPPED_TO_LAYOUT";
        }

        boolean matched = false;
        for (XrayDefectMappingResponse.Projection projection : projections) {
            if ("AMBIGUOUS".equals(projection.status())) {
                return "AMBIGUOUS";
            }
            if ("MATCHED".equals(projection.status())) {
                matched = true;
            }
        }
        return matched ? "MATCHED" : "UNMATCHED";
    }

    private List<XrayDefectMappingResponse.Point> transformRectangle(
            Rect sourceRect,
            LayoutFragment fragment
    ) {
        List<XrayDefectMappingResponse.Point> sourcePoints = List.of(
                new XrayDefectMappingResponse.Point(sourceRect.x1(), sourceRect.y1()),
                new XrayDefectMappingResponse.Point(sourceRect.x2(), sourceRect.y1()),
                new XrayDefectMappingResponse.Point(sourceRect.x2(), sourceRect.y2()),
                new XrayDefectMappingResponse.Point(sourceRect.x1(), sourceRect.y2())
        );

        List<XrayDefectMappingResponse.Point> transformed = new ArrayList<>();
        for (XrayDefectMappingResponse.Point sourcePoint : sourcePoints) {
            double localX = sourcePoint.x() - fragment.crop().x1();
            double localY = sourcePoint.y() - fragment.crop().y1();

            double[][] matrix = fragment.affine();
            double x = matrix[0][0] * localX
                    + matrix[0][1] * localY
                    + matrix[0][2];
            double y = matrix[1][0] * localX
                    + matrix[1][1] * localY
                    + matrix[1][2];

            transformed.add(new XrayDefectMappingResponse.Point(
                    round6(x),
                    round6(y)
            ));
        }
        return transformed;
    }

    private XrayDefectMappingResponse.BoundingBox boundingBox(
            List<XrayDefectMappingResponse.Point> points
    ) {
        double minX = Double.POSITIVE_INFINITY;
        double minY = Double.POSITIVE_INFINITY;
        double maxX = Double.NEGATIVE_INFINITY;
        double maxY = Double.NEGATIVE_INFINITY;

        for (XrayDefectMappingResponse.Point point : points) {
            minX = Math.min(minX, point.x());
            minY = Math.min(minY, point.y());
            maxX = Math.max(maxX, point.x());
            maxY = Math.max(maxY, point.y());
        }

        return new XrayDefectMappingResponse.BoundingBox(
                round6(minX),
                round6(minY),
                round6(maxX),
                round6(maxY)
        );
    }

    private List<XrayDefectMappingResponse.Point> clipPolygonToRect(
            List<XrayDefectMappingResponse.Point> polygon,
            Rect rect
    ) {
        List<XrayDefectMappingResponse.Point> result = new ArrayList<>(polygon);
        result = clip(result, Boundary.LEFT, rect.x1());
        result = clip(result, Boundary.RIGHT, rect.x2());
        result = clip(result, Boundary.TOP, rect.y1());
        result = clip(result, Boundary.BOTTOM, rect.y2());
        return result;
    }

    private List<XrayDefectMappingResponse.Point> clip(
            List<XrayDefectMappingResponse.Point> input,
            Boundary boundary,
            double value
    ) {
        if (input.isEmpty()) {
            return input;
        }

        List<XrayDefectMappingResponse.Point> output = new ArrayList<>();
        XrayDefectMappingResponse.Point previous = input.get(input.size() - 1);
        boolean previousInside = isInside(previous, boundary, value);

        for (XrayDefectMappingResponse.Point current : input) {
            boolean currentInside = isInside(current, boundary, value);

            if (currentInside) {
                if (!previousInside) {
                    output.add(intersection(previous, current, boundary, value));
                }
                output.add(current);
            } else if (previousInside) {
                output.add(intersection(previous, current, boundary, value));
            }

            previous = current;
            previousInside = currentInside;
        }
        return output;
    }

    private boolean isInside(
            XrayDefectMappingResponse.Point point,
            Boundary boundary,
            double value
    ) {
        return switch (boundary) {
            case LEFT -> point.x() >= value - EPSILON;
            case RIGHT -> point.x() <= value + EPSILON;
            case TOP -> point.y() >= value - EPSILON;
            case BOTTOM -> point.y() <= value + EPSILON;
        };
    }

    private XrayDefectMappingResponse.Point intersection(
            XrayDefectMappingResponse.Point start,
            XrayDefectMappingResponse.Point end,
            Boundary boundary,
            double value
    ) {
        double dx = end.x() - start.x();
        double dy = end.y() - start.y();

        if (boundary == Boundary.LEFT || boundary == Boundary.RIGHT) {
            if (Math.abs(dx) <= EPSILON) {
                return new XrayDefectMappingResponse.Point(value, start.y());
            }
            double t = (value - start.x()) / dx;
            return new XrayDefectMappingResponse.Point(
                    value,
                    start.y() + t * dy
            );
        }

        if (Math.abs(dy) <= EPSILON) {
            return new XrayDefectMappingResponse.Point(start.x(), value);
        }
        double t = (value - start.y()) / dy;
        return new XrayDefectMappingResponse.Point(
                start.x() + t * dx,
                value
        );
    }

    private double polygonArea(List<XrayDefectMappingResponse.Point> polygon) {
        if (polygon.size() < 3) {
            return 0.0;
        }

        double sum = 0.0;
        for (int i = 0; i < polygon.size(); i++) {
            XrayDefectMappingResponse.Point a = polygon.get(i);
            XrayDefectMappingResponse.Point b = polygon.get((i + 1) % polygon.size());
            sum += a.x() * b.y() - b.x() * a.y();
        }
        return Math.abs(sum) * 0.5;
    }

    private void validateRequest(XrayDefectMappingRequest request) {
        if (request == null
                || request.fragmentDetection() == null
                || request.assembledDetection() == null) {
            throw new IllegalArgumentException(
                    "Both fragmentDetection and assembledDetection are required."
            );
        }
        if (!request.fragmentDetection().success()) {
            throw new IllegalArgumentException(
                    "fragmentDetection was not successful."
            );
        }
        if (!request.assembledDetection().success()) {
            throw new IllegalArgumentException(
                    "assembledDetection was not successful."
            );
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> readFinalLayout(String jobId) {
        try {
            return objectMapper.readValue(
                    xrayStitchService.getFinalLayout(jobId),
                    Map.class
            );
        } catch (Exception e) {
            throw new IllegalStateException(
                    "Failed to parse layout.final.json for job: " + jobId,
                    e
            );
        }
    }

    private void validateFinalLayout(Map<String, Object> layout) {
        Object stage = layout.get("layoutStage");
        if (!(stage instanceof String text) || !"FINAL".equalsIgnoreCase(text)) {
            throw new IllegalStateException(
                    "Defect mapping requires layout.final.json."
            );
        }
    }

    @SuppressWarnings("unchecked")
    private XrayDefectMappingResponse.Canvas readCanvas(Map<String, Object> layout) {
        Object canvasValue = layout.get("canvas");
        if (!(canvasValue instanceof Map<?, ?> rawCanvas)) {
            throw new IllegalStateException("Final layout does not contain canvas.");
        }

        Map<String, Object> canvas = (Map<String, Object>) rawCanvas;
        int width = requirePositiveInt(canvas.get("width"), "canvas.width");
        int height = requirePositiveInt(canvas.get("height"), "canvas.height");

        return new XrayDefectMappingResponse.Canvas(width, height);
    }

    private void validateAssembledImageSize(
            XrayDetectionResponse assembledDetection,
            XrayDefectMappingResponse.Canvas canvas
    ) {
        XrayDetectionResponse.DetectionSummary summary = assembledDetection.summary();
        if (summary == null
                || summary.imageWidth() == null
                || summary.imageHeight() == null) {
            throw new IllegalArgumentException(
                    "assembledDetection summary with image size is required."
            );
        }

        if (summary.imageWidth() != canvas.width()
                || summary.imageHeight() != canvas.height()) {
            throw new IllegalArgumentException(
                    "Assembled detection image size does not match final layout canvas. "
                            + "detection=" + summary.imageWidth() + "x" + summary.imageHeight()
                            + ", canvas=" + canvas.width() + "x" + canvas.height()
            );
        }
    }

    private Map<Integer, XrayDetectionResponse.DetectionSummary> indexSourceSummaries(
            XrayDetectionResponse fragmentDetection
    ) {
        if (fragmentDetection.summaries() == null) {
            throw new IllegalArgumentException(
                    "fragmentDetection summaries are required."
            );
        }

        Map<Integer, XrayDetectionResponse.DetectionSummary> result = new HashMap<>();
        for (XrayDetectionResponse.DetectionSummary summary
                : fragmentDetection.summaries()) {
            if (summary == null) {
                continue;
            }

            Integer sourceIndex = summary.sourceIndex();
            if (sourceIndex == null) {
                throw new IllegalArgumentException(
                        "Fragment detection summary sourceIndex is required."
                );
            }

            if (summary.error() != null && !summary.error().isBlank()) {
                throw new IllegalArgumentException(
                        "Fragment detection failed for sourceIndex="
                                + sourceIndex + ": " + summary.error()
                );
            }

            if (summary.imageWidth() == null || summary.imageHeight() == null) {
                throw new IllegalArgumentException(
                        "Fragment detection image size is missing for sourceIndex="
                                + sourceIndex
                );
            }

            XrayDetectionResponse.DetectionSummary previous =
                    result.put(sourceIndex, summary);
            if (previous != null) {
                throw new IllegalArgumentException(
                        "Duplicate fragment detection summary sourceIndex: "
                                + sourceIndex
                );
            }
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private List<LayoutFragment> readLayoutFragments(Map<String, Object> layout) {
        Object fragmentsValue = layout.get("fragments");
        if (!(fragmentsValue instanceof List<?> rawFragments)) {
            throw new IllegalStateException(
                    "Final layout does not contain a valid fragments array."
            );
        }

        List<LayoutFragment> result = new ArrayList<>();
        for (Object value : rawFragments) {
            if (!(value instanceof Map<?, ?> rawFragment)) {
                throw new IllegalStateException("Invalid final layout fragment.");
            }
            Map<String, Object> fragment = (Map<String, Object>) rawFragment;

            int index = requireInt(fragment.get("index"), "fragments.index");
            int sourceIndex = requireInt(
                    fragment.get("originalSourceIndex"),
                    "fragments.originalSourceIndex"
            );
            int subfragmentIndex = requireInt(
                    fragment.get("subfragmentIndex"),
                    "fragments.subfragmentIndex"
            );
            String sourceName = requireString(
                    fragment.get("originalSourceName"),
                    "fragments.originalSourceName"
            );
            Rect crop = readCrop(fragment.get("originalCropBBoxXYWH"));
            double[][] affine = readAffine(fragment.get("affineMatrix"));

            result.add(new LayoutFragment(
                    index,
                    sourceIndex,
                    sourceName,
                    subfragmentIndex,
                    crop,
                    affine
            ));
        }
        return result;
    }

    private void validateLayoutSourceIdentity(
            List<LayoutFragment> fragments,
            List<String> sourceFileNames,
            Map<Integer, XrayDetectionResponse.DetectionSummary> sourceSummaries
    ) {
        for (LayoutFragment fragment : fragments) {
            int sourceIndex = fragment.originalSourceIndex();
            if (sourceIndex < 0 || sourceIndex >= sourceFileNames.size()) {
                throw new IllegalStateException(
                        "layout originalSourceIndex is out of range: " + sourceIndex
                );
            }

            String actualFileName = sourceFileNames.get(sourceIndex);
            if (!actualFileName.equals(fragment.originalSourceName())) {
                throw new IllegalStateException(
                        "layout source identity mismatch: index=" + sourceIndex
                                + ", layout=" + fragment.originalSourceName()
                                + ", actual=" + actualFileName
                );
            }

            XrayDetectionResponse.DetectionSummary summary =
                    sourceSummaries.get(sourceIndex);
            if (summary == null) {
                throw new IllegalArgumentException(
                        "Fragment detection summary is missing for sourceIndex="
                                + sourceIndex
                );
            }

            Rect crop = fragment.crop();
            if (crop.x1() < -EPSILON
                    || crop.y1() < -EPSILON
                    || crop.x2() > summary.imageWidth() + EPSILON
                    || crop.y2() > summary.imageHeight() + EPSILON) {
                throw new IllegalStateException(
                        "layout crop is outside source image: " + actualFileName
                );
            }
        }
    }

    private void validateRegion(
            XrayDetectionResponse.AnomalyRegion region,
            String description
    ) {
        if (region == null
                || region.regionId() == null
                || region.regionId().isBlank()
                || region.fileName() == null
                || region.fileName().isBlank()
                || region.bbox() == null) {
            throw new IllegalArgumentException(
                    "Invalid " + description + " detection region."
            );
        }
        Rect.from(region.bbox());
    }

    private void validateRegionInsideImage(
            XrayDetectionResponse.AnomalyRegion region,
            XrayDetectionResponse.DetectionSummary summary
    ) {
        if (summary == null) {
            throw new IllegalArgumentException(
                    "Fragment detection summary is missing for sourceIndex="
                            + region.sourceIndex()
            );
        }

        Rect rect = Rect.from(region.bbox());
        if (rect.x1() < -1.0
                || rect.y1() < -1.0
                || rect.x2() > summary.imageWidth() + 1.0
                || rect.y2() > summary.imageHeight() + 1.0) {
            throw new IllegalArgumentException(
                    "Fragment defect bbox is outside source image: " + region.regionId()
            );
        }
    }

    private List<XrayDetectionResponse.AnomalyRegion> safeRegions(
            XrayDetectionResponse detection
    ) {
        return detection.regions() == null ? List.of() : detection.regions();
    }

    private Rect readCrop(Object value) {
        if (!(value instanceof List<?> list) || list.size() != 4) {
            throw new IllegalStateException(
                    "Invalid originalCropBBoxXYWH in final layout."
            );
        }

        double x = requireFiniteDouble(list.get(0), "crop.x");
        double y = requireFiniteDouble(list.get(1), "crop.y");
        double width = requireFiniteDouble(list.get(2), "crop.width");
        double height = requireFiniteDouble(list.get(3), "crop.height");

        if (width <= 0.0 || height <= 0.0) {
            throw new IllegalStateException("Invalid final layout crop size.");
        }
        return new Rect(x, y, x + width, y + height);
    }

    private double[][] readAffine(Object value) {
        if (!(value instanceof List<?> rows) || rows.size() != 3) {
            throw new IllegalStateException("Invalid affineMatrix in final layout.");
        }

        double[][] matrix = new double[3][3];
        for (int row = 0; row < 3; row++) {
            Object rowValue = rows.get(row);
            if (!(rowValue instanceof List<?> columns) || columns.size() != 3) {
                throw new IllegalStateException("Invalid affineMatrix in final layout.");
            }
            for (int column = 0; column < 3; column++) {
                matrix[row][column] = requireFiniteDouble(
                        columns.get(column),
                        "affineMatrix[" + row + "][" + column + "]"
                );
            }
        }

        if (Math.abs(matrix[2][0]) > EPSILON
                || Math.abs(matrix[2][1]) > EPSILON
                || Math.abs(matrix[2][2] - 1.0) > EPSILON) {
            throw new IllegalStateException(
                    "Final layout affineMatrix is not a 2D affine transform."
            );
        }

        double col0Length = Math.hypot(matrix[0][0], matrix[1][0]);
        double col1Length = Math.hypot(matrix[0][1], matrix[1][1]);
        double dot = matrix[0][0] * matrix[0][1]
                + matrix[1][0] * matrix[1][1];

        if (Math.abs(col0Length - 1.0) > 1.0e-6
                || Math.abs(col1Length - 1.0) > 1.0e-6
                || Math.abs(dot) > 1.0e-6) {
            throw new IllegalStateException(
                    "Final layout transform must contain rotation/translation only."
            );
        }

        return matrix;
    }

    private int requirePositiveInt(Object value, String name) {
        int result = requireInt(value, name);
        if (result <= 0) {
            throw new IllegalStateException(name + " must be positive.");
        }
        return result;
    }

    private int requireInt(Object value, String name) {
        if (!(value instanceof Number number)) {
            throw new IllegalStateException(name + " is missing or invalid.");
        }
        return number.intValue();
    }

    private String requireString(Object value, String name) {
        if (!(value instanceof String text) || text.isBlank()) {
            throw new IllegalStateException(name + " is missing or invalid.");
        }
        return text;
    }

    private double requireFiniteDouble(Object value, String name) {
        if (!(value instanceof Number number)) {
            throw new IllegalStateException(name + " is missing or invalid.");
        }
        double result = number.doubleValue();
        if (!Double.isFinite(result)) {
            throw new IllegalStateException(name + " must be finite.");
        }
        return result;
    }

    private double round6(double value) {
        return Math.round(value * 1_000_000.0) / 1_000_000.0;
    }


    private record ProvenanceMaps(
            PixelMap sourceOwner,
            PixelMap seamZone,
            PixelMap overlapMask
    ) {
    }

    private record PixelMap(
            Raster raster,
            int width,
            int height
    ) {
        int sample(int x, int y) {
            return raster.getSample(x, y, 0);
        }
    }

    private record SourceOnlyCandidate(
            XrayDefectMappingResponse.SourceOnlyObservation observation
    ) {
    }

    private record DefectGroupAccumulator(
            String assembledRegionId,
            String className,
            Double confidence,
            List<XrayDefectMappingResponse.SourceObservation> observations
    ) {
    }

    private enum Boundary {
        LEFT,
        RIGHT,
        TOP,
        BOTTOM
    }

    private record LayoutFragment(
            int index,
            int originalSourceIndex,
            String originalSourceName,
            int subfragmentIndex,
            Rect crop,
            double[][] affine
    ) {
    }

    private record Rect(
            double x1,
            double y1,
            double x2,
            double y2
    ) {
        private Rect {
            if (!Double.isFinite(x1)
                    || !Double.isFinite(y1)
                    || !Double.isFinite(x2)
                    || !Double.isFinite(y2)
                    || x2 <= x1
                    || y2 <= y1) {
                throw new IllegalArgumentException("Invalid bounding box.");
            }
        }

        static Rect from(XrayDetectionResponse.BoundingBox bbox) {
            if (bbox == null
                    || bbox.x1() == null
                    || bbox.y1() == null
                    || bbox.x2() == null
                    || bbox.y2() == null) {
                throw new IllegalArgumentException("Bounding box is required.");
            }
            return new Rect(bbox.x1(), bbox.y1(), bbox.x2(), bbox.y2());
        }

        double area() {
            return (x2 - x1) * (y2 - y1);
        }

        Rect intersection(Rect other) {
            double left = Math.max(x1, other.x1);
            double top = Math.max(y1, other.y1);
            double right = Math.min(x2, other.x2);
            double bottom = Math.min(y2, other.y2);

            if (right <= left || bottom <= top) {
                return null;
            }
            return new Rect(left, top, right, bottom);
        }
    }
}
