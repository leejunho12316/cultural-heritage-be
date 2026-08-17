# RAG Findings

Review date: 2026-08-03
Review scope: high-precision read-only review of the current `vca_v2` workspace.
Review status: advisory. No fixes are approved by this file.

## Verification Snapshot

Current workspace checks after the documentation pass:

- `uv run python -m pytest modules -q` -> `452 passed`
- `uv run ruff check modules/mask_refining modules/visual_cue_generation modules/rough_masking modules/rag modules/shared` -> pass
- `uv run basedpyright modules/mask_refining modules/visual_cue_generation modules/rough_masking modules/rag modules/shared` -> `0 errors`

This file records a rejected review candidate so future reviewers do not reopen
the same issue without new evidence.

## R-001: Rejected Candidate - Qwen Confidence Ranking Is Not A Contract Violation

Original candidate severity: High.

Final status: rejected by internal review plus Claude one-shot review.

Affected files reviewed:

- `modules/shared/qwen_bridge.py`
- `modules/shared/test_bridge_contracts.py`
- `modules/rag/qwen/qwen_visual_cues.py`
- `modules/rag/evidence/concept_cards.py`
- `modules/rag/operations/candidate_sidecars.py`
- `modules/rag/operations/candidate_card_terms.py`
- `modules/rag/tests/test_qwen_visual_cues.py`
- `modules/rag/tests/test_qwen_visual_cue_sidecars.py`

### Why The Candidate Looked Suspicious

The Qwen bridge contract classifies confidence as provenance-only:

- `shared/qwen_bridge.py:55-63` defines `BridgeFieldRole.CONFIDENCE`.
- `shared/qwen_bridge.py:66-74` maps `BridgeFieldRole.CONFIDENCE` to
  `BridgeFieldUsage.PROVENANCE_ONLY`.

The RAG Qwen adapter does use Qwen confidence as visual cue confidence:

- `rag/qwen/qwen_visual_cues.py:21-28` chooses `result.confidence` when present,
  otherwise `_DEFAULT_QWEN_CUE_CONFIDENCE`.
- `rag/qwen/qwen_visual_cues.py:31-35` caps weak cues to
  `_WEAK_QWEN_CUE_CONFIDENCE`.

Concept-card ranking uses `VisualCue.confidence`:

- `rag/evidence/concept_cards.py` ranks cards by cue confidence before retrieval
  score.
- `rag/operations/candidate_sidecars.py` also sorts deduplication candidates by
  cue confidence.

At first glance, that looked like untrusted Qwen confidence was violating a
provenance-only contract.

### Why It Was Rejected

The source-level contract is narrower than the initial suspicion. It forbids Qwen
confidence from becoming a retrieval query driver; it does not forbid ranking or
accounting usage.

Evidence:

- `shared/test_bridge_contracts.py:74-75` explicitly describes Qwen observation
  metadata as "retained for accounting and ranking".
- `shared/test_bridge_contracts.py:84-88` defines the assertion as: none of these
  metadata fields may become retrieval query drivers.
- `shared/qwen_bridge.py:97-107` exposes only `selected_terms` and
  `extracted_descriptors` through `query_driving_fields()` and
  `to_rag_query_input()`.
- `rag/qwen/qwen_visual_cues.py:17-36` builds cues only from
  `selected_terms` and `extracted_descriptors`; raw `reason` prose is not used as
  cue text.

Therefore, the current use of Qwen confidence for cue/ranking strength is
consistent with the tests and contract as written. It is not a confirmed bug.

### Residual Design Risk

Although not a contract violation, this remains a design consideration:

- Qwen self-reported confidence is model output, not an independently calibrated
  measurement.
- Retrieval cues use a deterministic confidence scale in
  `candidate_card_terms.py`, while Qwen cues may use model-provided confidence or
  the default/capped value in `qwen_visual_cues.py`.
- Mixed cue sources can therefore compete in ranking with different confidence
  semantics.

This is acceptable if the project intentionally treats Qwen confidence as a
ranking/accounting hint. If stricter trust boundaries are desired later, the
design could normalize Qwen confidence to fixed deterministic buckets or ignore
model-provided confidence for ranking.

### What Would Reopen This Finding

Reopen only if new code causes any of the following:

- `confidence`, `reason`, `qwen_observation_id`, or `input_view_hashes` are added
  to retrieval query construction.
- Raw Qwen `reason` or observation prose is concatenated into generated prompts
  without the existing allowlisted term adapter.
- The shared contract is changed so `PROVENANCE_ONLY` means no behavioral effect
  at all, rather than no query-driving effect.
- Tests remove or contradict the current "accounting and ranking" allowance.

### Suggested Guard Tests For Future Changes

Maintain or extend tests that prove:

- Qwen `reason` text containing prompt injection phrases never appears in cue
  reasons, descriptor terms, retrieval queries, or generated prompts.
- `QwenBridgeResult.to_rag_query_input()` continues to expose only selected terms
  and extracted descriptors.
- Confidence can affect ranking only if that remains an explicit, documented
  contract choice.

### Verification After Future RAG/Qwen Bridge Changes

Run:

```bash
uv run python -m pytest modules/shared/test_bridge_contracts.py modules/rag/tests/test_qwen_visual_cues.py modules/rag/tests/test_qwen_visual_cue_sidecars.py -q
uv run ruff check modules/shared modules/rag
uv run basedpyright modules/shared modules/rag
```

### Final Disposition

No code change is recommended for this rejected candidate. Treat it as a note for
future reviewers and a reminder to preserve the current trust boundary: Qwen
structured terms may drive query/cue construction; Qwen metadata may support
accounting/ranking only under the documented contract.
