# Mask Refining Findings

Review date: 2026-08-03
Review scope: high-precision read-only review of the current `vca_v2` workspace.
Review status: advisory. No fixes are approved by this file.

## Verification Snapshot

The original review captured quality-gate failures in the new prompt-driven mask
refinement execution path. During the later documentation pass, the current
workspace no longer reproduced those failures:

- `uv run python -m pytest modules -q` -> `452 passed`
- `uv run ruff check modules/mask_refining modules/visual_cue_generation modules/rough_masking modules/rag modules/shared` -> pass
- `uv run basedpyright modules/mask_refining modules/visual_cue_generation modules/rough_masking modules/rag modules/shared` -> `0 errors`

This file keeps the historical finding for auditability, but marks it as resolved
in the current tree.

After this snapshot, the refinement path also received targeted hardening for
JPEG ROI dimension parsing, rounded preprocessing crop transforms, lightweight CLI
imports, symlink output-root rejection, and `--max-groups` help text. The latest
focused mask-refining verification reported `78 passed`, clean ruff/basedpyright
gates, and a real `--max-groups 1` smoke with `executed_groups=1`,
`failed_groups=0`, and `skips=0`.

## F-002: Refinement Execution Quality Gates Were Previously Failing

Severity at time of review: Low-Medium.

Current status: resolved in the current workspace snapshot.

Affected files reviewed:

- `modules/mask_refining/execution.py`
- `modules/mask_refining/execution_models.py`
- `modules/mask_refining/refinement_cli.py`
- `modules/mask_refining/asset_join.py`
- `modules/mask_refining/prompt_variants.py`
- `modules/mask_refining/test_refinement_execution.py`

### Original Observation

The high-precision review initially observed that tests passed but quality gates
did not. The narrow gate failures were concentrated around the new refinement
execution/CLI/test files. Categories included:

- import ordering and formatting issues
- strict typing issues in CLI argument parsing
- style violations around exception-message construction
- magic constants and positional boolean calls
- test typing issues
- an initially suspected protocol parameter-name mismatch in
  `RefinementRunnerFactory`

Claude's independent review rejected the protocol parameter-name mismatch as a
real current defect after checking the latest code. A later source re-read
confirmed the current protocol and implementation agree:

- `execution_models.py:99-103` declares
  `def __call__(self, details: RunnerFactoryInput) -> DetectorRunner`.
- `execution.py:143-153` defines
  `default_runner_factory(details: RunnerFactoryInput) -> DetectorRunner`.
- `execution.py:284` invokes the factory positionally with a
  `RunnerFactoryInput`.

Therefore, do not spend future fix time on the rejected `input` versus `details`
protocol issue unless it reappears in tool output.

### Current Confirmed State

The current implementation has moved to typed CLI parsing:

- `refinement_cli.py:19-39` defines `_CliNamespace` with typed attributes and
  initialized defaults.
- `refinement_cli.py:55-68` parses into that namespace before constructing
  `RefinementRunRequest`.

The current execution contract includes an optional bounded-run control:

- `execution_models.py:74-85` includes `max_groups: int | None = None`.
- `execution.py:255-256` stops processing when `max_groups` is reached.
- `execution.py:215-226` writes `max_groups` into the manifest inputs.

The current gate result is clean:

- ruff passed across reviewed modules.
- basedpyright reported `0 errors` across reviewed modules.
- the full module test suite passed with `452 passed`.

### Remaining Advisory Notes

No active correctness defect remains from this finding in the current snapshot.
Keep the following guardrails for future work:

- Any change to `RefinementRunnerFactory` should keep protocol parameter names
  aligned with concrete factories because strict structural typing can consider
  callback parameter names.
- Any change to `refinement_cli.py` should rerun basedpyright because argparse
  often reintroduces `Any` leaks.
- Any change to `run_refinement()` should preserve the current behavior of
  recording per-group failures without aborting unrelated groups.

### Suggested Regression Checks For Future Refinement Work

Run:

```bash
uv run python -m pytest modules/mask_refining/test_refinement_execution.py modules/mask_refining/test_qwen_contracts.py -q
uv run ruff check modules/mask_refining
uv run basedpyright modules/mask_refining
```

### Risk If Left As-Is

Current risk is low because the gate failures are no longer present. The main
risk is regression: this path sits at the boundary between prompt artifacts,
preprocessing assets, rough-mask candidates, local detector/SAM2 execution, and
CLI invocation. Small type or contract drift can make failures difficult to
diagnose unless the gates above remain required.
