# 공유 모델 캐시

이 디렉터리는 파이프라인 모듈들이 공유하는 대형 로컬 모델의 워크스페이스 로컬 캐시입니다.

가중치와 다운로드된 스냅샷은 의도적으로 git이 무시합니다. 모든 모듈이 모델 바이너리를 커밋하지 않고도 모델 ID/revision/로컬 경로에 대해 합의할 수 있도록, `models/inventory/` 아래의 작은 인벤토리 파일만 유지합니다.

예상 디렉터리 구조:

```text
models/
  hf/
    <repo-id-as-path>/
  inventory/
    model_inventory.json
```

이 워크스페이스 변형에서는 preprocessing이 detector/SAM2 실행을 소유합니다. RAG는 벡터 검색용 로컬 텍스트 임베딩 모델을 소유합니다. 다른 모듈들은 자체적으로 모델 사본을 내려받지 말고 인벤토리와 생성된 아티팩트를 그대로 사용해야 합니다.

## 로컬 경로 오버라이드

하나 이상의 로컬 모델 디렉터리를 오버라이드하려면 [`.models.example`](../.models.example)을 프로젝트 루트의 `.models` 파일로 복사하세요. `.models`는 git이 무시하며, 한 줄에 하나씩 비어있지 않은 `KEY=VALUE` 항목을 씁니다. 빈 줄과 `#`으로 시작하는 줄은 무시됩니다. 형식이 잘못된 줄이나 빈 경로는 다른 위치로 폴백하지 않고 검증에서 바로 실패합니다.

상대 경로는 워크스페이스 루트(활성 모델 캐시 루트의 부모 디렉터리로 정의됨) 기준으로 해석됩니다. 기본 `models` 캐시 루트를 쓰는 경우 그 워크스페이스 루트는 저장소 루트입니다.

각 인벤토리 모델 키에 대해 해석 순서는 다음과 같습니다.

1. `VCA_MODEL_QWEN2_5_VL_VISUAL_PATH`, `VCA_MODEL_RAG_TEXT_EMBEDDING_PATH` 같은 프로세스 환경 변수
2. 프로젝트 루트 `.models` 파일의 일치하는 항목
3. `models/inventory/model_inventory.json`의 `local_dir`

이는 이미 로컬에 있는 디렉터리를 선택하는 용도일 뿐입니다. 모델 다운로드나 Hugging Face로의 원격 폴백을 활성화하지는 않습니다.
