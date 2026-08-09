package com.aivle.conservation_backend.vca.gateway;

import java.util.List;

public record VcaAiAssessmentFinding(
        String category,
        String severity,
        String message,
        String imageId,
        String conceptFamily,
        String descriptor,
        List<Citation> citations,
        Bbox bbox,
        List<List<Point>> polygons
) {

    public record Citation(
            String citationId,
            String sourceCitation,
            Integer pageNumber
    ) {
    }

    public record Bbox(
            Double xMin,
            Double yMin,
            Double xMax,
            Double yMax
    ) {
    }

    public record Point(
            Double x,
            Double y
    ) {
    }
}
