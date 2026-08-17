package com.aivle.conservation_backend.vca.dto;

import java.time.Instant;
import java.util.List;
import java.util.Map;

public record ReportResponse(
        String assessmentRunId,
        String artifactId,
        String status,
        Instant generatedAt,
        Summary summary,
        List<Finding> findings,
        List<Recommendation> recommendations,
        List<Image> images,
        RagArtifacts ragArtifacts,
        PotteryInspection potteryInspection,
        PotteryInspectionStatus potteryInspectionStatus
) {

    public record Summary(
            String headline,
            String description,
            String overallCondition,
            String riskLevel
    ) {
    }

    public record Finding(
            String findingId,
            String category,
            String severity,
            String description,
            String conceptFamily,
            String descriptor,
            String imageId,
            List<Citation> citations,
            Bbox bbox,
            List<List<Point>> polygons
    ) {
    }

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

    // Vectorized mask outlines in original-image pixel space, one polygon
    // per disconnected mask fragment (real masks are often multi-component).
    // The mask is the standard segmentation signal; Bbox above is kept only
    // for auxiliary/legacy display.
    public record Point(
            Double x,
            Double y
    ) {
    }

    public record Recommendation(
            String recommendationId,
            String priority,
            String title,
            String description
    ) {
    }

    public record Image(
            String imageId,
            String fileName,
            String downloadUrl
    ) {
    }

    public record RagArtifacts(
            String schema,
            int queryCount,
            int retrievalResultCount,
            int evidenceRowCount,
            int visualConceptCardCount,
            List<RagQuery> queries,
            List<RagRetrievalResult> retrievalResults,
            List<RagEvidenceRow> evidenceRows,
            List<RagVisualConceptCard> visualConceptCards
    ) {
    }

    public record RagQuery(
            String lane,
            String promptText,
            String queryId
    ) {
    }

    public record RagRetrievalResult(
            String chunkId,
            String citationId,
            String lane,
            List<String> matchedTerms,
            Integer pageNumber,
            String promptText,
            String queryId,
            int rank,
            double score,
            String snippetText,
            String sourceCitation
    ) {
    }

    public record RagEvidenceRow(
            String evidenceState,
            String lane,
            List<String> matchedCitationIds,
            String promptText,
            String queryId,
            String ragParentCandidateId,
            String topCitationId,
            Double topRetrievalScore
    ) {
    }

    public record RagVisualConceptCard(
            String conceptCardId,
            String conceptFamily,
            List<String> contextTerms,
            List<String> descriptorTerms,
            List<String> materialTerms,
            String provenanceStrength,
            String ragParentCandidateId,
            String rawRetrievedSentence,
            double retrievalScore,
            List<String> sourceCitationIds
    ) {
    }

    public record PotteryInspection(
            String moduleVersion,
            String inspectionText,
            String summary,
            boolean humanReviewRecommended,
            Map<String, Object> detail
    ) {
    }

    public record PotteryInspectionStatus(
            boolean applicable,
            String status,
            boolean retryable,
            String failureMessage,
            Instant lastAttemptedAt
    ) {
    }
}
