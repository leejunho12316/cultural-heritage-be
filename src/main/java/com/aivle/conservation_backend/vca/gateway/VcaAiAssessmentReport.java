package com.aivle.conservation_backend.vca.gateway;

import java.util.List;

public record VcaAiAssessmentReport(
        String runId,
        String assessmentId,
        String status,
        String summary,
        List<VcaAiAssessmentFinding> findings,
        RagArtifacts ragArtifacts
) {

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
}
