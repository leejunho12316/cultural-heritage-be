# 육안조사 AI 모듈 - 도커 이미지 빌드/실행

BE가 `app/pottery_api.py`를 직접 파이썬 환경 세팅 없이 컨테이너로 띄울 수
있도록 도커 이미지로 감쌌습니다. `docs/API_SPEC.md`의 `POST /inspect` /
`GET /health`는 이 컨테이너 안에서 동일하게 동작합니다.

> **주의**: 이 Dockerfile은 코드 검증 환경(네트워크가 막힌 샌드박스)에서
> 실제로 빌드/실행해보지 못했습니다. 문법/의존성 목록은 확인했지만,
> 처음 빌드할 때 이미지 크기·다운로드 시간·환경별 차이로 예상 못한 오류가
> 날 수 있습니다. 실제 빌드는 인터넷 되는 로컬 환경에서 한 번 해보고,
> 에러 나면 공유해 주세요.

## 빌드

저장소 루트에서:

```bash
docker build -t pottery-inspection-api .
```

torch/transformers/SAM2가 들어가서 이미지가 큽니다(수 GB, 첫 빌드는
꽤 오래 걸릴 수 있음). `app/requirements.txt`만 먼저 복사해서 설치하는
구조라, 코드만 바뀐 재빌드는 훨씬 빠릅니다.

## 실행

```bash
docker run -p 8001:8001 \
  -e OPENAI_API_KEY=sk-... \
  -v "$(pwd)/ai_hub:/srv/ai_hub" \
  -v pottery_hf_cache:/root/.cache/huggingface \
  pottery-inspection-api
```

| 옵션 | 설명 |
|---|---|
| `-e OPENAI_API_KEY=...` | 문양 분석 VLM 호출에 필요. 이미지에 키를 절대 넣지 않으므로 실행할 때마다 넘겨줘야 합니다. |
| `-v .../ai_hub:/srv/ai_hub` | 시대(CNN) 모델(용량 커서 이미지에 미포함, git에도 없음). 컨테이너 안 `ERA_MODEL_DIR`이 `/srv/ai_hub/pottery_multitask_model_v2`를 보도록 코드가 상대경로로 짜여 있어, 이 마운트만 맞추면 자동으로 인식됩니다. **마운트 안 해도 컨테이너는 정상 실행되고, 시대 판정만 경고와 함께 빠집니다.** |
| `-v pottery_hf_cache:/root/.cache/huggingface` | Grounding DINO / SAM2 가중치는 최초 요청 시 Hugging Face Hub에서 자동 다운로드됩니다(컨테이너에 인터넷 필요). 이 볼륨을 안 걸면 컨테이너를 새로 띄울 때마다 다시 다운로드합니다 - 걸어두는 걸 강력 권장. |

## 확인

```bash
curl http://localhost:8001/health
# {"status": "ok", "module_version": "pottery-inspection-v11"}

curl -X POST "http://localhost:8001/inspect" -F "image=@../sample_images/sample1.jpg"
```

첫 요청은 모델 다운로드 때문에 오래 걸릴 수 있습니다(이후 캐시되면
빨라짐). 응답 필드 설명은 `docs/API_SPEC.md` 참고.

## 알려진 제약

- 이미지 안에는 `app/`(코드+RF 모델 joblib)만 들어갑니다. `ai_hub/`(시대
  CNN)는 볼륨으로, Grounding DINO/SAM2 가중치는 Hugging Face Hub에서
  런타임에 받습니다 - 완전 오프라인 이미지는 아닙니다.
- GPU 없이도(CPU만) 동작하도록 코드가 짜여 있지만(`torch.cuda.is_available()`
  로 자동 분기), CPU에서는 요청 하나 처리하는 데 시간이 꽤 걸릴 수
  있습니다. 실사용 전에 컨테이너 환경에서 소요 시간을 한 번 재보는 걸
  권장합니다.
