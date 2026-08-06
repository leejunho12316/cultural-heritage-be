# VCA v2 Pipeline

Visual Condition Assessment(VCA) v2는 유물 이미지 입력을 받아 전처리, 거친 마스크 생성, 시각 단서/RAG, 프롬프트 생성, 마스크 정제, 이상 그룹핑, 정적 리포트 생성을 순서대로 실행하는 로컬 파이프라인입니다.

## Canonical Startup

전체 파이프라인은 orchestration startup CLI를 통해 실행합니다.

```bash
uv run python -m modules.orchestration.startup <project_name> <input_image_folder> \
  [--dry-run] \
  [--device auto|cuda|mps|cpu] \
  [--model-cache-root PATH] \
  [--max-images N] \
  [--storage-mode filesystem|rdb] \
  [--artifact-id UUID] \
  [--db-url URL]
```

Help:

```bash
uv run python -m modules.orchestration.startup --help
```

Default storage is filesystem-only. `--storage-mode rdb` enables an optional PostgreSQL dual-write and requires `--artifact-id` plus `--db-url` or `VCA_DATABASE_URL`. The current startup writer persists one `artifact + assessment_run` snapshot only. `uploaded_image`, `assessment_report`, and `report_pdf_job` are target ERD tables for the Spring VCA/API persistence layer, and the current startup writer does not populate image, report, or PDF rows. Final reports are JSONB-backed through `assessment_report.report_json` rather than normalized `report_*` rows. Stage receipts, intermediate payloads, and local artifacts remain filesystem-only; the startup snapshot stores only run progress plus the input folder, output root, and image count configuration.

The Spring in-memory VCA MVP defaults to `SIGNED_PUT`. Local development without a signed upload verifier must explicitly set `VCA_LOCAL_DIRECT_COMPLETE_ENABLED=true`; `DIRECT_COMPLETE` is local/test-only and should not be used as the production upload path.

The FE-facing VCA API draft is documented in [`docs/vca-api-contract.md`](docs/vca-api-contract.md).

## Stage Order

The startup runner executes stages in this order:

1. `preprocessing`
2. `rough_masking`
3. `visual_cue_generation`
4. `rag`
5. `prompt_generating`
6. `mask_refining`
7. `anomaly_grouping`
8. `report_generating`

Each stage writes under `output/<stage>/<project_name>` unless the stage-specific request supplies a narrower output path. The startup receipt is written to `output/result/<project_name>/receipts/startup.json`.

## Stage Summary

| Stage | Input | Main output |
|---|---|---|
| `preprocessing` | Raw image folder | Input and real preprocessing manifests plus detector/SAM2 asset roots under `output/preprocessing/<project_name>` |
| `rough_masking` | Preprocessing manifest and object assets | Rough mask candidate assets and records under `output/rough_masking/<project_name>` |
| `visual_cue_generation` | Preprocessing and rough-mask artifacts | Optional Qwen bridge visual cue artifacts used by RAG |
| `rag` | Rough records, local corpus, optional visual cues | Query/evidence JSONL files, visual concept cards, and RAG manifest under `output/rag/<project_name>` |
| `prompt_generating` | RAG concept cards/evidence | `rag_refinement_prompt_variants.jsonl` and prompt manifest under `output/prompt_generating/<project_name>` |
| `mask_refining` | Prompt variants, rough masks, preprocessing assets, model cache | `refined_records.jsonl`, `skips.jsonl`, manifest, and per-group refined records under `output/mask_refining/<project_name>` |
| `anomaly_grouping` | Refined mask records and RAG evidence | `anomaly_grouping_result.json` and `report_trace_source.json` |
| `report_generating` | Anomaly trace source | Trace report, final report, metadata, and verification receipts under `output/report_generating/<project_name>` |

## Standalone Runner Help

These modules are direct CLI entrypoints and support `--help`:

```bash
uv run python -m modules.orchestration.startup --help
uv run python -m modules.preprocessing.pipeline --help
uv run python -m modules.mask_refining.refinement_cli --help
uv run python -m modules.anomaly_grouping.runner --help
uv run python -m modules.report_generating.runner --help
uv run python -m modules.report_generating.browser_qa --help
```

`modules/*/startup_runner.py` files are orchestration adapter modules. They are called in-process by `modules.orchestration.startup` and are not standalone command-line runners.

## Model Cache And Devices

Shared heavyweight model metadata lives under `models/inventory/model_inventory.json`. Model weights and snapshots remain local and are not committed. With the default model cache root, local path overrides are resolved from:

1. Process environment variables such as `VCA_MODEL_QWEN2_5_VL_VISUAL_PATH`.
2. Project-root `.models` entries.
3. `local_dir` values in `models/inventory/model_inventory.json`.

`--device auto` is allowed at startup. Real execution tries CUDA, then Apple MPS, then CPU. Explicit `--device cpu` is supported for local experiments when GPU acceleration is unavailable. When preprocessing resolves a concrete device during real execution, downstream stage requests inherit that device. Dry runs avoid model loading and use filesystem artifacts/receipts to verify wiring.

When the pipeline runs through Docker `vca-ai`, Spring passes
`VCA_MODEL_CACHE_ROOT` to `--model-cache-root`. The local compose default is
`/opt/vca-models/models`, backed by a named volume. `vca-ai` copies
`models/inventory/model_inventory.json` there on startup. If
`VCA_BOOTSTRAP_MODELS=true`, container startup also downloads Hugging Face
snapshots into that cache before serving requests; otherwise only dry-run and
already-cached real runs are expected to work.

## Development Checks

Common local checks:

```bash
uv run ruff check modules
uv run basedpyright modules
uv run pytest modules -q
git diff --check
```

These checks should not download model weights, probe GPU devices, run Qwen, or touch the source document corpus.
