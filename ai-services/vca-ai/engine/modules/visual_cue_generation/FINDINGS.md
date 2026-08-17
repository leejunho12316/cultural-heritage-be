# Visual Cue Generation Findings

Review date: 2026-08-03
Review scope: high-precision read-only review of the current `vca_v2` workspace.
Review status: advisory. No fixes are approved by this file.

## Verification Snapshot

The review initially found a candidate-local versus batch-fatal failure-handling
asymmetry in the Qwen bridge generation path. After the documentation pass, the
workspace was rechecked and the general quality gates were green:

- `uv run python -m pytest modules -q` -> `452 passed`
- `uv run ruff check modules/mask_refining modules/visual_cue_generation modules/rough_masking modules/rag modules/shared` -> pass
- `uv run basedpyright modules/mask_refining modules/visual_cue_generation modules/rough_masking modules/rag modules/shared` -> `0 errors`

These gates do not resolve the design question below; they only show the current
implementation is test/type/lint clean.

## F-001: Source Manifest Validation Can Abort the Whole Qwen Bridge Batch

Severity: Medium if non-null per-candidate Qwen accounting is required; Low if
malformed preprocessing manifests are intentionally batch-fatal.

Status: resolved in the current workspace snapshot by choosing the batch-fatal
input-manifest integrity contract.

Affected files:

- `modules/visual_cue_generation/generation.py`
- `modules/visual_cue_generation/source_manifest.py`
- `modules/visual_cue_generation/test_generation.py`

### What Happens

`generate_qwen_bridge_results()` loads all source assets before iterating rough
mask candidates:

- `generation.py:33-36` calls `source_assets_by_image(inputs.input_manifest_path,
  inputs.asset_root)`.
- Only after that returns does `generation.py:37-78` iterate rough candidates and
  append `QwenBridgeCandidateArtifact` rows.

Inside the loop, some candidate-local failures are explicitly materialized as
failed bridge rows:

- `generation.py:40-48` writes a failed row with `failure_code="source_asset_missing"`
  when a rough candidate's `image_id` is absent from the already-loaded source map.
- `generation.py:49-58` writes a failed row with `failure_code="image_decode_failed"`
  when the source image exists in the map but Pillow cannot decode it.

However, `source_assets_by_image()` itself is fail-fast:

- `source_manifest.py:22-31` parses every manifest image entry before returning.
- `source_manifest.py:38-43` raises `ContractValidationError` for non-absolute or
  escaping `run_root_asset_path` values.
- `source_manifest.py:44-45` raises when the source asset file is missing.
- `source_manifest.py:47-50` raises when `file_sha256` does not match the actual
  source bytes.

That means one malformed, missing, unsafe, or hash-mismatched manifest entry can
abort the whole Qwen bridge generation call before any candidate row is written.
This is asymmetric with the loop's non-null failed-row handling for absent image
IDs and image decode failures.

### Why It Matters

The surrounding Qwen/RAG bridge design appears to prefer auditable, non-null
candidate outcomes:

- A successful candidate becomes a `QwenBridgeResult` with `status=SUCCESS`.
- Candidate-local source absence and decode failures become `status=FAILED` rows.
- Downstream RAG accounting can then distinguish successful Qwen evidence from
  explicit Qwen unavailability.

If preprocessing manifest entry corruption is intended to be candidate-local,
the current eager source-map construction weakens that contract: downstream code
receives no bridge artifact for any candidate in the batch. That makes the
failure less granular and can hide how many candidates were unaffected by the bad
manifest entry.

If preprocessing manifest corruption is intended to be a pipeline-fatal input
contract violation, the current behavior is reasonable. In that case the module
should document that distinction explicitly: missing image IDs after manifest
load are candidate-local, while malformed manifest entries are batch-fatal.

### Current Test Coverage

Existing tests cover the candidate-local paths:

- `test_generate_qwen_bridge_results_preserves_failed_rows_for_missing_source`
  covers a valid manifest that simply lacks a rough candidate image ID.
- `test_generate_qwen_bridge_results_preserves_failed_rows_for_decode_errors`
  covers a manifest entry whose source path exists but image decoding fails.

The missing coverage is for manifest entries that fail validation inside
`source_assets_by_image()`:

- unsafe path outside `asset_root`
- source path missing on disk
- `file_sha256` mismatch
- malformed `images` shape or non-object item

### Recommended Decision

The current workspace chose Option B: preprocessing source-manifest integrity
failures are batch-fatal input contract violations, distinct from candidate-local
Qwen unavailability.

Rejected alternative, Option A candidate-local failure contract:

- Change source manifest loading to preserve per-image validation failures in a
  typed result instead of raising immediately for every bad image entry.
- Let `generate_qwen_bridge_results()` emit failed `QwenBridgeResult` rows for
  rough candidates whose source image entry is invalid.
- Keep truly unreadable manifest JSON or top-level schema corruption batch-fatal
  only if that is required.

Chosen contract, Option B batch-fatal input contract:

- Keep current fail-fast behavior.
- Tests now prove unsafe, missing, and hash-mismatched manifest entries abort
  before writing `qwen_bridge_results`.
- `generate_qwen_bridge_results()` documents that source-manifest validation is
  batch-fatal before candidate iteration.

### Applied Regression Coverage

`test_generate_qwen_bridge_results_rejects_invalid_manifest_without_artifact`
now covers:

- `file_sha256` mismatch
- missing `run_root_asset_path` source file
- absolute source path outside `asset_root`

Each case asserts `ContractValidationError` and no `qwen_bridge_results` artifact
directory.

### Verification After Any Future Change

Run:

```bash
uv run python -m pytest modules/visual_cue_generation modules/rag/tests/test_qwen_visual_cue_sidecars.py -q
uv run ruff check modules/visual_cue_generation modules/rag modules/shared
uv run basedpyright modules/visual_cue_generation modules/rag modules/shared
```

### Risk If Left As-Is

Current risk is low under the chosen contract. If the pipeline later requires
candidate-level auditability even for corrupt preprocessing manifest entries,
reopen this finding and implement the rejected candidate-local result contract
explicitly.
