# Rough Masking Findings

Review date: 2026-08-03
Review scope: high-precision read-only review of the current `vca_v2` workspace.
Review status: advisory record. No additional fixes are approved by this file.

## Verification Snapshot

Current workspace checks recorded during the documentation/review pass:

- `uv run python -m pytest modules -q` -> `452 passed`
- `uv run ruff check modules/mask_refining modules/visual_cue_generation modules/rough_masking modules/rag modules/shared` -> pass
- `uv run basedpyright modules/mask_refining modules/visual_cue_generation modules/rough_masking modules/rag modules/shared` -> `0 errors`

This file records the rough-mask quality finding after a follow-up read-only
review with Claude. The earlier note that `assess_mask_quality()` still crashed
on direct empty-mask calls was stale; the current source already contains the
guard and regression test.

## F-003: Empty-Mask Quality Scoring Crash Is Resolved

Original severity: Low.

Current status: resolved in the current workspace snapshot.

Affected files reviewed:

- `modules/rough_masking/artifacts/quality.py`
- `modules/rough_masking/local_model/segmentation.py`
- `modules/rough_masking/tests/artifacts/test_quality.py`

### Original Finding

The original review identified that a direct call to `assess_mask_quality()` with
an all-false anomaly mask could crash before returning a rejected quality
decision. The failure mode was:

- `area_px` would be `0`.
- The scorer would call `_mask_bounds(mask)`.
- `_mask_bounds()` would call `min()` and `max()` on empty arrays returned by
  `np.nonzero(mask)`.
- Later calculations such as boundary ratio and component ratio also assumed a
  non-zero area.

That concern was always lower severity for production because
`segment_detections()` already filtered empty masks before calling the quality
scorer.

### Current Confirmed State

The current `assess_mask_quality()` implementation handles empty masks directly:

- `quality.py:59` computes `area_px = int(np.count_nonzero(mask))`.
- `quality.py:60-72` checks `if area_px == 0` and returns a `_QualityDecision`
  with `accepted=False` and `reject_reason="empty_mask"`.
- The returned `_MaskQuality` telemetry is stable and zero-valued:
  `score=0.0`, `area_ratio=0.0`, `bbox_fill_ratio=0.0`,
  `boundary_pixel_ratio=0.0`, `perimeter_coverage_ratio=0.0`,
  `border_touch_count=0`, `component_count=0`, and
  `largest_component_ratio=0.0`.
- `_mask_bounds(mask)` is now reached only after the zero-area guard.

The production segmentation path still has its own guard:

- `local_model_segmentation.py:124-125` converts the SAM2 mask and intersects it
  with `object_foreground`.
- `local_model_segmentation.py:126-127` runs `if not np.any(mask): continue`.
- `local_model_segmentation.py:148` calls `assess_mask_quality(mask,
  object_foreground)` only after that non-empty guard.

So both the direct scorer API and the production caller now avoid the stale crash
condition.

### Applied Regression Coverage

`tests/artifacts/test_quality.py:8-26` contains
`test_empty_mask_is_rejected_without_crashing`, which verifies the direct scorer
behavior:

- Builds an all-false anomaly mask.
- Calls `assess_mask_quality(mask, object_foreground)` directly, without the
  production segmentation guard.
- Asserts `decision.accepted is False`.
- Asserts `decision.reject_reason == "empty_mask"`.
- Asserts all relevant telemetry values are zero/stable.

Existing nearby tests also continue to cover the non-empty quality rules:

- ROI frame pixels do not count as object boundary.
- Interior object outlines count as boundary-only masks.
- Small one-edge localized masks can remain accepted.
- Partial boundary masks record perimeter coverage.

### Remaining Advisory Notes

No active correctness defect remains for the empty-mask case in the current
snapshot.

Future changes should preserve both layers of protection:

- Keep `assess_mask_quality()` robust for direct empty-mask calls.
- Keep `segment_detections()` free to skip empty SAM2/object-intersection masks
  without materializing noisy rejected outputs, unless the product contract
  changes to require explicit empty-mask rejected rows.

The review did not deeply evaluate unrelated edge cases such as mismatched mask
and object-foreground shapes. Do not infer shape-validation coverage from this
empty-mask finding.

### What Would Reopen This Finding

Reopen F-003 if any future change causes one of the following:

- `assess_mask_quality()` calls `_mask_bounds()` before checking `area_px == 0`.
- The direct empty-mask regression test is removed or weakened.
- Empty masks again raise instead of returning a stable rejected decision.
- A new caller bypasses both the scorer guard and production guard while relying
  on empty-mask inputs to be safe.

### Suggested Regression Checks For Future Rough-Masking Work

Run:

```bash
uv run python -m pytest modules/rough_masking/tests/artifacts/test_quality.py modules/rough_masking/tests/local_model/test_segmentation.py -q
uv run ruff check modules/rough_masking
uv run basedpyright modules/rough_masking
```

### Risk If Left As-Is

Current risk is low. The stale unresolved-finding text has been replaced with the
current resolved state, and the implementation has both direct scorer coverage
and production-call-site protection for empty masks.
