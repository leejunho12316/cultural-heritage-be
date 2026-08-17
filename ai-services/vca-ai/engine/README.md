# VCA v2 Pipeline

Visual Condition Assessment(VCA) v2는 유물 이미지 입력을 받아 전처리, 거친 마스크 생성, 시각 단서/RAG, 프롬프트 생성, 마스크 정제, 이상 그룹핑, 정적 리포트 생성을 순서대로 실행하는 로컬 파이프라인입니다.

## 표준 실행 방법

전체 파이프라인은 orchestration startup CLI를 통해 실행합니다.

```bash
uv run python -m modules.orchestration.startup <project_name> <input_image_folder> \
  [--dry-run] \
  [--device auto|cuda|mps|cpu] \
  [--model-cache-root PATH] \
  [--max-images N]
```

도움말:

```bash
uv run python -m modules.orchestration.startup --help
```

저장 방식은 파일시스템 전용입니다. 스테이지별 receipt, 중간 페이로드, 로컬 아티팩트는 모두 파일시스템에 기록됩니다.

Spring 인메모리 VCA MVP는 기본값이 `SIGNED_PUT`입니다. signed upload verifier 없이 로컬 개발을 할 때는 `VCA_LOCAL_DIRECT_COMPLETE_ENABLED=true`를 명시적으로 설정해야 합니다. `DIRECT_COMPLETE`는 로컬/테스트 전용이며 프로덕션 업로드 경로로 쓰면 안 됩니다.

FE용 VCA API 초안 문서는 [`docs/vca-api-contract.md`](docs/vca-api-contract.md)에 있습니다.

## 스테이지 순서

startup runner는 다음 순서로 스테이지를 실행합니다.

1. `preprocessing`
2. `rough_masking`
3. `visual_cue_generation`
4. `rag`
5. `prompt_generating`
6. `mask_refining`
7. `anomaly_grouping`
8. `report_generating`

각 스테이지는 스테이지별 요청이 더 좁은 출력 경로를 지정하지 않는 한 `output/<stage>/<project_name>` 아래에 결과를 씁니다. startup receipt는 `output/result/<project_name>/receipts/startup.json`에 기록됩니다.

## 스테이지 요약

| 스테이지 | 입력 | 주요 출력 |
|---|---|---|
| `preprocessing` | 원본 이미지 폴더 | `output/preprocessing/<project_name>` 아래의 입력/실제 전처리 매니페스트와 detector/SAM2 에셋 루트 |
| `rough_masking` | 전처리 매니페스트와 객체 에셋 | `output/rough_masking/<project_name>` 아래의 거친 마스크 후보 에셋과 레코드 |
| `visual_cue_generation` | 전처리 및 rough-mask 아티팩트 | RAG가 쓰는 선택적 Qwen 브릿지 시각 단서 아티팩트 |
| `rag` | rough 레코드, 로컬 코퍼스, 선택적 시각 단서 | `output/rag/<project_name>` 아래의 쿼리/근거 JSONL 파일, 시각 개념 카드, RAG 매니페스트 |
| `prompt_generating` | RAG 개념 카드/근거 | `output/prompt_generating/<project_name>` 아래의 `rag_refinement_prompt_variants.jsonl`과 프롬프트 매니페스트 |
| `mask_refining` | 프롬프트 변형, 거친 마스크, 전처리 에셋, 모델 캐시 | `output/mask_refining/<project_name>` 아래의 `refined_records.jsonl`, `skips.jsonl`, 매니페스트, 그룹별 정제 레코드 |
| `anomaly_grouping` | 정제된 마스크 레코드와 RAG 근거 | `anomaly_grouping_result.json`과 `report_trace_source.json` |
| `report_generating` | anomaly trace source | `output/report_generating/<project_name>` 아래의 trace 리포트, 최종 리포트, 메타데이터, 검증 receipt |

## 독립 실행 모듈 도움말

아래 모듈은 CLI로 직접 실행 가능하며 `--help`를 지원합니다.

```bash
uv run python -m modules.orchestration.startup --help
uv run python -m modules.preprocessing.pipeline --help
uv run python -m modules.mask_refining.refinement_cli --help
uv run python -m modules.anomaly_grouping.runner --help
uv run python -m modules.report_generating.runner --help
uv run python -m modules.report_generating.browser_qa --help
```

`modules/*/startup_runner.py` 파일은 orchestration adapter(orchestration 어댑터) 모듈입니다. `modules.orchestration.startup`이 프로세스 내부에서 호출하는 용도이며, 독립 실행형 커맨드라인 러너가 아닙니다.

## 모델 캐시와 디바이스

공유 대형 모델 메타데이터는 `models/inventory/model_inventory.json` 아래에 있습니다. 모델 가중치와 스냅샷은 로컬에만 두며 커밋하지 않습니다. 기본 모델 캐시 루트를 쓸 때, 로컬 경로 오버라이드는 다음 순서로 해석됩니다.

1. `VCA_MODEL_QWEN2_5_VL_VISUAL_PATH`, `VCA_MODEL_RAG_TEXT_EMBEDDING_PATH` 같은 프로세스 환경 변수
2. 프로젝트 루트의 `.models` 항목
3. `models/inventory/model_inventory.json`의 `local_dir` 값

startup 시점에는 `--device auto`를 쓸 수 있습니다. 실제 실행 시 CUDA, Apple MPS, CPU 순서로 시도합니다. GPU 가속을 쓸 수 없는 로컬 실험을 위해 명시적으로 `--device cpu`도 지원합니다. 전처리가 실제 실행 중 구체적인 디바이스를 확정하면, 이후 스테이지 요청들은 그 디바이스를 그대로 물려받습니다. dry run은 모델 로딩을 피하고 연결 상태 검증을 위해 파일시스템 아티팩트/receipt만 사용합니다.

파이프라인이 Docker `vca-ai`를 통해 실행될 때는 Spring이 `VCA_MODEL_CACHE_ROOT`를 `--model-cache-root`로 전달합니다. 로컬 compose 기본값은 named volume이 뒷받침하는 `/opt/vca-models/models`입니다. `vca-ai`는 시작 시 `models/inventory/model_inventory.json`을 그 위치로 복사합니다. `VCA_BOOTSTRAP_MODELS=true`이면 컨테이너 기동 시 요청을 받기 전에 Hugging Face 스냅샷도 그 캐시로 다운로드합니다. 그렇지 않으면 dry run과 이미 캐시된 실제 실행만 정상 동작한다고 가정합니다.

RAG 스테이지는 로컬/오프라인 벡터 검색을 위해 `rag.text_embedding` 인벤토리 항목을 사용합니다. 기본으로 기대하는 스냅샷은 revision `fd1525a9fd15316a2d503bf26ab031a61d056e98`의 `intfloat/multilingual-e5-small`입니다.

## 개발 시 점검 항목

로컬에서 흔히 하는 점검:

```bash
uv run ruff check modules
uv run basedpyright modules
uv run pytest modules -q
git diff --check
```

이 점검들은 모델 가중치를 내려받거나, GPU 디바이스를 탐지하거나, Qwen을 실행하거나, 원본 문서 코퍼스를 건드리면 안 됩니다.
